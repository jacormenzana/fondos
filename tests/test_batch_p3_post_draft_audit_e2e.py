"""
tests/test_batch_p3_post_draft_audit_e2e.py -- P3_buildPortfolio.bat persists a P2 audit AFTER the DRAFT is built (Step A, FND-0307).

Runs the REAL launchers against the simulated tool tree of tests/test_batch_launchers_e2e.py (NORMAS_BATCH.md section 12): no database, no
network, never the real repo. Every path asserted here is one the launcher takes; what is NOT covered is listed at the bottom.
The audit step supplies time ordering only: the promotion gate and its policy are untouched (Step B, FND-0306, is a separate ticket).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_batch_launchers_e2e as e2e  # noqa: E402  (the stub tree and its conventions, one definition)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the launchers are cmd.exe batch files")

# The audit tool fails ONLY for the persisted run made after the DRAFT build (the trace already holds p3_build_portfolio): the earlier,
# report-mode and baseline audits of a cycle keep succeeding, which is exactly the case the launcher has to survive.
_AUDIT_STUB = '''import os, sys
_args = sys.argv[1:]
_trace = open(os.environ["STUB_TRACE"]).read() if os.path.exists(os.environ["STUB_TRACE"]) else ""
if os.environ.get("STUB_FAIL_POST_BUILD_AUDIT") and "--persist" in _args and "p3_build_portfolio" in _trace:
    with open(os.environ["STUB_TRACE"], "a") as f:
        f.write("run_statistical_audit %s\\n" % " ".join(_args))
    sys.exit(int(os.environ["STUB_FAIL_POST_BUILD_AUDIT"]))
''' + e2e._PY_STUB

MARK = "[AUDIT_P2_POST_DRAFT]"


@pytest.fixture()
def tree(tmp_path):
    t = e2e.Tree(tmp_path)
    (t.root / "scripts" / "audit" / "run_statistical_audit.py").write_text(_AUDIT_STUB)
    return t


def _idx(lines, prefix, after=0):
    return next(i for i, ln in enumerate(lines) if i >= after and ln.startswith(prefix))


def _post_build_audits(lines):
    """Persisted audit lines that come after the DRAFT build."""
    b = _idx(lines, "p3_build_portfolio")
    return [ln for ln in lines[b + 1:] if ln.startswith("run_statistical_audit") and "--persist" in ln]


def _markers(r):
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith(MARK)]


# ---------------------------------------------------------------- the standalone launcher

def test_standalone_build_persists_a_p2_audit_after_the_draft_with_a_post_run_id(tree):
    r = tree.run("P3_buildPortfolio")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    audits = _post_build_audits(ln)
    assert len(audits) == 1, ln
    a = audits[0]
    assert "--domain p2" in a and "--mode report" in a and "--persist" in a
    run_id = a.split("--run-id ")[1].split()[0]
    assert run_id.startswith("post_") and run_id.endswith("_p2") and len(run_id) > len("post__p2"), run_id
    assert _markers(r) == [f"{MARK} OK run-id={run_id}"]
    assert (tree.root / "proyecto3" / "log").glob("log_P3_auditP2_*.log")        # the audit has its own log, outside the build log


def test_the_run_id_is_unique_per_run(tree):
    ids = []
    for _ in range(2):
        tree.run("P3_buildPortfolio")
        ids.append(_post_build_audits(tree.lines())[-1].split("--run-id ")[1].split()[0])
        import time
        time.sleep(1.1)                                                            # the stamp has second resolution
    assert ids[0] != ids[1], ids


def test_no_audit_skips_the_step_and_is_not_forwarded_to_the_build(tree):
    r = tree.run("P3_buildPortfolio", "--no-audit")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "run_statistical_audit" not in tree.tools()
    assert _markers(r) == [f"{MARK} SKIPPED --no-audit"]
    build = next(x for x in tree.lines() if x.startswith("p3_build_portfolio"))
    assert "--no-audit" not in build, build                                        # the Python CLI would take it for a scenario id


def test_no_audit_in_any_position_keeps_the_other_arguments_for_the_build(tree):
    r = tree.run("P3_buildPortfolio", "mi_escenario", "--no-audit", "--allow-stale")
    assert r.returncode == 0, r.stdout + r.stderr
    build = next(x for x in tree.lines() if x.startswith("p3_build_portfolio"))
    assert build.split()[1:] == ["mi_escenario", "--allow-stale"], build
    assert "run_statistical_audit" not in tree.tools()


def test_dry_run_builds_nothing_so_no_audit_is_persisted(tree):
    r = tree.run("P3_buildPortfolio", "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "run_statistical_audit" not in tree.tools()
    assert "--dry-run" in next(x for x in tree.lines() if x.startswith("p3_build_portfolio"))
    assert _markers(r) and _markers(r)[0].startswith(f"{MARK} SKIPPED --dry-run")


def test_a_failed_build_keeps_its_rc_and_persists_no_audit(tree):
    r = tree.run("P3_buildPortfolio", STUB_RC_P3_BUILD_PORTFOLIO="2")
    assert r.returncode == 2
    assert "run_statistical_audit" not in tree.tools()
    assert _markers(r) and "RC=2" in _markers(r)[0] and _markers(r)[0].startswith(f"{MARK} SKIPPED")


@pytest.mark.parametrize("audit_rc", ["2", "7"])           # 2 = INCOMPLETE in report mode, anything else = the tool crashed
def test_an_audit_failure_is_reported_loudly_keeps_p3_rc_and_is_not_evidence(tree, audit_rc):
    r = tree.run("P3_buildPortfolio", STUB_FAIL_POST_BUILD_AUDIT=audit_rc)
    assert r.returncode == 0, r.stdout + r.stderr                                  # P3 exit-code contract unchanged
    m = _markers(r)
    assert len(m) == 1 and m[0].startswith(f"{MARK} FAILED rc={audit_rc} run-id=post_"), m
    assert "NO hay evidencia de promocion valida" in r.stdout
    assert "OK" not in m[0]
    log = next((tree.root / "proyecto3" / "log").glob("log_P3_buildPortfolio_*.log")).read_text(errors="replace")
    assert f"{MARK} FAILED rc={audit_rc}" in log                                   # also in the build log, not only on the console


def test_a_stale_inputs_build_still_returns_2_and_an_audit_failure_does_not_mask_a_build_failure(tree):
    r = tree.run("P3_buildPortfolio", STUB_RC_P3_BUILD_PORTFOLIO="2", STUB_FAIL_POST_BUILD_AUDIT="7")
    assert r.returncode == 2 and "run_statistical_audit" not in tree.tools()


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_still_runs_nothing_and_documents_the_new_option(tree, flag):
    r = tree.run("P3_buildPortfolio", flag)
    assert r.returncode == 0 and tree.tools() == []
    assert "--no-audit" in r.stdout and "post_<sello>_p2" in r.stdout


# ---------------------------------------------------------------- every cycle path that builds a DRAFT

def test_p2_p3_complete_persists_the_audit_after_the_build_and_before_the_report(tree):
    r = tree.run("P2_P3_complete", "--no-pause")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    b = _idx(ln, "p3_build_portfolio")
    assert len(_post_build_audits(ln)) == 1
    a = _idx(ln, "run_statistical_audit", after=b)
    g = _idx(ln, "generate_report", after=b)
    assert b < a < g, ln                                                           # evidence is newer than the DRAFT, then the report
    assert not any("--persist" in x for x in ln[:b] if x.startswith("run_statistical_audit"))   # P2's own audit stays report-only
    assert len(_markers(r)) == 1 and "OK" in _markers(r)[0]


def test_p2_p3_complete_survives_a_failing_post_draft_audit(tree):
    r = tree.run("P2_P3_complete", "--no-pause", STUB_FAIL_POST_BUILD_AUDIT="2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "generate_report" in tree.tools()                                       # the cycle went on to the report
    assert len(_markers(r)) == 1 and "FAILED rc=2" in _markers(r)[0]


def test_p1_p2_p3_persists_the_audit_after_the_build_and_before_the_report(tree):
    r = tree.run("P1_P2_P3", *e2e.P123)
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    b = _idx(ln, "p3_build_portfolio")
    a = _idx(ln, "run_statistical_audit", after=b)
    g = _idx(ln, "generate_report", after=b)
    assert b < a < g and len(_post_build_audits(ln)) == 1, ln
    assert len(_markers(r)) == 1 and "OK" in _markers(r)[0]


def test_p1_p2_p3_with_p3_dry_run_persists_nothing_after_the_build(tree):
    r = tree.run("P1_P2_P3", *e2e.P123, "--p3-dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _post_build_audits(tree.lines()) == []
    assert _markers(r) and _markers(r)[0].startswith(f"{MARK} SKIPPED --dry-run")


def test_p1_p2_p3_survives_a_failing_post_draft_audit(tree):
    r = tree.run("P1_P2_P3", *e2e.P123, STUB_FAIL_POST_BUILD_AUDIT="7")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "generate_report" in tree.tools()
    assert len(_markers(r)) == 1 and "FAILED rc=7" in _markers(r)[0]


def test_p1_p2_p3_does_not_reach_the_audit_when_the_build_fails(tree):
    r = tree.run("P1_P2_P3", *e2e.P123, STUB_RC_P3_BUILD_PORTFOLIO="2")
    assert r.returncode == 2, r.stdout + r.stderr
    assert _post_build_audits(tree.lines()) == []


# NOT covered here (stated, not claimed): the real AUDIT_P2.bat against a real database and the real `control.audit_run` write (the
# persistence itself is tested in the statistical-audit suites); the opt-out THROUGH the cycle launchers (P2_P3_complete and P1_P2_P3
# have no option that forwards --no-audit: it is available on P3_buildPortfolio.bat only); the costs-domain audit (the cost gate has
# its own evidence); the promotion gate, which this step does not change.
