"""FND-0385 / FND-0387: the impact selector on the REAL repository (shared/test_impact.py). Split from tests/test_test_impact.py so the two files run in parallel shards (each decision builds the dependency graph of
the whole tree, about 13 s): the 336daff8 regression, the hub modules, the narrowed loaders, the real `shared/config.py` bounded by its symbols, and the repository scanners that keep the NARROWED_READERS
(owner-approved 2026-11-12) true."""
import ast
import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import delivery_receipt as dr  # noqa: E402
from shared import test_impact as ti  # noqa: E402

# ── the real repository ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]


@functools.lru_cache(maxsize=1)
def _real_sources_cached():
    return _real_sources_uncached()


def _real_sources():
    return dict(_real_sources_cached())


def _real_sources_uncached():
    sys.path.insert(0, str(ROOT / "scripts" / "audit"))
    import delivery_attest as da                                              # the one reader the receipt uses, so the tests analyse what a receipt would
    return da.read_sources(ROOT)


@functools.lru_cache(maxsize=1)
def _real_graph():
    """The dependency graph of the real tree, built ONCE for the file (about 13 s): every decision below is taken on this tree, or on it with one line of `shared/config.py` changed, which adds no edge."""
    return ti.build_graph(dict(_real_sources_cached()))


def _decide_real(changed, sources, base=None, **kw):
    return ti.decide(changed, sources, base, graph=_real_graph(), **kw)


def test_the_336daff8_failure_is_selected_by_the_graph_and_missed_by_name_matching():
    """proyecto1/core/cost_table_parser.py changed and proyecto1/tests/test_cost_label_binding_fnd0279.py failed in the full run; name matching never picks that test."""
    changed = ["proyecto1/core/cost_table_parser.py"]
    avail = [t for t in _real_sources() if t.endswith(".py") and ti.suite_of(t)]
    by_name, _ = dr.select_tests(changed, avail)
    assert "proyecto1/tests/test_cost_label_binding_fnd0279.py" not in by_name
    d = _decide_real(changed, _real_sources(), policy_tests=dr.POLICY_TESTS)
    assert d.scope == "impact" and "proyecto1/tests/test_cost_label_binding_fnd0279.py" in d.selected["proyecto1"]
    assert d.policy == sorted(t for t in dr.POLICY_TESTS if (ROOT / t.split("::")[0]).exists())      # all 18 policy entries ride along, node-level, in every impact decision


def test_the_hub_modules_are_full_on_the_real_tree():
    src = _real_sources()
    for hub in ("shared/config.py", "shared/backlog_client.py", "shared/out_dir.py"):
        assert _decide_real([hub], src).scope == "full", hub


def test_narrowed_loaders_are_still_true():
    """Each NARROW_LOADERS entry is only sound while its claim holds; this re-proves the three claims on the real tree."""
    src = _real_sources()
    g = ti.build_graph(src)
    assert set(ti.NARROW_LOADERS) <= set(g.narrowed) | set(), "a narrowed file that no longer has an unbounded loader should leave NARROW_LOADERS"
    # 1. env_guard loads the configured driver: "psycopg" or a name that is not a repo module
    from shared import env_guard
    keys = ti._dotted_keys(g.py_files)
    assert ti._resolve(env_guard.driver_module(), keys) == set()
    for t in ROOT.joinpath("tests").glob("test_*.py"):
        for m in __import__("re").findall(r'FONDOS_DB_DRIVER_MODULE["\']?\s*[:,=]\s*["\']([\w.]+)["\']', t.read_text(encoding="utf-8", errors="replace")):
            assert ti._resolve(m, keys) == set(), f"{t.name} points the driver at a repo module: {m}"
    # 2. pit_metrics: every load_p2_calc call passes literals naming a file under the declared prefixes
    prefixes = ti.NARROW_LOADERS["proyecto3/src/pit_metrics.py"]
    seen = 0
    for f, text in src.items():
        if not f.endswith(".py") or "load_p2_calc" not in text:
            continue
        for n in ast.walk(ast.parse(text)):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "load_p2_calc" and n.args:
                if f == "proyecto3/src/pit_metrics.py" and isinstance(n.args[0], ast.Name):
                    continue                                                              # the definition's own recursion on its parameter
                assert all(isinstance(a, ast.Constant) for a in n.args), f"{f}:{n.lineno} passes a non-literal to load_p2_calc"
                name = n.args[0].value
                sub = n.args[1].value if len(n.args) > 1 else "calculations"
                target = f"proyecto2/src/{sub}/{name}.py"
                assert target in src and target.startswith(prefixes), f"{f}:{n.lineno} loads {target}"
                seen += 1
    assert seen >= 5
    # 3. a registered writer's entry module lives under scripts/audit
    from shared import backlog_writers as bw
    registry = getattr(bw, "WRITERS", None) or getattr(bw, "REGISTRY", None) or {}
    specs = registry.values() if isinstance(registry, dict) else registry
    for spec in specs:
        entry = getattr(spec, "entry", None)
        if entry:
            module = entry.split(":")[0]
            assert any(p.startswith(ti.NARROW_LOADERS["shared/backlog_writer_proof.py"]) for p in ti._resolve(module, keys)), f"writer {spec.name} has its entry outside scripts/audit: {entry}"


def test_the_real_config_bounded_by_symbols_a_new_constant_is_impact_and_every_test_naming_an_edited_one_is_selected():
    src = dict(_real_sources())
    base = src["shared/config.py"]
    new = _decide_real(["shared/config.py"], {**src, "shared/config.py": base + "\nA_BRAND_NEW_FLAG_ENABLED: bool = False\n"}, base_texts={"shared/config.py": base}, policy_tests=dr.POLICY_TESTS)
    assert new.scope == "impact" and new.selected_count < 0.4 * new.suite_total, (new.scope, new.reasons, new.selected_count)
    edit = _decide_real(["shared/config.py"], {**src, "shared/config.py": base.replace('SCHEMA_VERSION: str = "v27"', 'SCHEMA_VERSION: str = "v28"')}, base_texts={"shared/config.py": base}, policy_tests=dr.POLICY_TESTS)
    users = [t for t, text in src.items() if t.endswith(".py") and ti.suite_of(t) and "SCHEMA_VERSION" in text]
    assert edit.scope == "full" or set(users) <= set(edit.files()), "every test that names SCHEMA_VERSION must be selected"


# ── the two owner-approved narrowed readers (NARROWED_READERS): the reasoning must stay true ──────────────────

def _is_test(path):
    return "/tests/" in "/" + path or path.rsplit("/", 1)[-1].startswith("test_")


def _compares_with(tree, needle):
    """Compare nodes (==, !=, <, in ...) with `needle` in the text of one of their operands. Pure."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.Compare) and any(needle in ast.unparse(o) for o in [n.left, *n.comparators])]


def test_narrowed_readers_are_real_enumerators_so_the_exemption_is_not_stale():
    src = _real_sources()
    for hub, readers in ti.NARROWED_READERS.items():
        for f, names in readers.items():
            assert f in src, f"{f} no longer exists: drop its NARROWED_READERS entry"
            bindings, _anon = ti._bindings(src[f], hub)
            hazard_names = {n for ns, _t, hz in bindings if hz for n in ns}
            assert set(names) <= hazard_names, f"{f}: {sorted(set(names) - hazard_names)} no longer enumerate {hub}: drop the entry"


def test_narrowed_identity_reader_the_config_hash_is_recorded_never_compared_and_no_test_pins_a_literal_one():
    src = _real_sources()
    users = sorted(f for f, t in src.items() if f.endswith(".py") and not _is_test(f) and ("config_hash" in t or "config_items" in t))
    assert users == ["scripts/audit/run_statistical_audit.py", "scripts/launch/p3_promote_scenario.py", "shared/governance/orchestrator.py", "shared/governance/run_identity.py",
                     "shared/statistical_audit/persistence.py"], f"a new reader of config_hash / config_items needs a review of NARROWED_READERS: {users}"
    for f in users:
        tree = ast.parse(src[f])
        for node in _compares_with(tree, "config_hash"):
            if f == "shared/governance/run_identity.py":
                continue                                                      # the definition's own checks
            assert False, f"{f}:{node.lineno} compares a config hash: the identity reading no longer holds ({ast.unparse(node)[:90]})"
    for f, t in src.items():
        if not (f.endswith(".py") and _is_test(f)) or "config_hash" not in t:
            continue
        for node in _compares_with(ast.parse(t), "config_hash("):
            operands = [node.left, *node.comparators]
            assert not any(isinstance(o, ast.Constant) and isinstance(o.value, (str, bytes)) for o in operands), \
                f"{f}:{node.lineno} pins a literal config hash: a new switch would break it unseen ({ast.unparse(node)[:90]})"


def test_narrowed_vocabulary_reader_has_one_caller_and_no_test_pins_its_size():
    src = _real_sources()
    callers = sorted(f for f, t in src.items() if f.endswith(".py") and not _is_test(f) and "build_vocabulary(" in t)
    assert callers == ["scripts/audit/backlog_links.py"], f"build_vocabulary has a new caller: {callers}"
    own = ast.parse(src["scripts/audit/backlog_links.py"])
    users = [n for n in ast.walk(own) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "build_vocabulary"]
    assert len(users) == 1 and "_load" in src["scripts/audit/backlog_links.py"].split("build_vocabulary()")[0].rsplit("def ", 1)[-1][:20]       # called from `_load` only
    for f, t in src.items():
        if not (f.endswith(".py") and _is_test(f)) or "vocab" not in t:
            continue
        for node in ast.walk(ast.parse(t)):
            if isinstance(node, ast.Compare) and any("len(" in ast.unparse(o) and "vocab" in ast.unparse(o) for o in [node.left, *node.comparators]):
                assert False, f"{f}:{node.lineno} pins the size of the vocabulary: {ast.unparse(node)[:90]}"


def test_with_the_narrowing_a_new_constant_no_longer_selects_the_cycle_end_to_end_tests_but_a_changed_switch_still_selects_its_readers():
    src = dict(_real_sources())
    base = src["shared/config.py"]
    new = _decide_real(["shared/config.py"], {**src, "shared/config.py": base + "\nA_BRAND_NEW_FLAG_ENABLED: bool = False\n"}, base_texts={"shared/config.py": base}, policy_tests=dr.POLICY_TESTS)
    assert new.scope == "impact"
    files = set(new.files())
    assert not files & {"tests/test_batch_cycle_audits_e2e.py", "tests/test_cycle_sequence_e2e.py", "tests/test_batch_launchers_e2e.py"}, sorted(files)[:5]
    users = [t for t, text in src.items() if t.endswith(".py") and ti.suite_of(t) and "AUDIT_COMPLETENESS_CONTRACT_ENABLED" in text]
    edit = _decide_real(["shared/config.py"], {**src, "shared/config.py": base.replace("AUDIT_COMPLETENESS_CONTRACT_ENABLED: bool = True", "AUDIT_COMPLETENESS_CONTRACT_ENABLED: bool = False")},
                     base_texts={"shared/config.py": base}, policy_tests=dr.POLICY_TESTS)
    assert edit.scope == "full" or set(users) <= set(edit.files()), "every test that names an edited switch must stay selected"
