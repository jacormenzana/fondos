"""
tests/test_report_dirs.py — guards for the per-project report output directories (FND-0270).

Report files a person opens now live under out/reports/<project>: p1_export_*.xlsx -> p1, p2_metricas_*.xlsx -> p2,
informe_cartera_*.xlsx -> p3 (drafts under p3/borradores). `shared.config.REPORT_DIRS` is the single source; the
generators and the launchers must not carry their own copy of the path. Previously config.REPORTS_DIR said out/reports
while P3_generateReport.bat forced out\\export, so the official report landed in a different place than the module default.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import shared.config as cfg

_ROOT = Path(__file__).resolve().parent.parent
_BS = chr(92)
_LAUNCH = _ROOT / "scripts" / "launch"


def test_registry_shape():
    assert set(cfg.REPORT_DIRS) == {"p1", "p2", "p3"}
    for ctx, d in cfg.REPORT_DIRS.items():
        assert d == cfg.REPORTS_DIR / ctx
    assert cfg.REPORTS_DIR == _ROOT / "out" / "reports"
    assert cfg.METRICS_DIR == cfg.REPORT_DIRS["p2"]                 # compat alias


def test_generators_default_to_the_registry():
    from proyecto1.src.analysis import export_p1
    assert export_p1.EXPORT_DIR == cfg.REPORT_DIRS["p1"]
    src = (_ROOT / "proyecto2" / "src" / "analysis" / "export_metrics.py").read_text(encoding="utf-8")
    assert 'default=str(REPORT_DIRS["p2"])' in src
    src = (_ROOT / "proyecto3" / "src" / "monthly_report.py").read_text(encoding="utf-8")
    assert '_DEFAULT_REPORTS_DIR = REPORT_DIRS["p3"]' in src


def test_p3_launcher_default_is_the_p3_dir():
    text = (_LAUNCH / "P3_generateReport.bat").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'set "OUT_DIR=%ROOT%' + re.escape(_BS) + r'([^"]+)"', text)
    assert m, "OUT_DIR default not found"
    assert _ROOT.joinpath(*m.group(1).split(_BS)) == cfg.REPORT_DIRS["p3"]


_OLD = re.compile(r"out[/\\](export|metrics)\b")
_TEXT = {".py", ".bat", ".ps1", ".sh", ".md"}
_SKIP_DIRS = {"doc/backlog", "doc/analisis", "doc/memoria", "doc/instrucciones", "doc/especificacion", "upload", "prev", "log"}


def test_no_live_file_names_the_old_report_dirs():
    out = subprocess.run(["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
    offenders = []
    for rel in out:
        p = _ROOT / rel
        if p.suffix.lower() not in _TEXT or rel == "tests/test_report_dirs.py":
            continue
        if any(rel.startswith(d + "/") or ("/" + d + "/") in ("/" + rel) for d in _SKIP_DIRS):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _OLD.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:110]}")
    assert not offenders, "old report dir still referenced (use REPORT_DIRS):\n" + "\n".join(offenders)
