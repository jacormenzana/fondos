#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_ingest_pending_logs.py -- ingest PENDING_FND_LOGS.md into gestion.backlog through the allow-listed agent note path (FND-0354, owner order 2026-10-29/30).

    python scripts/ops/backlog_ingest_pending_logs.py                      # DRY RUN: parse, check the tickets, print the plan; writes nothing
    python scripts/ops/backlog_ingest_pending_logs.py --apply              # append what is not there yet, then write the record

The file is parsed STRICTLY (a heading `### FND-nnnn / nn - title`, then the lines `actor:`, `date:`, `source:`, a blank line, the text); anything malformed makes the whole run
fail before a single note is written. Each note is appended with `shared.backlog_client.append_log` (an existing ticket, an agent identity: the one operation a non-interactive
process may write with) and starts with a marker `[PENDING_FND_LOGS FND-nnnn/nn]`; a note whose marker is already in the ticket's logs is skipped, so a second run, or a run after a
crash half-way, appends only what is missing (exactly once). The ticket must exist. The record (what was appended, with the log ids) is written to --record-dir (default `deployments/`,
version-controlled); removing PENDING_FND_LOGS.md after a complete ingestion is a separate, reviewed change. Exit 0 = nothing left to ingest, 1 = a failure, 2 = usage.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILE = ROOT / "PENDING_FND_LOGS.md"
DEFAULT_RECORD_DIR = ROOT / "deployments"
RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2
HEADING = re.compile(r"^### (FND-\d{4}) / (\d{2}) - (\S.*)$")
ACTOR = re.compile(r"^(agent|system):[A-Za-z0-9._-]+$")
FIELDS = ("actor", "date", "source")
MAX_BODY = 20000


class PendingLogsError(ValueError):
    """The file does not follow the format; nothing is ingested."""


@dataclass(frozen=True)
class Entry:
    ticket: str
    seq: str
    title: str
    actor: str
    date: str
    source: str
    body: str

    @property
    def marker(self) -> str:
        return f"[PENDING_FND_LOGS {self.ticket}/{self.seq}]"

    @property
    def text(self) -> str:
        return f"{self.marker} {self.title} (decision date {self.date}; source: {self.source})\n\n{self.body}"


def parse(text: str) -> list:
    """Strict parser. Raises PendingLogsError listing every problem found."""
    lines = text.replace("\r\n", "\n").split("\n")
    problems, entries, seen = [], [], set()
    i = 0
    while i < len(lines):
        m = HEADING.match(lines[i])
        if not m:
            if lines[i].startswith("### "):
                problems.append(f"line {i + 1}: a heading that does not follow `### FND-nnnn / nn - title`: {lines[i][:60]!r}")
            i += 1
            continue
        ticket, seq, title = m.group(1), m.group(2), m.group(3).strip()
        start, fields = i, {}
        i += 1
        for name in FIELDS:
            if i < len(lines) and lines[i].startswith(f"{name}: ") and lines[i][len(name) + 2:].strip():
                fields[name] = lines[i][len(name) + 2:].strip()
                i += 1
            else:
                problems.append(f"line {i + 1}: expected `{name}: ...` after the heading of {ticket}/{seq}")
                break
        if len(fields) < len(FIELDS):
            continue
        if i >= len(lines) or lines[i].strip():
            problems.append(f"line {i + 1}: expected a blank line after the fields of {ticket}/{seq}")
            continue
        i += 1
        body_start = i
        while i < len(lines) and not lines[i].startswith("## ") and not lines[i].startswith("### "):
            i += 1
        body = "\n".join(lines[body_start:i]).strip()
        if not ACTOR.match(fields["actor"]):
            problems.append(f"line {start + 1}: actor {fields['actor']!r} of {ticket}/{seq} is not agent:<model> or system:<name>")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", fields["date"]):
            problems.append(f"line {start + 1}: date {fields['date']!r} of {ticket}/{seq} is not YYYY-MM-DD")
        if not body or len(body) > MAX_BODY:
            problems.append(f"line {start + 1}: the text of {ticket}/{seq} is empty or longer than {MAX_BODY} characters")
        if (ticket, seq) in seen:
            problems.append(f"line {start + 1}: {ticket}/{seq} appears twice")
        seen.add((ticket, seq))
        entries.append(Entry(ticket, seq, title, fields["actor"], fields["date"], fields["source"], body))
    if not entries and not problems:
        problems.append("no entry found")
    if problems:
        raise PendingLogsError("; ".join(problems))
    return entries


def plan(entries: list, ticket_ids: set, existing_comments: dict) -> list:
    """[(entry, status)] with status MISSING_TICKET, ALREADY or TO_APPEND. `existing_comments` maps a ticket id to the list of its log texts. Pure."""
    out = []
    for e in entries:
        if e.ticket not in ticket_ids:
            out.append((e, "MISSING_TICKET"))
        elif any(c.startswith(e.marker) for c in existing_comments.get(e.ticket, [])):
            out.append((e, "ALREADY"))
        else:
            out.append((e, "TO_APPEND"))
    return out


def ingest(entries: list, *, ticket_ids: set, existing_comments: dict, append, apply: bool) -> list:
    """Run the plan; with apply, append every TO_APPEND entry through `append(ticket, actor, text) -> log_id` and stop at the first failure (what was appended is recorded).
    Returns [(entry, status, log_id)]; status APPENDED, FAILED:<reason>, or the planned one."""
    results = []
    for e, status in plan(entries, ticket_ids, existing_comments):
        if status == "TO_APPEND" and apply:
            try:
                results.append((e, "APPENDED", append(e.ticket, e.actor, e.text)))
            except Exception as exc:  # noqa: BLE001 -- recorded, then the run stops: the next run resumes from here
                results.append((e, f"FAILED:{type(exc).__name__}: {exc}"[:200], None))
                break
        else:
            results.append((e, status, None))
    return results


def render(results: list, header: str, apply: bool) -> str:
    lines = [header]
    lines += [f"{status:15s} {e.ticket}/{e.seq} {e.title[:70]}" + (f" -> log {log_id}" if log_id else "") for e, status, log_id in results]
    pending = [s for _, s, _ in results if s in ("TO_APPEND", "MISSING_TICKET") or s.startswith("FAILED")]
    lines.append(f"RESULT: {'COMPLETE: nothing left to ingest' if not pending else ('INCOMPLETE: ' + str(len(pending)) + ' not ingested')}" + ("" if apply else " (dry run: nothing was written)"))
    return "\n".join(lines)


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--apply", action="store_true", help="append the missing notes (default: dry run)")
    ap.add_argument("--record-dir", default=str(DEFAULT_RECORD_DIR), help="where the record of an --apply run is written ('' to skip)")
    args = ap.parse_args(argv)
    path = Path(args.file)
    if not path.is_file():
        print(f"error: {path} not found", file=sys.stderr)
        return RC_USAGE
    try:
        entries = parse(path.read_text(encoding="utf-8"))
    except PendingLogsError as exc:
        print(f"error: {path.name} does not follow its format, nothing was ingested: {exc}", file=sys.stderr)
        return RC_FAIL

    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads the repo .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    try:
        ticket_ids = {t["action_point_id"] for t in bc.list_tickets(ids=sorted({e.ticket for e in entries}))}
        existing: dict = {}
        for row in bc.list_logs(ids=sorted(ticket_ids)):
            existing.setdefault(row["action_point_id"], []).append(row["comment"])
        results = ingest(entries, ticket_ids=ticket_ids, existing_comments=existing, append=lambda t, a, c: bc.append_log(t, a, c), apply=args.apply)
    except bc.BacklogUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    text = render(results, f"PENDING_FND_LOGS ingestion, {stamp}, {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} in {path.name}", args.apply)
    print(text)
    if args.apply and args.record_dir:
        out = Path(args.record_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}_pending_logs_ingestion_{stamp}.txt").write_text(text + "\n", encoding="utf-8")
    return RC_OK if text.splitlines()[-1].startswith("RESULT: COMPLETE") else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
