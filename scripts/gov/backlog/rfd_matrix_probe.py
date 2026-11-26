#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
rfd_matrix_probe.py -- evidence matrix for the READY_FOR_DEPLOY tickets of gestion.backlog (read-only). Nothing is closed by this script.

A READY_FOR_DEPLOY ticket means "developed, not deployed". It may be closed only when what the ticket says must be true in PRODUCTION is true. Closing them
in bulk would hide the ones still waiting for an owner action (a flag still off, a recompute not run, a Windows task not checked). This script states,
per ticket, the predicates that decide it and evaluates the ones a machine can evaluate:

    pushed(sha)            the commit is on origin/master            flag(NAME, True)     shared.config switch has the value the ticket needs
    absent_re_in(file, rx) no code reference matching rx (a quoted metric name, not a docstring mention)
    calc_share(min)        share of fund_metrics rows already on the current CALC_VERSION (i.e. the recompute ran)
    metric_absent(metric)  no row of a retired metric             absent_in(file, text) the source no longer contains the text
    ols_ran()              a P2 RUN_SUMMARY with ols_funds > 0 exists after the CALC_VERSION bump
    ticket_closed(id)      another ticket this one closes with        task_ok(name)  last result 0 of a Windows scheduled task
    tests_pass([sha])      CI green: every check run of the change that delivers the ticket (the second parent of its merge, or `sha`) passed, and every check the branch requires has run
    manual(text)           an owner action no script can see

Verdict per ticket: READY (every probe passes: closing is a decision with evidence), BLOCKED (a probe fails: a named precondition is missing),
MANUAL (machine probes pass but an owner action remains). The workflow is in doc/operativos/BACKLOG_RFD_VERIFICATION_MATRIX.md.

    python scripts/gov/backlog/rfd_matrix_probe.py            # prints the matrix; exit 0
    python scripts/gov/backlog/rfd_matrix_probe.py --md FILE  # also writes it as markdown
    python scripts/gov/backlog/rfd_matrix_probe.py --close-ready        # only the tickets whose every probe passes
    python scripts/gov/backlog/rfd_matrix_probe.py --seed [--apply]     # copy SPEC into backlog.close_condition (tickets still READY_FOR_DEPLOY)

Source of the conditions (FND-0334, Phase C slice 1): backlog.close_condition once the owner applied `migrate_backlog_v2.py --phase sync` (added with
shared.backlog_client.add_close_condition(), retired with retire_close_condition(): adding a ticket is no longer a code change); until then the SPEC
dictionary below. After the seed, SPEC is only the fallback for a database without the store.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# ticket -> list of probes: (kind, *args). The text of each ticket's closing condition is in its backlog log; this table is its executable form.
SPEC = {
    # the DBeaver / Metabase repoint (manual) was split out to FND-0248: this ticket is now fully machine-verifiable
    "FND-0073": [("task_ok", "Fondos PG basebackup+prune"), ("task_ok", "Fondos PG offbox copy")],
    "FND-0181": [("pushed", "af3a3db"), ("metric_absent", "beta_m2_global")],
    "FND-0196": [("pushed", "8507fad"), ("flag", "MACRO_VIF_ITERATIVE_ENABLED", True), ("calc_share", 0.95), ("ols_ran",)],
    "FND-0200": [("pushed", "8507fad"), ("flag", "PERSISTENCE_FIRST_LAST_NAV_ENABLED", True), ("calc_share", 0.95)],
    "FND-0201": [("pushed", "7793f0d"), ("absent_in", "proyecto2/src/readers/db_readers.py", "data_status != 'INACTIVE'")],
    "FND-0202": [("pushed", "8507fad"), ("flag", "CAPTURE_MONTH_END_ENABLED", True), ("calc_share", 0.95)],
    "FND-0203": [("ticket_closed", "FND-0236")],
    "FND-0225": [("pushed", "b38125d"), ("absent_re_in", "proyecto3/src/fund_scorer.py", r"""["']beta_(oil|rate_eu|spread_hy|vix)["']""")],
    "FND-0226": [("pushed", "8507fad"), ("flag", "MACRO_FACTOR_CLEAN_ENABLED", True), ("calc_share", 0.95)],
    "FND-0229": [("pushed", "d7202ec"), ("ols_ran",), ("calc_share", 0.95)],
    "FND-0235": [("pushed", "c0312a7"), ("flag", "FX_CONTRIBUTION_EUR_VIEW_ENABLED", True),
                 ("manual", "ships in the SAME release as FND-0243 (owner directive 2026-10-07)")],
    "FND-0240": [("pushed", "ed6df6c"), ("flag", "ANNUALIZATION_INTERVAL_ENABLED", True), ("manual", "risk + rolling recompute done and the audit clean afterwards")],
    "FND-0241": [("pushed", "929ceed"), ("flag", "DEFLATION_MONTH_ALIGN_ENABLED", True), ("manual", "risk + rolling recompute done and the audit clean afterwards")],
    "FND-0242": [("pushed", "d304ba6"), ("flag", "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED", True), ("manual", "risk + rolling recompute done and the audit clean afterwards")],
}
PROBE_KINDS = {"pushed", "flag", "calc_share", "metric_absent", "absent_in", "absent_re_in", "ols_ran", "ticket_closed", "task_ok", "tests_pass", "audit_isins_clear", "db_check", "manual"}
AUDIT_FINISHED = ("PASS", "PASS_WITH_WARNINGS", "FAIL")                   # the verdicts of a finished run of the statistical audit (an INCOMPLETE run proves nothing)

# audit_isins_clear: the rows that mark a fund as held out of the audit's cross-sectional snapshot (a stale or frozen fund: its NAV is too far from the others) are not violations, but they mean the
# audit did NOT evaluate that fund, so it cannot be called clear either (FND-0423: the six frozen funds of FND-0250 carry one such row in every run, so the probe failed for them for ever).
STRUCTURAL_MARKER_RULES = ("SNAPSHOT_HELD_OUT",)

# db_check (FND-0423): positive log and table probes and a rule-scoped audit probe, as a CLOSED registry of named checks. The condition names a check and its parameters; the SQL of each check is a
# fixed literal here (never text from the condition), and a comparison is done in Python against a whitelisted operator, so a condition cannot carry a statement. args = [check, *params].
DB_CHECK_COMPARATORS = (">=", "<=", "==", ">", "<")
AUDIT_DOMAINS = ("p2_metrics", "cost_attributes")
# table_rows(table, op, n): a row count of a table that tickets talk about. Each query is a named literal that probe_db_check executes directly (tests/test_sql_explain_sweep_pg.py EXPLAINs it).
Q_ROWS_SERIES_BENCHMARK = "SELECT COUNT(*) FROM bronze.series_benchmark"
Q_ROWS_NAV_SOURCES = "SELECT COUNT(*) FROM control.nav_sources"
Q_ROWS_NAV_SOURCES_OLDER_180D = "SELECT COUNT(*) FROM control.nav_sources WHERE last_nav_date < CURRENT_DATE - 180"
TABLE_ROWS_SQL = {"series_benchmark": Q_ROWS_SERIES_BENCHMARK, "nav_sources": Q_ROWS_NAV_SOURCES, "nav_sources_older_180d": Q_ROWS_NAV_SOURCES_OLDER_180D}
# FND-0441: every stored Geography has a source, and the 'Global' that nothing supports does not rise above its level at delivery, by nature.
Q_GEO_WITHOUT_SOURCE = "SELECT COUNT(*) FROM silver.fund_master WHERE geography IS NOT NULL AND geography_source IS NULL"
Q_GEO_DEFAULT_BY_NATURE = "SELECT COALESCE(fund_nature, '-'), COUNT(*) FROM silver.fund_master WHERE geography = 'Global' AND geography_source = 'default' GROUP BY 1 ORDER BY 1"
_NATURE = re.compile(r"[A-Za-z\- ]{1,40}")
_RULE_ID = re.compile(r"[A-Za-z0-9_]{3,80}")
_STEP_OR_STATUS = re.compile(r"[A-Za-z0-9_]{1,60}")
_ISIN = re.compile(r"[A-Z0-9]{12}")


# ── pure ──────────────────────────────────────────────────────────────────────────────────────────

def verdict(results: list) -> str:
    """results = [(ok, detail)] with ok True / False / None (None = manual). BLOCKED beats MANUAL beats READY."""
    if any(ok is False for ok, _ in results):
        return "BLOCKED"
    if any(ok is None for ok, _ in results):
        return "MANUAL"
    return "READY"


def render_markdown(rows: list) -> str:
    out = ["| Ticket | Verdict | Evidence |", "|---|---|---|"]
    for tid, v, results in rows:
        ev = "<br>".join(f"{'OK' if ok else ('MANUAL' if ok is None else 'FAIL')}: {d}" for ok, d in results)
        out.append(f"| {tid} | **{v}** | {ev} |")
    return "\n".join(out)


def _is_int(v, minimum: int = 0) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= minimum


def validate_db_check(args) -> list:
    """The problems of a `db_check` condition's args ([check, *params]); empty when it is well formed. Pure, and the only gate on the text of a condition before it is evaluated (the registry is closed)."""
    args = list(args)
    if not args or not isinstance(args[0], str):
        return ["db_check needs the name of a check as its first argument"]
    name, p = args[0], args[1:]
    if name == "table_rows":
        if len(p) != 3 or p[0] not in TABLE_ROWS_SQL or p[1] not in DB_CHECK_COMPARATORS or not _is_int(p[2]):
            return [f"table_rows needs [table in {sorted(TABLE_ROWS_SQL)}, operator in {list(DB_CHECK_COMPARATORS)}, a non-negative integer]"]
    elif name == "ingestion_events":
        if len(p) != 5 or not (isinstance(p[0], str) and _STEP_OR_STATUS.fullmatch(p[0])) or not (isinstance(p[1], str) and _STEP_OR_STATUS.fullmatch(p[1])) \
                or p[2] not in DB_CHECK_COMPARATORS or not _is_int(p[3]) or not _is_int(p[4], 1):
            return ["ingestion_events needs [step, status, operator, a non-negative count, a number of days >= 1]"]
    elif name == "p2_run_summary":
        if len(p) != 2 or not _is_int(p[0]) or not _is_int(p[1], 1):
            return ["p2_run_summary needs [minimum processed funds >= 0, a number of days >= 1]"]
    elif name == "audit_rule_clear":
        if len(p) < 3 or p[0] not in AUDIT_DOMAINS or not (isinstance(p[1], str) and _RULE_ID.fullmatch(p[1])) \
                or not all(isinstance(i, str) and _ISIN.fullmatch(i) for i in p[2:]):
            return [f"audit_rule_clear needs [domain in {list(AUDIT_DOMAINS)}, a rule id, at least one ISIN]"]
    elif name == "geography_without_source":
        if p:
            return ["geography_without_source takes no argument"]
    elif name == "geography_default_global":
        natures = p[0::2]
        if not p or len(p) % 2 or not all(isinstance(n, str) and _NATURE.fullmatch(n) for n in natures) or len(set(natures)) != len(natures) or not all(_is_int(v) for v in p[1::2]):
            return ["geography_default_global needs pairs [fund nature, its level at delivery >= 0], each nature once"]
    else:
        return [f"unknown db_check {name!r} (known: audit_rule_clear, geography_default_global, geography_without_source, ingestion_events, p2_run_summary, table_rows)"]
    return []


def compare(value, op: str, n) -> bool:
    """`value op n` for a whitelisted operator spelling; anything else is a ValueError, never evaluated. Pure."""
    if op not in DB_CHECK_COMPARATORS:
        raise ValueError(f"operator {op!r} is not one of {DB_CHECK_COMPARATORS}")
    return {">=": value >= n, "<=": value <= n, "==": value == n, ">": value > n, "<": value < n}[op]


def spec_problems(spec: dict = SPEC) -> list:
    out = [f"{t}: unknown probe {p[0]!r}" for t, probes in spec.items() for p in probes if p[0] not in PROBE_KINDS]
    out += [f"{t}: {why}" for t, probes in spec.items() for p in probes if p[0] == "db_check" for why in validate_db_check(p[1:])]
    return out


def choose_spec(stored) -> tuple:
    """(spec, source): the closing conditions in backlog.close_condition once the owner applied Phase C slice 1 (FND-0334), else the SPEC
    dictionary above. `stored` is backlog_client.close_conditions() (None = store not applied)."""
    if stored is None:
        return SPEC, "SPEC dictionary (backlog.close_condition not applied)"
    return stored, "backlog.close_condition"


def seed_rows(spec: dict, statuses: dict, stored: dict) -> tuple:
    """(to_add, skipped) for seeding backlog.close_condition from `spec`: only tickets still READY_FOR_DEPLOY, never a condition already active.
    to_add = [(ticket, kind, [args])]; skipped = [(ticket, reason)]."""
    to_add, skipped = [], []
    for tid, probes in spec.items():
        if statuses.get(tid) != "READY_FOR_DEPLOY":
            skipped.append((tid, f"status {statuses.get(tid, 'NOT_FOUND')}"))
            continue
        have = {tuple(p) for p in stored.get(tid, [])}
        for p in probes:
            if tuple(p) not in have:
                to_add.append((tid, p[0], list(p[1:])))
    return to_add, skipped


# ── probes (read-only) ────────────────────────────────────────────────────────────────────────────

def _run(cmd: list) -> tuple:
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60)
    return r.returncode, r.stdout.strip()


def probe_pushed(sha: str) -> tuple:
    rc, out = _run(["git", "-C", str(ROOT), "branch", "-r", "--contains", sha])
    return ("origin/master" in out), f"commit {sha} {'is' if 'origin/master' in out else 'is NOT'} on origin/master"


def probe_flag(name: str, want: bool) -> tuple:
    """`flag`: a switch of shared/config.py has the value the condition wants. Each switch is read by its LITERAL name, drawn from the inline list below (FND-0423): a read by a computed name (`getattr(cfg, name)`)
    makes every module that imports this one depend on ANY change of shared/config.py for the impact selector (shared/test_impact.py), and the cycle report now imports this module, which would pull the launcher
    end-to-end tests into every config change. A switch a new condition needs is added to the list, with its test (tests/test_rfd_matrix_probe.py checks the list covers every switch of SPEC)."""
    sys.path.insert(0, str(ROOT))
    import shared.config as cfg
    for known in ("ANNUALIZATION_INTERVAL_ENABLED", "CAPTURE_MONTH_END_ENABLED", "DEFLATION_MONTH_ALIGN_ENABLED", "FX_CONTRIBUTION_EUR_VIEW_ENABLED", "MACRO_FACTOR_CLEAN_ENABLED",
                  "MACRO_VIF_ITERATIVE_ENABLED", "PERSISTENCE_FIRST_LAST_NAV_ENABLED", "RATIO_VOL_FLOOR_ENABLED", "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED"):
        if known == name:
            have = getattr(cfg, known, None)
            return (have is want), f"{name} = {have} (needs {want})"
    raise ValueError(f"flag {name!r} is not one the probe may read: add it to the literal list of probe_flag (with its test)")


def probe_absent_in(rel: str, text: str) -> tuple:
    src = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    return (text not in src), f"{rel} {'no longer contains' if text not in src else 'STILL contains'} {text!r}"


def probe_absent_re_in(rel: str, pattern: str) -> tuple:
    """Like absent_in, for code references: a quoted metric name as the scorer would READ it, not a mention in a docstring."""
    hit = re.search(pattern, (ROOT / rel).read_text(encoding="utf-8", errors="replace"))
    return (hit is None), f"{rel} {'has no code reference matching' if hit is None else 'STILL references'} {pattern!r}" + ("" if hit is None else f" ({hit.group(0)})")


def probe_task_ok(name: str) -> tuple:
    rc, out = _run(["schtasks", "/query", "/tn", name, "/v", "/fo", "list"])
    if rc != 0:
        return False, f"scheduled task {name!r} not found"
    m = re.search(r"(?:Last Result|Resultado de la .ltima ejecuci.n|.ltimo resultado)\s*:\s*(\-?\d+)", out)
    if not m:
        return None, f"task {name!r}: could not read its last result (locale); check LastTaskResult = 0 by hand"
    return (m.group(1) == "0"), f"task {name!r} last result = {m.group(1)}"


def probe_tests_pass(tid: str, sha: str = None, evidence: dict = None, read=None, git=None, local=None) -> tuple:
    """`tests_pass` (Backlog v3 W4, FND-0359): (True | False | None, text). The commit is `sha` when the condition names one, else the delivering commit the autopilot knows for the ticket (`evidence` =
    {ticket: sha}). No delivering commit yet is False ("not yet"). With BACKLOG_GITHUB_EVIDENCE_ENABLED False (local delivery mode, FND-0375) it is true only when a local test receipt names that exact full sha,
    is intact, and its tests really ran and passed at a sufficient scope (shared/delivery_receipt.py; scripts/gov/delivery/delivery_attest.py leaves the receipt); otherwise CI green as before: the checks are read on
    the PULL REQUEST'S HEAD, the second parent of a merge (shared.backlog_ci.evidence_commit), and an unreadable GitHub is None (unverifiable). Neither is a pass, and the autopilot asks again on its next run.
    `read`, `git` and `local` (a replacement for delivery_attest.verify) are injectable for the tests."""
    commit = sha or (evidence or {}).get(tid)
    if not commit:
        return False, f"{tid}: no delivering commit on record yet, so there is no test evidence to read"
    sys.path.insert(0, str(ROOT))
    import shared.config as cfg
    if not cfg.BACKLOG_GITHUB_EVIDENCE_ENABLED:
        sys.path.insert(0, str(ROOT / "scripts" / "gov" / "delivery"))
        import delivery_attest as da
        ok, detail = (local or da.verify)(ROOT, commit, da.RECEIPTS)
        return ok, f"tests_pass: {detail}"
    from shared import backlog_ci as ci
    run_git = git or _run
    rc, out = run_git(["git", "-C", str(ROOT), "rev-list", "--parents", "-n", "1", commit])
    parents = out.split()[1:] if rc == 0 and out else []
    head = ci.evidence_commit(commit, parents)
    # NO secret: the repository is public, and this runs inside the autopilot, a session process that must never read a vault variable (tests/test_owner_vault.py). The unauthenticated limit (60 an hour)
    # is enough for the handful of delivering merges it reads; an exhausted limit is "unverifiable", the autopilot asks again on its next run.
    ok, detail = (read or ci.read_ci)(cfg.BACKLOG_GATE_GITHUB_REPO, cfg.BACKLOG_GATE_GITHUB_BRANCH, head, None)
    return ok, f"tests_pass: {detail}"


def probe_db(conn, kind: str, *args) -> tuple:
    if kind == "calc_share":
        src = (ROOT / "proyecto2/src/pipeline/run_pipeline.py").read_text(encoding="utf-8")
        calc = re.search(r'CALC_VERSION\s*(?::\s*str\s*)?=\s*["\'](\d+)["\']', src).group(1)
        # FND-0427: a family with an epoch override stores `<CALC_VERSION>.<override>` (proyecto2/src/utils/family_versions.family_token), so an exact match counted 11.6 % of rows on a universe that
        # was 99.5 % recomputed. Both forms are on the current CALC_VERSION.
        cur, total = conn.execute("SELECT COUNT(*) FILTER (WHERE algorithm_version = %s OR algorithm_version LIKE %s), COUNT(*) FROM fund_metrics", (calc, calc + ".%")).fetchone()
        share = cur / total if total else 0.0
        return share >= args[0], f"{share:.1%} of {total:,} fund_metrics rows are on CALC_VERSION {calc} (exact or family token {calc}.<epoch>; needs >= {args[0]:.0%})"
    if kind == "metric_absent":
        n = conn.execute("SELECT COUNT(*) FROM fund_metrics WHERE metric = %s", (args[0],)).fetchone()[0]
        return n == 0, f"{n} rows of metric {args[0]}"
    if kind == "ols_ran":
        n = conn.execute("SELECT COUNT(*) FROM p2_pipeline_log WHERE step = 'RUN_SUMMARY' AND message !~ 'ols_funds=0( |$)' AND message ~ 'ols_funds=[1-9]'").fetchone()[0]
        return n > 0, f"{n} P2 RUN_SUMMARY rows with ols_funds > 0"
    if kind == "audit_isins_clear":
        # args = (domain, isin, isin, ...): the most recent FINISHED run of that statistical-audit domain has no persisted violation for any of them. The persisted findings include the ones an
        # acceptance covers (audit_finding_isin is written before the acceptance is applied), so an accepted-but-still-violating fund is NOT clear: only a resolved one is (FND-0384).
        domain, isins = args[0], list(args[1:])
        if not isins:
            raise ValueError("audit_isins_clear needs a domain and at least one ISIN")
        run = conn.execute("SELECT run_id FROM audit_run WHERE domain = %s AND finished_at IS NOT NULL AND status = ANY(%s) ORDER BY started_at DESC LIMIT 1", (domain, list(AUDIT_FINISHED))).fetchone()
        if not run:
            return None, f"no finished audit run of domain {domain}: cannot tell"
        hit = [r[0] for r in conn.execute("SELECT DISTINCT isin FROM audit_finding_isin WHERE run_id = %s AND domain = %s AND isin = ANY(%s) AND rule_id <> ALL(%s) ORDER BY 1",
                                          (run[0], domain, isins, list(STRUCTURAL_MARKER_RULES))).fetchall()]
        if hit:
            return False, f"run {run[0]}: {len(hit)} of {len(isins)} fund(s) still have a persisted violation ({', '.join(hit[:8])})"
        # A fund held out of the audit snapshot carries only a structural marker row: it has no violation, but the audit did not evaluate it, so "clear" cannot be said (FND-0423). That is MANUAL, never
        # a pass and never the permanent FAIL it used to be; a claim about ONE rule is db_check audit_rule_clear.
        held_out = [r[0] for r in conn.execute("SELECT DISTINCT isin FROM audit_finding_isin WHERE run_id = %s AND domain = %s AND isin = ANY(%s) AND rule_id = ANY(%s) ORDER BY 1",
                                               (run[0], domain, isins, list(STRUCTURAL_MARKER_RULES))).fetchall()]
        if held_out:
            return None, (f"run {run[0]}: cannot tell, {len(held_out)} of {len(isins)} fund(s) are held out of the audit snapshot ({', '.join(held_out[:8])}) and were not evaluated; "
                          "state the claim for one rule with db_check audit_rule_clear")
        # Absence is not evidence when an acceptance can hide the fund: a rule that is only PARTLY accepted keeps just the unaccepted funds in its finding (accepted_residuals.apply_accepted), so
        # the accepted ones vanish from audit_finding_isin although they still violate (seen 2026-11-07: OC_NOT_CONTAMINATED, 1 new fund and 69 accepted). A rule that holds an active acceptance for
        # one of these funds and carries a non-INFO finding in the run therefore cannot say anything about them: unverifiable, never a pass.
        hidden = [r[0] for r in conn.execute(
            "SELECT DISTINCT a.rule_id FROM audit_accepted_finding a JOIN audit_finding f ON f.run_id = %s AND f.domain = a.domain AND f.rule_id = a.rule_id AND f.severity <> 'INFO' "
            "WHERE a.domain = %s AND a.isin = ANY(%s) AND a.expires_at >= CURRENT_DATE ORDER BY 1", (run[0], domain, isins)).fetchall()]
        if hidden:
            return None, f"run {run[0]}: cannot tell, rule(s) {', '.join(hidden)} hold an acceptance for these funds and have unaccepted violators, which hides the accepted ones"
        return True, f"run {run[0]}: all {len(isins)} fund(s) are clear"
    if kind == "db_check":
        return probe_db_check(conn, *args)
    raise ValueError(kind)


def probe_db_check(conn, *args) -> tuple:
    """`db_check` (FND-0423): one named check of the closed registry (validate_db_check). (True | False | None, text); a malformed condition is a ValueError, which evaluate() turns into a failed probe."""
    problems = validate_db_check(args)
    if problems:
        raise ValueError("; ".join(problems))
    name, p = args[0], list(args[1:])
    if name == "table_rows":
        table, op, n = p
        if table == "series_benchmark":
            value = conn.execute(Q_ROWS_SERIES_BENCHMARK).fetchone()[0]
        elif table == "nav_sources":
            value = conn.execute(Q_ROWS_NAV_SOURCES).fetchone()[0]
        else:                                                                  # nav_sources_older_180d: validate_db_check admits nothing else
            value = conn.execute(Q_ROWS_NAV_SOURCES_OLDER_180D).fetchone()[0]
        return compare(value, op, n), f"table {table}: {value} row(s) (needs {op} {n})"
    if name == "geography_without_source":
        value = conn.execute(Q_GEO_WITHOUT_SOURCE).fetchone()[0]
        return value == 0, f"{value} fund(s) with a Geography and no Geography_Source (needs 0)"
    if name == "geography_default_global":
        level = dict(zip(p[0::2], p[1::2]))
        found = {r[0]: r[1] for r in conn.execute(Q_GEO_DEFAULT_BY_NATURE).fetchall()}
        above = [f"{nature} {count} > {level.get(nature, 0)}" for nature, count in sorted(found.items()) if count > level.get(nature, 0)]
        return not above, f"default 'Global' by nature: {'above its level: ' + ', '.join(above) if above else 'none above its level'} ({sum(found.values())} in total)"
    if name == "ingestion_events":
        step, status, op, n, days = p
        value = conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE step = %s AND status = %s AND created_at >= now() - make_interval(days => %s)", (step, status, days)).fetchone()[0]
        return compare(value, op, n), f"{value} ingestion_log row(s) of step {step} status {status} in the last {days} d (needs {op} {n})"
    if name == "p2_run_summary":
        minimum, days = p
        value = conn.execute("SELECT COUNT(*) FROM p2_pipeline_log WHERE step = 'RUN_SUMMARY' AND created_at >= now() - make_interval(days => %s) "
                             "AND COALESCE(substring(message from 'processed=([0-9]+)')::int, -1) >= %s", (days, minimum)).fetchone()[0]
        return value > 0, f"{value} P2 RUN_SUMMARY row(s) in the last {days} d with processed >= {minimum}"
    # audit_rule_clear: the latest FINISHED run of the domain persists no violation of THIS rule for any of the funds. A held-out fund is not an obstacle here: the claim is about one rule, and the persisted
    # rows are per rule. An acceptance that hides a partly accepted rule makes it unverifiable (as in audit_isins_clear).
    domain, rule_id, isins = p[0], p[1], p[2:]
    run = conn.execute("SELECT run_id FROM audit_run WHERE domain = %s AND finished_at IS NOT NULL AND status = ANY(%s) ORDER BY started_at DESC LIMIT 1", (domain, list(AUDIT_FINISHED))).fetchone()
    if not run:
        return None, f"no finished audit run of domain {domain}: cannot tell"
    hit = [r[0] for r in conn.execute("SELECT DISTINCT isin FROM audit_finding_isin WHERE run_id = %s AND domain = %s AND rule_id = %s AND isin = ANY(%s) ORDER BY 1", (run[0], domain, rule_id, isins)).fetchall()]
    if hit:
        return False, f"run {run[0]}: {len(hit)} of {len(isins)} fund(s) still have a persisted violation of {rule_id} ({', '.join(hit[:8])})"
    hidden = conn.execute(
        "SELECT 1 FROM audit_accepted_finding a JOIN audit_finding f ON f.run_id = %s AND f.domain = a.domain AND f.rule_id = a.rule_id AND f.severity <> 'INFO' "
        "WHERE a.domain = %s AND a.rule_id = %s AND a.isin = ANY(%s) AND a.expires_at >= CURRENT_DATE LIMIT 1", (run[0], domain, rule_id, isins)).fetchone()
    if hidden:
        return None, f"run {run[0]}: cannot tell, {rule_id} holds an acceptance for these funds and has unaccepted violators, which hides the accepted ones"
    return True, f"run {run[0]}: none of the {len(isins)} fund(s) has a persisted violation of {rule_id}"


def probe_ticket_closed(tid: str, lookup=None) -> tuple:
    """Status of another ticket, read through shared.backlog_client (the single owner of backlog reads: the backlog is a SEPARATE database, so no
    SQL on it belongs in this module). 'LOOKUP_FAILED' (no DSN, outage) is unverifiable, never a pass: it returns MANUAL."""
    if lookup is None:
        sys.path.insert(0, str(ROOT))
        from shared.backlog_client import get_action_point_status as lookup
    st = lookup(tid)
    if st == "LOOKUP_FAILED":
        return None, f"{tid}: the backlog could not be read (no FONDOS_BACKLOG_PG_DSN or outage): check by hand"
    return st == "CLOSED", f"{tid} is {st}"


def evaluate(spec: dict = SPEC, evidence: dict = None) -> list:
    """[(ticket, verdict, results)] for the conditions in `spec`. `evidence` = {ticket: the sha of the merge that delivers it} (the autopilot's delivery facts), which `tests_pass` reads the CI of."""
    sys.path.insert(0, str(ROOT))
    from shared.env_guard import require_db_driver
    require_db_driver("rfd_matrix_probe.py")
    import shared.config  # noqa: F401  (loads .env)
    from shared.db import get_connection
    conn = get_connection()
    rows = []
    for tid, probes in spec.items():
        results = []
        for p in probes:
            kind, args = p[0], p[1:]
            try:
                if kind == "pushed":
                    results.append(probe_pushed(*args))
                elif kind == "flag":
                    results.append(probe_flag(*args))
                elif kind == "absent_in":
                    results.append(probe_absent_in(*args))
                elif kind == "absent_re_in":
                    results.append(probe_absent_re_in(*args))
                elif kind == "task_ok":
                    results.append(probe_task_ok(*args))
                elif kind == "ticket_closed":
                    results.append(probe_ticket_closed(*args))
                elif kind == "tests_pass":
                    results.append(probe_tests_pass(tid, *args, evidence=evidence))
                elif kind == "manual":
                    results.append((None, args[0]))
                else:
                    results.append(probe_db(conn, kind, *args))
            except Exception as exc:                       # a probe that cannot run is a failed probe, never a pass
                results.append((False, f"{kind}{args}: probe failed ({type(exc).__name__}: {str(exc)[:100]})"))
        rows.append((tid, verdict(results), results))
    conn.rollback()
    conn.close()
    return rows


def seed(apply: bool) -> int:
    """Copy the SPEC conditions of tickets still READY_FOR_DEPLOY into backlog.close_condition (append-only; needs FONDOS_ACTOR). Dry run by default."""
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads .env)
    from shared import backlog_client as bc
    stored = bc.close_conditions()
    if stored is None:
        print("backlog.close_condition is not applied (owner: python scripts/mig/backlog/migrate_backlog_v2.py --phase sync).", file=sys.stderr)
        return 2
    to_add, skipped = seed_rows(SPEC, {t["action_point_id"]: t["status"] for t in bc.list_tickets()}, stored)
    for tid, why in skipped:
        print(f"skip {tid}: {why}")
    for tid, kind, args in to_add:
        print(f"{'add' if apply else 'would add'} {tid}: {kind}{tuple(args)}")
        if apply:
            bc.add_close_condition(tid, kind, args, "seeded from rfd_matrix_probe.SPEC (FND-0334)")
    print(f"\n{len(to_add)} condition(s) {'added' if apply else 'to add (DRY RUN: --apply writes them)'}, {len(skipped)} ticket(s) skipped.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--md", help="also write the matrix as markdown to this file")
    ap.add_argument("--close-ready", action="store_true", help="list only the tickets whose every probe passes (closing them stays a decision)")
    ap.add_argument("--seed", action="store_true", help="copy SPEC into backlog.close_condition for tickets still READY_FOR_DEPLOY (dry run)")
    ap.add_argument("--apply", action="store_true", help="with --seed: write the conditions")
    args = ap.parse_args(argv)
    if args.seed:
        return seed(args.apply)
    sys.path.insert(0, str(ROOT))
    try:
        import shared.config  # noqa: F401  (loads .env)
        from shared import backlog_client as bc
        stored = bc.close_conditions()
    except Exception:                                        # the backlog is another database: without it the SPEC dictionary still works
        stored = None
    spec, source = choose_spec(stored)
    print(f"closing conditions: {source}")
    rows = evaluate(spec)
    if args.close_ready:
        rows = [r for r in rows if r[1] == "READY"]
    md = render_markdown(rows)
    print(md)
    counts = {v: sum(1 for _, x, _ in rows if x == v) for v in ("READY", "MANUAL", "BLOCKED")}
    print(f"\n{counts}  (nothing was closed: closing a ticket is a separate, per-ticket decision)")
    if args.md:
        Path(args.md).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
