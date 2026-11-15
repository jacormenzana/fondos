"""FND-0422: a function-local import that rebinds a name the function already uses EARLIER makes that name a local of the whole function,
so the earlier use raises UnboundLocalError. In run_block it was swallowed by two `except` handlers and every pass classified without the
Morningstar class and the realized-volatility band. Static (AST) check, no import of pipeline (R-7)."""
import ast
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "core" / "pipeline.py"


def _shadowing_imports(tree: ast.AST) -> list[str]:
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound: dict[str, int] = {}
        uses: dict[str, list[int]] = {}
        for node in ast.walk(fn):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    name = (a.asname or a.name).split(".")[0]
                    bound[name] = min(bound.get(name, node.lineno), node.lineno)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                uses.setdefault(node.id, []).append(node.lineno)
        for name, line in bound.items():
            early = [u for u in uses.get(name, []) if u < line]
            if early:
                bad.append(f"{fn.name}: '{name}' imported locally at line {line} but used at line {early[0]} before it")
    return bad


def test_pipeline_has_no_local_import_that_shadows_an_earlier_use():
    assert _shadowing_imports(ast.parse(PIPELINE.read_text(encoding="utf-8"))) == []


def test_the_check_detects_the_fnd_0422_pattern():
    src = "from m import f\n\ndef g():\n    with f():\n        pass\n    if x:\n        from m import f\n"
    assert _shadowing_imports(ast.parse(src)) == ["g: 'f' imported locally at line 7 but used at line 4 before it"]
