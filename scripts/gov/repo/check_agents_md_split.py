#!/usr/bin/env python
"""
check_agents_md_split.py - prove that splitting a document lost no line (FND-0312, read-only).

Takes every non-blank line that the source file lost between a git ref and the working tree (counted with
multiplicity, whitespace-trimmed only) and requires each one to be either
  (a) present verbatim in one of the explicit --receivers files (never "any .md": a dropped line must not pass
      because it happens to exist somewhere else), or
  (b) listed in the --ledger file with the reason / canonical home that already holds it.
Each receiver line and each ledger line covers one occurrence. Markup-only lines (table separators, `---`, HTML comments
such as sentinel markers) are counted and listed separately, never silently skipped, and never fail the check.
A plain line-count comparison is not used: counts can match while content is lost.

Ledger format (UTF-8): one entry per line, `<exact line> ||| <reason or canonical home>`; blank lines and `#` lines that do not
contain the ` ||| ` separator are comments (an entry may itself start with `#`, e.g. a markdown heading).

Usage:
  python scripts/audit/check_agents_md_split.py --base HEAD --receivers proyecto1/AGENTS.md proyecto2/AGENTS.md \
         --ledger scripts/audit/agents_md_split_ledger.txt [--source AGENTS.md] [--repo DIR] [--verbose]
Exit: 0 = every removed line accounted for, 1 = unaccounted lines (printed), 2 = usage / git / file error.
"""
import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER_SEP = " ||| "
_MARKUP = re.compile(r"^(?:[-|:\s]+|<!--.*-->)$")


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _git_show(repo: Path, ref: str, path: str) -> str:
    proc = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise FileNotFoundError(f"git show {ref}:{path} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8")


def load_ledger(path: Path) -> Counter:
    entries: Counter = Counter()
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        # A comment is a blank line or a `#` line WITHOUT the separator: an entry may itself start with `#` (a markdown heading).
        if not raw.strip() or (raw.lstrip().startswith("#") and LEDGER_SEP not in raw):
            continue
        line, sep, reason = raw.rpartition(LEDGER_SEP)
        if not sep or not line.strip() or not reason.strip():
            raise ValueError(f"{path}:{n}: ledger entry must be '<line>{LEDGER_SEP}<reason>'")
        entries[line.strip()] += 1
    return entries


def check(repo: Path, base: str, source: str, receivers: list[str], ledger: Path | None = None) -> dict:
    old = Counter(_lines(_git_show(repo, base, source)))
    new = Counter(_lines((repo / source).read_text(encoding="utf-8")))
    removed = old - new                                  # positive part only: multiplicity-aware
    pool: Counter = Counter()
    for rel in receivers:
        pool.update(_lines((repo / rel).read_text(encoding="utf-8")))
    led = load_ledger(ledger) if ledger else Counter()

    moved = ledgered = 0
    markup: list[tuple[str, int]] = []
    unaccounted: list[tuple[str, int]] = []
    for line, n in sorted(removed.items()):
        if _MARKUP.match(line):
            markup.append((line, n))
            continue
        hit = min(n, pool[line])
        pool[line] -= hit
        moved += hit
        rest = n - hit
        hit = min(rest, led[line])
        led[line] -= hit
        ledgered += hit
        rest -= hit
        if rest:
            unaccounted.append((line, rest))
    return {
        "removed": sum(n for ln, n in removed.items() if not _MARKUP.match(ln)),
        "moved": moved, "ledgered": ledgered, "markup": markup, "unaccounted": unaccounted,
        "unused_ledger": sorted(ln for ln, c in led.items() if c > 0),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="HEAD", help="git ref holding the original source (default HEAD)")
    ap.add_argument("--source", default="AGENTS.md", help="file that was split, relative to --repo")
    ap.add_argument("--receivers", nargs="*", default=[], help="files that must hold the moved lines, relative to --repo")
    ap.add_argument("--ledger", type=Path, help="file listing lines deliberately removed (dedupe / dropped), with the reason")
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--verbose", action="store_true", help="list every markup-only line instead of the first 20")
    a = ap.parse_args(argv)
    try:
        r = check(a.repo, a.base, a.source, a.receivers, a.ledger)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"removed from {a.source} since {a.base} (non-blank, with multiplicity, markup-only excluded): {r['removed']}")
    print(f"  found in receivers: {r['moved']} | in ledger: {r['ledgered']} | unaccounted: {sum(n for _, n in r['unaccounted'])}")
    mk = r["markup"] if a.verbose else r["markup"][:20]
    print(f"  markup-only lines listed, not verified: {sum(n for _, n in r['markup'])} ({len(r['markup'])} distinct)")
    for line, n in mk:
        print(f"    [markup x{n}] {line}")
    for line in r["unused_ledger"]:
        print(f"  WARNING unused ledger entry (matches no removed line): {line}")
    for line, n in r["unaccounted"]:
        print(f"  UNACCOUNTED x{n}: {line}", file=sys.stderr)
    if r["unaccounted"]:
        print("FAIL: lines were lost; move them to a receiver or add them to the ledger with a reason.", file=sys.stderr)
        return 1
    print("OK: every removed line is accounted for.")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):    # a redirected Windows stream is cp1252: a line with an arrow or a dash would crash the report
        _stream.reconfigure(encoding="utf-8")
    sys.exit(main())
