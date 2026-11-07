# shared/test_impact.py
# -*- coding: utf-8 -*-
"""test_impact.py -- which tests a change can affect (FND-0385; plan: impact-based test selection for delivery receipts, owner-accepted 2026-11-07).

A change under shared/ or proyecto*/ used to need the whole suite (about 27 min) because `delivery_receipt.required_scope` is a path rule. This module is the analysis behind a narrower
`impact` scope. EVERYTHING HERE IS PURE: it takes the text of the files (`{repo-relative path with forward slashes: source}`) and returns a decision; git, the receipts file and the
test runner live in scripts/audit/delivery_attest.py. The module is runner-agnostic: a CI that returns can use the same `Decision`.

Principle (owner, 2026-11-07): optimise validation time, never validation assurance. UNCERTAINTY ALWAYS WIDENS: every rule F1..F8 below that cannot establish coverage returns `full`.

    F1 a changed or test file does not parse        F5 the selection is not smaller than half the suite (or not faster)
    F2 changed test infrastructure / the selector   F6 more than MAX_RUNTIME_FILES runtime files changed
    F3 changed hub (its closure reaches > 50%)      F7 no recent passing `full` receipt on the ancestry of HEAD  (freshness_reason)
    F4 a changed code file no test reaches          F8 the selector is switched off (kill switch / mode OFF)

The graph (build_graph): AST imports (absolute and relative, resolved by every dotted suffix of each repo path, the standard library winning), string edges (a test that names a repo file or a dotted
module, e.g. a mock.patch target; runtime code only inside a dynamic-load or subprocess call), launcher edges (.bat/.sh -> the .py they run) and data edges. Dynamic loaders are resolved in three tiers:
a literal, an f-string with a module-like prefix (`f"blocks.{name}"`), the string constants of the same file; a site that resolves to nothing is `unbounded` and is reported, never silently ignored.
`effective_mode` derives SHADOW / ENFORCE from the receipts alone (nothing stored), so a person never flips it and one escape demotes it."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import warnings
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from shared.backlog_autopilot import is_code_path
from shared.delivery_receipt import digest_of, is_test_path

ALGO_VERSION = 1                                    # bump whenever the graph or a rule changes: the evidence and the shadow streak start again (effective_mode)
EXCLUDED_PREFIXES = ("upload/", "out/", ".git/")    # never collected by the full run
SUITE_ROOTS = (("proyecto1", "proyecto1/tests/"), ("proyecto3", "proyecto3/tests/"), ("tests", "tests/"), ("proyecto2", "proyecto2/tests/"))   # run_pg_tests.py's four runs
SUITE_EXCLUDE = frozenset({"proyecto2/tests/discovery/test_historia.py"})                 # needs the network; the full run ignores it
DATA_EXTS = (".json", ".yaml", ".yml", ".sql", ".bat", ".sh")
MAX_RUNTIME_FILES = 20                                                                    # F6
HUB_SHARE = 0.5                                                                           # F3 and F5
INFRA_EXACT = frozenset({"scripts/ops/run_pg_tests.py", "shared/delivery_receipt.py", "shared/test_impact.py", "scripts/audit/delivery_attest.py", "shared/git_export.py",
                         "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "requirements.txt"})
INFRA_PREFIXES = ("shared/testing/", "db/")
_LOADERS = frozenset({"import_module", "__import__", "spec_from_file_location", "run_path", "run_module"})
_SPAWNERS = frozenset({"run", "Popen", "check_output", "check_call", "call", "system", "spawnv", "execv"})
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.\w+)+$")
_FILE_REF = re.compile(r"[\w.\-]+\.(?:py|bat|sh)\b")
_STD = frozenset(sys.stdlib_module_names)
_TOP_DIRS = frozenset({"shared", "proyecto1", "proyecto2", "proyecto3", "scripts", "db"})

# A dynamic loader whose target is not visible in the file is `unbounded` and conservatively depends on every runtime module of its top-level directory (and of any top-level directory it names).
# That is sound but expensive when the loader sits in a module everything imports. An entry here narrows ONE site to the directories it can really load from, with the evidence; each narrowing is
# re-proved by tests/test_test_impact.py::test_narrowed_loaders_are_still_true, so a change that makes it false fails the suite instead of silently under-selecting. Empty tuple = loads no repo module.
NARROW_LOADERS = {
    "shared/env_guard.py": (),                                                              # imports FONDOS_DB_DRIVER_MODULE or "psycopg": a third-party driver, or a module that does not exist (the tests)
    "shared/backlog_writer_proof.py": ("scripts/audit/",),                                  # a registered writer's entry module (NORMAS_BACKLOG.md section 4.9: importable from scripts/audit)
    "proyecto3/src/pit_metrics.py": ("proyecto2/src/calculations/", "proyecto2/src/utils/"),   # load_p2_calc(name, subdir): every call site passes literals
}


# ── data ────────────────────────────────────────────────────────────────────────

@dataclass
class Graph:
    deps: dict = field(default_factory=dict)             # path -> set of paths it depends on
    rdeps: dict = field(default_factory=dict)            # path -> set of paths that depend on it
    py_files: list = field(default_factory=list)
    parse_errors: list = field(default_factory=list)
    unbounded: list = field(default_factory=list)        # (path, line, call) loader sites no tier could bound
    narrowed: list = field(default_factory=list)         # loader files covered by NARROW_LOADERS
    blind_tests: set = field(default_factory=set)        # tests that start a subprocess a tracer cannot see (or build its command dynamically)
    scanners: set = field(default_factory=set)           # tests that walk the repository tree
    unresolved: dict = field(default_factory=dict)       # test path -> top-level import names that resolve to nothing


@dataclass
class Decision:
    scope: str                                           # "impact" | "full"
    reasons: list                                        # why it is full (empty for impact), or notes
    selected: dict                                       # suite name -> sorted test paths relative to the repository root
    selected_count: int
    suite_total: int
    algo_version: int = ALGO_VERSION
    graph_digest: str = ""
    hubs: list = field(default_factory=list)
    unbounded_sites: int = 0
    policy: list = field(default_factory=list)           # `file::test` policy entries that must run in every impact receipt (delivery_receipt.POLICY_TESTS present in the tree)

    def files(self) -> list:
        """Every entry to hand the runner: the selected test files plus the policy entries, sorted, without repeats."""
        return sorted({t for v in self.selected.values() for t in v} | set(self.policy))

    def as_dict(self) -> dict:
        return {"algo": self.algo_version, "scope": self.scope, "reasons": list(self.reasons)[:20], "selected_count": self.selected_count, "suite_total": self.suite_total,
                "graph_digest": self.graph_digest, "hubs": list(self.hubs)[:10], "unbounded_sites": self.unbounded_sites,
                "selected": {k: list(v) for k, v in self.selected.items()}, "policy": list(self.policy)}


# ── classification of paths ─────────────────────────────────────────────────────

def _norm(p: str) -> str:
    return str(p).replace("\\", "/")


def is_excluded(path: str) -> bool:
    return _norm(path).startswith(EXCLUDED_PREFIXES)


def suite_of(path: str) -> Optional[str]:
    """The suite (run_pg_tests.py run) a test file belongs to, None when the full run does not collect it. Pure."""
    p = _norm(path)
    name = p.rsplit("/", 1)[-1]
    if p in SUITE_EXCLUDE or not (name.startswith("test_") and name.endswith(".py")):
        return None
    for suite, prefix in SUITE_ROOTS:
        if p.startswith(prefix):
            return suite
    return None


def is_runtime_code(path: str) -> bool:
    p = _norm(path)
    return p.endswith(".py") and not is_test_path(p) and not is_excluded(p)


def is_infra(path: str) -> bool:
    p = _norm(path)
    return p in INFRA_EXACT or p.startswith(INFRA_PREFIXES) or p.rsplit("/", 1)[-1] == "conftest.py"


# ── the graph ───────────────────────────────────────────────────────────────────

def _dotted_keys(py_files: Iterable[str]) -> dict:
    """Every dotted suffix of every repo path -> the paths it can name (`core.x`, `proyecto1.core.x`, `x`)."""
    keys: dict = {}
    for f in py_files:
        parts = f[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            keys.setdefault(".".join(parts[i:]), set()).add(f)
    return keys


def _resolve(name: str, keys: dict) -> set:
    """Repo paths a dotted import name denotes: the longest dotted prefix of it that is a key. The standard library wins. Pure."""
    if not name or name.split(".")[0] in _STD:
        return set()
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        hit = keys.get(".".join(parts[:i]))
        if hit:
            return set(hit)
    return set()


def _ancestor_inits(path: str, files: set) -> set:
    out, parts = set(), path.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        init = "/".join(parts[:i]) + "/__init__.py"
        if init in files and init != path:
            out.add(init)
    return out


def _call_name(call: ast.Call) -> tuple:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr, ast.unparse(fn.value)
    return (fn.id if isinstance(fn, ast.Name) else ""), ""


def _fstring_prefix(node: ast.AST) -> str:
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant) and isinstance(node.values[0].value, str):
        return node.values[0].value
    return ""


def _strings(tree: ast.AST) -> Iterable[str]:
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and 0 < len(n.value) < 300:
            yield n.value


def _environ_names(tree: ast.AST) -> set:
    """Identifiers whose value is derived from the parent environment: a name or an attribute assigned from an expression that mentions `environ` (`env = dict(os.environ, ...)`, `self.env = {**os.environ}`),
    and the name of a function or method whose body mentions it (`def env(self): return {... os.environ ...}`). So `env=env`, `env=self.env` and `env=tree.env(**x)` are recognised as inheriting PYTHONPATH."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and "environ" in ast.unparse(n.value):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    out.add(t.id)
                elif isinstance(t, ast.Attribute):
                    out.add(t.attr)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and "environ" in ast.unparse(n):
            out.add(n.name)
    return out


def _derived_from_environ(value: ast.AST, derived: set) -> bool:
    if "environ" in ast.unparse(value):
        return True
    for x in ast.walk(value):
        if (isinstance(x, ast.Name) and x.id in derived) or (isinstance(x, ast.Attribute) and x.attr in derived):
            return True
    return False


def _is_blind_call(call: ast.Call, derived: set) -> bool:
    """A subprocess the import tracer cannot follow: a command built dynamically, a python started with -I / -S, or an env= that does not come from os.environ (PYTHONPATH and sitecustomize are lost)."""
    name, recv = _call_name(call)
    if name not in _SPAWNERS or recv not in ("subprocess", "os"):
        return False
    first = call.args[0] if call.args else None
    if first is not None and (any(isinstance(x, ast.JoinedStr) for x in ast.walk(first)) or any(k.arg == "shell" and ast.unparse(k.value) != "False" for k in call.keywords)):
        return True
    if first is not None and any(isinstance(x, ast.Constant) and x.value in ("-I", "-S") for x in ast.walk(first)):
        return True
    for k in call.keywords:
        if k.arg == "env" and not _derived_from_environ(k.value, derived):
            return True
    return False


_REPO_ROOT_NAMES = frozenset({"ROOT", "REPO", "_ROOT", "REPO_ROOT", "_REPO", "PROJECT_ROOT"})
_SOURCE_ROOTS = ("shared", "scripts", "proyecto1", "proyecto2", "proyecto3", "db")


def _leftmost_name(node: ast.AST) -> str:
    while True:
        if isinstance(node, ast.Name):
            return node.id
        node = getattr(node, "value", None) or getattr(node, "func", None) or getattr(node, "left", None)
        if node is None:
            return ""


def _file_rooted_names(tree: ast.AST) -> set:
    """Names that lead back to the repository: assigned (transitively) from an expression that mentions `__file__` (`_ROOT = Path(__file__).resolve().parents[1]`, `_LAUNCH = _ROOT / "scripts"`)."""
    names = set(_REPO_ROOT_NAMES)
    assigns = [(t.id, n.value) for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None
               for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)]
    changed = True
    while changed:
        changed = False
        for name, value in assigns:
            if name not in names and ("__file__" in ast.unparse(value) or any(isinstance(x, ast.Name) and x.id in names for x in ast.walk(value))):
                names.add(name)
                changed = True
    return names


def scans_repository(source: str) -> bool:
    """True when a test walks (rglob / walk / glob / iglob) the REAL repository: the receiver (or an argument) leads back to a repository root, i.e. a name derived from `__file__`, or one of the root
    directories is named as a path. A glob over a stub tree the test builds itself (`(tree.root / "proyecto3" / "log").glob(...)`, `tmp_path.rglob(...)`) is not a scan of the repository sources, which
    is what the owner classified by hand for the launcher end-to-end files (NOT_POLICY in tests/test_delivery_receipt.py). Static, no import. Unparseable = True (it then runs always)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return True
    rooted = _file_rooted_names(tree)
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name, recv = _call_name(n)
        if name not in ("rglob", "walk", "glob", "iglob"):
            continue
        fn = n.func
        subject = fn.value if isinstance(fn, ast.Attribute) and recv not in ("os", "glob", "fnmatch") else (n.args[0] if n.args else None)
        if subject is None:
            continue
        if _leftmost_name(subject) in rooted:
            return True
        strings = [x.value for x in ast.walk(subject) if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        if _leftmost_name(subject) in ("Path", "") and any(s == "." or s.split("/")[0] in _SOURCE_ROOTS for s in strings):      # `Path(".").joinpath("shared").rglob(...)`, `os.walk("shared")`
            return True
    return False


def installed(top: str) -> bool:
    """True when `top` is an importable top-level package of this interpreter (the only impure line of the module; every function that needs it takes it as the injectable `third_party`)."""
    import importlib.util
    try:
        return importlib.util.find_spec(top) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def build_graph(sources: dict, *, third_party: Callable = installed, extra_edges: Iterable = ()) -> Graph:
    """The dependency graph of a tree. `sources` = {path: text} for the .py files and the data / launcher files (DATA_EXTS). `third_party(top)` says whether a top-level import name is an installed
    package (only used to report unresolved imports of tests). `extra_edges` = (importer, imported) pairs learned at runtime. Pure given its arguments."""
    files = {_norm(p): s for p, s in sources.items() if not is_excluded(p)}
    py = sorted(p for p in files if p.endswith(".py"))
    nonpy = sorted(p for p in files if p.endswith(DATA_EXTS))
    py_set, keys = set(py), _dotted_keys(py)
    by_name: dict = {}
    for p in py + nonpy:
        by_name.setdefault(p.rsplit("/", 1)[-1], set()).add(p)
    g = Graph(py_files=py)
    deps: dict = {p: set() for p in py + nonpy}

    def add(src: str, targets: Iterable) -> None:
        for t in targets:
            if t != src and t in deps:
                deps[src].add(t)
                deps[src] |= _ancestor_inits(t, py_set)

    def str_targets(s: str, *, modules: bool, py_files: bool, data: bool) -> set:
        s = s.replace("\\", "/")
        base = s.rsplit("/", 1)[-1]
        out: set = set()
        if base in by_name and ((base.endswith(".py") and py_files) or (base.endswith(DATA_EXTS) and data)):
            out |= by_name[base]
        if modules and _DOTTED.match(s) and s.split(".")[0] not in _STD:
            parts = s.split(".")
            for i in range(len(parts), 1, -1):                                  # the longest prefix of at least two segments (a mock.patch target names a function of the module)
                if ".".join(parts[:i]) in keys:
                    out |= keys[".".join(parts[:i])]
                    break
        return out

    for f in py:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                tree = ast.parse(files[f])
            except (SyntaxError, ValueError, RecursionError):
                g.parse_errors.append(f)
                continue
        test = is_test_path(f)
        derived = _environ_names(tree) if test else set()
        file_strings = list(_strings(tree))
        unresolved_tops: set = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    hit = _resolve(a.name, keys)
                    add(f, hit)
                    if not hit and a.name.split(".")[0] not in _STD:
                        unresolved_tops.add(a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    base = f.split("/")[:-1]
                    base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                    mod = ".".join(base + ([n.module] if n.module else []))
                else:
                    mod = n.module or ""
                hit = _resolve(mod, keys)
                for a in n.names:
                    hit |= _resolve(f"{mod}.{a.name}", keys) if mod else set()
                add(f, hit)
                if not hit and mod and mod.split(".")[0] not in _STD and not n.level:
                    unresolved_tops.add(mod.split(".")[0])
            elif isinstance(n, ast.Call):
                name, _recv = _call_name(n)
                if test and _is_blind_call(n, derived):
                    g.blind_tests.add(f)
                if not test and name in (_LOADERS | _SPAWNERS):
                    arg0 = n.args[0] if n.args else None
                    found: set = set()
                    for s in _strings(n):
                        found |= str_targets(s, modules=True, py_files=True, data=True)
                    prefix = _fstring_prefix(arg0)
                    if prefix and _DOTTED.match(prefix.rstrip(".") + ".x"):                     # f"blocks.{name}" -> every module under `blocks.`
                        p0 = prefix.rstrip(".")
                        found |= {t for k, ts in keys.items() if k == p0 or k.startswith(p0 + ".") for t in ts}
                    if not found and name in _LOADERS:                                          # tier 3: the strings of the same file, then give up (unbounded)
                        for s in file_strings:
                            found |= str_targets(s, modules=True, py_files=True, data=False)
                        if not found and not isinstance(arg0, ast.Constant):
                            g.unbounded.append((f, n.lineno, name))
                    add(f, found)
        if f in NARROW_LOADERS and any(u[0] == f for u in g.unbounded):                          # a documented, test-proved narrowing replaces the conservative set
            g.unbounded = [u for u in g.unbounded if u[0] != f]
            g.narrowed.append(f)
            add(f, {p for p in py if is_runtime_code(p) and p.startswith(NARROW_LOADERS[f])})
        elif any(u[0] == f for u in g.unbounded):                                               # conservative candidate set: every runtime module under the loader's own top-level directory and under any
            tops = {f.split("/")[0]} | {s for s in file_strings if s in _TOP_DIRS}               # top-level directory the file names
            add(f, {p for p in py if is_runtime_code(p) and p.split("/")[0] in tops})
        if test:
            for s in file_strings:
                add(f, str_targets(s, modules=True, py_files=True, data=True))
            if scans_repository(files[f]):
                g.scanners.add(f)
            if unresolved_tops:
                bad = {t for t in unresolved_tops if not third_party(t)}
                if bad:
                    g.unresolved[f] = sorted(bad)
        else:
            for s in file_strings:                                                              # a runtime module that names a data file reads it
                add(f, str_targets(s, modules=False, py_files=False, data=True))
    for f in nonpy:
        if f.endswith((".bat", ".sh")):
            for ref in _FILE_REF.findall(files[f]):
                add(f, by_name.get(ref, set()))
    add_pairs = [(a, b) for a, b in extra_edges if a in deps and b in deps]
    for a, b in add_pairs:
        deps[a].add(b)
    g.deps = deps
    rdeps: dict = {p: set() for p in deps}
    for a, bs in deps.items():
        for b in bs:
            rdeps[b].add(a)
    g.rdeps = rdeps
    return g


def merge_graphs(head: Graph, base: Optional[Graph]) -> Graph:
    """Head graph plus the base graph's edges, so a file deleted or renamed by the change still selects the tests that used to import it."""
    if base is None:
        return head
    for p, bs in base.rdeps.items():
        head.rdeps.setdefault(p, set()).update(bs)
    for p, bs in base.deps.items():
        head.deps.setdefault(p, set()).update(bs)
    head.parse_errors = sorted(set(head.parse_errors) | set(base.parse_errors))
    return head


def closure(graph: Graph, seeds: Iterable) -> set:
    """The seeds and everything that depends on them, directly or through other files. Pure."""
    seen, stack = set(), [s for s in seeds]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(graph.rdeps.get(x, ()))
    return seen


def graph_digest(graph: Graph, extra: str = "") -> str:
    edges = sorted((a, b) for a, bs in graph.deps.items() for b in bs)
    return hashlib.sha256(json.dumps([ALGO_VERSION, edges, extra], separators=(",", ":")).encode("utf-8")).hexdigest()


# ── the decision ────────────────────────────────────────────────────────────────

def suite_tests(paths: Iterable) -> list:
    return sorted({p for p in map(_norm, paths) if suite_of(p)})


def group_by_suite(tests: Iterable) -> dict:
    out: dict = {s: [] for s, _ in SUITE_ROOTS}
    for t in sorted(set(map(_norm, tests))):
        s = suite_of(t)
        if s:
            out[s].append(t)
    return {s: v for s, v in out.items() if v}


def always_run(graph: Graph, tests: Iterable) -> set:
    """Tests that run in every impact receipt: repository scanners, tests whose imports do not resolve and tests that start subprocesses the tracer cannot follow. Computed, never listed by hand."""
    ts = set(tests)
    return (graph.scanners | graph.blind_tests | set(graph.unresolved)) & ts


def decide(changed: Iterable, sources_head: dict, sources_base: Optional[dict] = None, *, policy_tests: Iterable = (), enabled: bool = True,
           third_party: Callable = installed, extra_edges: Iterable = (), extra_digest: str = "", durations: Optional[dict] = None,
           median_full_seconds: Optional[float] = None, fixed_overhead_seconds: float = 60.0) -> Decision:
    """The decision for a change: `impact` with the selected tests, or `full` with the reasons. `changed` = the repo-relative paths the change touches (added, modified, deleted, renamed both ways).
    `policy_tests` = the tests every impact receipt must include (delivery_receipt.POLICY_TESTS file part). Pure given its arguments; any exception while analysing is itself F1."""
    changed = sorted({_norm(c) for c in changed if not is_excluded(c)})
    head_files = {_norm(p) for p in sources_head}
    gone = [c for c in changed if c not in head_files]                                          # deleted / renamed away: the base graph still knows who imported them
    try:
        graph = merge_graphs(build_graph(sources_head, third_party=third_party, extra_edges=extra_edges),
                             build_graph(sources_base, third_party=third_party, extra_edges=extra_edges) if (sources_base and gone) else None)
    except Exception as exc:                                                                     # noqa: BLE001 -- F1: an analysis that fails is never a reason to run less
        return Decision("full", [f"F1 the dependency graph could not be built: {type(exc).__name__}: {str(exc)[:120]}"], {}, 0, 0)
    all_tests = suite_tests(p for p in head_files if is_test_path(p) or p.rsplit("/", 1)[-1].startswith("test_"))
    total = len(all_tests)
    digest = graph_digest(graph, extra_digest)

    def full(*why: str) -> Decision:
        return Decision("full", list(why), {}, 0, total, graph_digest=digest, unbounded_sites=len(graph.unbounded))

    if not enabled:
        return full("F8 impact selection is switched off")
    code = [c for c in changed if is_code_path(c)]
    if not code:
        return full("F4 no changed file is code (docs_only is decided by delivery_receipt, not here)")
    bad = [c for c in code if c in graph.parse_errors] + [t for t in graph.parse_errors if suite_of(t)]
    if bad:
        return full(f"F1 a changed or test file does not parse: {sorted(set(bad))[:5]}")
    infra = [c for c in code if is_infra(c)]
    if infra:
        return full(f"F2 changed test infrastructure or the selector: {infra[:5]}")
    runtime = [c for c in code if is_runtime_code(c)]
    if len(runtime) > MAX_RUNTIME_FILES:
        return full(f"F6 {len(runtime)} runtime files changed (limit {MAX_RUNTIME_FILES})")
    test_set = set(all_tests)
    selected: set = set()
    hubs: list = []
    uncovered: list = []
    for c in code:
        reach = closure(graph, [c])
        hits = reach & test_set
        if is_test_path(c) and suite_of(c):
            selected |= {c} & test_set                                                           # a changed test runs itself (a deleted one is nothing to run)
            selected |= hits
            continue
        if total and len(hits) > HUB_SHARE * total:
            hubs.append(f"{c} ({len(hits)}/{total})")
        elif not hits:
            uncovered.append(c)
        selected |= hits
    if hubs:
        return full(f"F3 changed hub: {hubs[:5]}")
    if uncovered:
        return full(f"F4 no test reaches the changed code file(s): {uncovered[:5]}")
    selected |= always_run(graph, all_tests)
    selected &= test_set
    policy = sorted({p for p in policy_tests if _norm(p).split("::", 1)[0] in test_set})         # kept as `file::test` entries: the policy set runs one test of a file that also holds module tests
    reach = selected | {_norm(p).split("::", 1)[0] for p in policy}
    if total and len(reach) >= HUB_SHARE * total:
        return full(f"F5 the selection is {len(reach)} of {total} test files: no benefit")
    if durations and median_full_seconds:
        projected = fixed_overhead_seconds + sum(float(durations.get(t, 0.0)) for t in reach)
        if projected >= 0.6 * median_full_seconds:
            return full(f"F5 projected {projected:.0f}s is at least 60% of the median full run {median_full_seconds:.0f}s")
    notes = [f"unbounded loader sites in the tree: {len(graph.unbounded)}"] if graph.unbounded else []
    return Decision("impact", notes, group_by_suite(selected), len(selected), total, graph_digest=digest, unbounded_sites=len(graph.unbounded), policy=policy)


# ── F7: freshness by ancestry ───────────────────────────────────────────────────

def _intact(r: dict) -> bool:
    return bool(r) and r.get("digest") == digest_of(r)


def freshness_reason(head: str, receipts: list, *, is_ancestor: Callable, code_commits_between: Callable, hours_between: Callable,
                     max_commits: int = 5, max_hours: float = 24.0) -> tuple:
    """(reason | None, anchor receipt | None). The anchor is the newest intact PASSING `full` receipt whose sha is an ancestor of `head`; the change needs a full receipt when there is none, when more than
    `max_commits` code-changing commits lie between it and `head`, or when it is older than `max_hours`. Callbacks (git): `is_ancestor(a, b)`, `code_commits_between(a, b) -> int | None`,
    `hours_between(a, b) -> float | None`; an unknown answer (None) widens. Only ancestry counts, never a local timestamp or a stale ref. Pure given the callbacks."""
    anchor = None
    for r in reversed(receipts):
        if r.get("scope") == "full" and r.get("result") == "pass" and _intact(r) and r.get("sha") and is_ancestor(r["sha"], head):
            anchor = r
            break
    if anchor is None:
        return "F7 no passing full receipt is an ancestor of HEAD", None
    n = code_commits_between(anchor["sha"], head)
    if n is None or n > max_commits:
        return f"F7 {n if n is not None else 'an unknown number of'} code-changing commits since the last full receipt {anchor['sha'][:12]} (limit {max_commits})", anchor
    h = hours_between(anchor["sha"], head)
    if h is None or h > max_hours:
        return f"F7 the last full receipt {anchor['sha'][:12]} is {'of unknown age' if h is None else f'{h:.0f} h older'} than HEAD (limit {max_hours:.0f} h)", anchor
    return None, anchor


# ── the mode, derived from the receipts ─────────────────────────────────────────

def _parse_ts(ts: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def effective_mode(receipts: list, algo_version: int = ALGO_VERSION, *, ceiling: str = "AUTO", enabled: bool = True, min_receipts: int = 10, min_days: float = 7.0) -> str:
    """OFF | SHADOW | ENFORCE, a pure function of the receipts: nothing is stored, so a person never flips it and an escape demotes it by itself.
    ENFORCE needs, for this `algo_version`: an intact PASS evidence line (kind `impact_evidence`, written by the evidence harness), and a streak of at least `min_receipts` intact SHADOW receipts in
    which the selector decided `impact` (the only decisions that could have been wrong), spanning at least `min_days` days with no escape since the streak began. Receipts written while ENFORCE neither
    add to nor break the streak; only an escape does. An escape (a failing full test the selection missed, an import edge the graph did not know, an order-dependence
    mismatch) empties the streak. A new algo version starts again at SHADOW."""
    if not enabled or ceiling == "OFF":
        return "OFF"
    if ceiling == "SHADOW":
        return "SHADOW"
    evidence = [r for r in receipts if r.get("kind") == "impact_evidence" and r.get("algo") == algo_version and r.get("result") == "pass" and _intact(r)]
    if not evidence:
        return "SHADOW"
    streak: list = []
    for r in receipts:
        sel = r.get("selection") or {}
        if r.get("kind") == "impact_evidence" or sel.get("algo") != algo_version or not _intact(r):
            continue
        if sel.get("escapes"):
            streak = []
            continue
        if sel.get("mode") == "SHADOW" and (sel.get("decision") or {}).get("scope") == "impact":      # only a decision that narrowed the run can be shown wrong; a `full` one proves nothing about the selector
            streak.append(r)
    if len(streak) < min_receipts:
        return "SHADOW"
    first, last = _parse_ts(streak[0].get("ts")), _parse_ts(streak[-1].get("ts"))
    if first is None or last is None or (last - first).total_seconds() < min_days * 86400:
        return "SHADOW"
    return "ENFORCE"


def find_escapes(failed_tests: Iterable, selected: Iterable, all_tests: Optional[Iterable] = None) -> list:
    """The failing test FILES (from pytest ids such as `tests/test_x.py::test_y`) that the selection did not contain: an escape. The proyecto2 suite runs with that directory as cwd, so its ids lack
    the `proyecto2/` prefix: with `all_tests` (every suite test file) an id is matched as written and under `proyecto2/`; it is an escape when a candidate exists and none was selected, and also
    when no candidate exists at all (a failure that cannot be shown to be selected is never assumed to be). Pure."""
    chosen = {_norm(s).split("::", 1)[0] for s in selected}
    known = {_norm(t) for t in all_tests} if all_tests is not None else None
    out = []
    for t in failed_tests:
        f = _norm(str(t).split("::", 1)[0])
        if not f.endswith(".py"):
            continue
        candidates = [c for c in (f, "proyecto2/" + f) if known is None or c in known] or [f]
        if not any(c in chosen for c in candidates) and f not in out:
            out.append(f)
    return out
