# -*- coding: utf-8 -*-
"""
p1_kiid_sync.py  —  KIID delta, download & lifecycle tracking
==============================================================
SPEC-KIID-001-rev2  (2026-07-17)

    python p1_kiid_sync.py                   # Phase 4: dry-run (DEFAULT)
    python p1_kiid_sync.py --dry-run         # Phase 4: explicit dry-run
    python p1_kiid_sync.py --sync            # Phase 5: download net-new KIIDs
    python p1_kiid_sync.py --sync --limit 10 # smoke test
    python p1_kiid_sync.py --retire-orphans  # move stale KIIDs to kiid_retired/YYYYMMDD/
    python p1_kiid_sync.py --backfill-lifecycle  # one-time: populate kiid_lifecycle from disk

Design law (§6.1): every download uses a CAPTURED href.  Zero URLs are string-built.
Validation (§7 Phase 5): HTTP 200 does NOT guarantee a PDF — the endpoint returns
200 with an HTML error body on failure.  Must check Content-Type + %PDF + size > 10 KB.

kiid_lifecycle table (one row per lifecycle period per ISIN):
    PK (isin, start_date) — multiple rows per ISIN if a fund cycles in/out
    status: 'commercializing' | 'retired'
    Retired PDFs are never deleted; kiid_retired/YYYYMMDD/ is the historical archive.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_HARVEST_DIR   = Path(__file__).resolve().parent        # proyecto1/harvest/
_PROYECTO1_DIR = _HARVEST_DIR.parent                    # proyecto1/
_ROOT          = _PROYECTO1_DIR.parent                  # repo root
sys.path.insert(0, str(_ROOT))
from shared.config import DB_PATH  # noqa: E402
from shared.db import get_connection, is_postgres_connection  # noqa: E402
import sqlite3

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAGE_URL = (
    "https://www.deutsche-bank.es/es/particulares/ahorro-inversion/"
    "productos/documentacion-legal.html"
)
KIID_DIR          = Path(r"C:\data\fondos\kiid")
KIID_RETIRED_BASE = Path(r"C:\data\fondos\kiid_retired")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": PAGE_URL,
}

RATE_LIMIT_S  = 1.0         # >= 1.0 s between downloads (§7 Phase 5)
TIMEOUT_S     = 30
MIN_PDF_BYTES = 10 * 1024   # 10 KB

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("p1_kiid_sync")


# ---------------------------------------------------------------------------
# kiid_lifecycle — DDL + helpers
# ---------------------------------------------------------------------------
DDL_LIFECYCLE = """
CREATE TABLE IF NOT EXISTS kiid_lifecycle (
    isin        TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT,
    status      TEXT NOT NULL DEFAULT 'commercializing',
    href        TEXT,
    retire_dir  TEXT,
    PRIMARY KEY (isin, start_date)
);
CREATE INDEX IF NOT EXISTS ix_lc_status ON kiid_lifecycle(status);
CREATE INDEX IF NOT EXISTS ix_lc_isin   ON kiid_lifecycle(isin);
"""


def _ensure_lifecycle_table(conn) -> None:
    # Postgres migration Phase 5c (2026-09-20): silver.kiid_lifecycle is already provisioned by
    # db/pg/20_silver.sql (applied once via psql) — not by this script's inline DDL, which is
    # SQLite-only (conn.executescript() doesn't exist on psycopg3 either way). Same architecture
    # as p1_db_harvest.py's DDL guard / sqlite_writer.create_schema(): Postgres schema creation is
    # external, not autotranslated at runtime.
    if is_postgres_connection(conn):
        return
    conn.executescript(DDL_LIFECYCLE)
    conn.commit()


def _lifecycle_activate(conn, isin: str, href: str, start_date: str) -> None:
    """Insert a new active lifecycle period. Idempotent: INSERT OR IGNORE on same (isin, start_date)."""
    if is_postgres_connection(conn):
        conn.execute("""
            INSERT INTO kiid_lifecycle (isin, start_date, end_date, status, href, retire_dir)
            VALUES (%s, %s, NULL, 'commercializing', %s, NULL)
            ON CONFLICT (isin, start_date) DO NOTHING
        """, (isin, start_date, href))
    else:
        conn.execute("""
            INSERT OR IGNORE INTO kiid_lifecycle (isin, start_date, end_date, status, href, retire_dir)
            VALUES (?, ?, NULL, 'commercializing', ?, NULL)
        """, (isin, start_date, href))


def _lifecycle_retire(conn, isin: str, end_date: str, retire_dir: str) -> None:
    """
    Mark the current active period as retired.
    If no active row exists (file predates the lifecycle table), insert a retired row
    using end_date as an approximated start_date.
    """
    pg = is_postgres_connection(conn)
    ph = "%s" if pg else "?"
    updated = conn.execute(f"""
        UPDATE kiid_lifecycle
        SET status = 'retired', end_date = {ph}, retire_dir = {ph}
        WHERE isin = {ph} AND end_date IS NULL
    """, (end_date, retire_dir, isin)).rowcount
    if updated == 0:
        if pg:
            conn.execute("""
                INSERT INTO kiid_lifecycle
                    (isin, start_date, end_date, status, href, retire_dir)
                VALUES (%s, %s, %s, 'retired', NULL, %s)
                ON CONFLICT (isin, start_date) DO NOTHING
            """, (isin, end_date, end_date, retire_dir))
        else:
            conn.execute("""
                INSERT OR IGNORE INTO kiid_lifecycle
                    (isin, start_date, end_date, status, href, retire_dir)
                VALUES (?, ?, ?, 'retired', NULL, ?)
            """, (isin, end_date, end_date, retire_dir))


# ---------------------------------------------------------------------------
# PHASE 4 — KIID Delta
# ---------------------------------------------------------------------------
def compute_delta(conn=None, backend=None) -> dict:
    """
    Returns:
      target   : {isin: href}  — from db_document_catalogue WHERE codSus='KIID'
      local    : set of ISINs with a {ISIN}.pdf in KIID_DIR
      missing  : ISINs in target but not local (to download)
      present  : ISINs in both
      orphans  : ISINs local but not in target (reported, never deleted here)

    conn: injected connection (Postgres or SQLite) — used by tests and any future dialect-aware
    caller. When None (the CLI's default), opens (and closes) its own short-lived connection via
    get_connection(backend=backend) (migration addendum, Stage 5, 2026-09-20). No placeholder
    translation needed — this query has none.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection(DB_PATH, backend=backend)
    rows = conn.execute("""
        SELECT DISTINCT isin, href
        FROM db_document_catalogue
        WHERE cod_sus = 'KIID'
          AND isin IS NOT NULL
          AND isin != ''
          AND harvest_ts = (SELECT MAX(harvest_ts) FROM db_document_catalogue)
    """).fetchall()
    if own_conn:
        conn.close()

    target     = {r[0]: r[1] for r in rows}
    local      = {p.stem for p in KIID_DIR.glob("*.pdf")} if KIID_DIR.exists() else set()
    target_set = set(target.keys())

    return {
        "target":  target,
        "local":   local,
        "missing": target_set - local,
        "present": target_set & local,
        "orphans": local - target_set,
    }


def cmd_dry_run(args, backend=None) -> None:  # noqa: ARG001
    """Phase 4 — KIID delta only.  No writes, no downloads."""
    log.info("Phase 4 — KIID delta (dry-run)")

    if not KIID_DIR.exists():
        log.warning("KIID_DIR does not exist: %s", KIID_DIR)

    delta   = compute_delta(backend=backend)
    target  = delta["target"]
    missing = delta["missing"]
    present = delta["present"]
    orphans = delta["orphans"]

    print("\n" + "=" * 60)
    print("PHASE 4 — KIID DELTA (dry-run)")
    print("=" * 60)
    print(f"Target (from harvest catalogue WHERE codSus='KIID'): {len(target):,}")
    print(f"  Present locally: {len(present):,}")
    print(f"  Missing (would download): {len(missing):,}")
    print(f"  Orphans (local but not in harvest): {len(orphans):,}")

    if missing:
        print(f"\nSample missing ISINs (first 10):")
        for isin in sorted(missing)[:10]:
            print(f"  {isin}  ->  {target[isin]}")
    if orphans:
        print(f"\nOrphan ISINs (first 10, will NOT be deleted):")
        for isin in sorted(orphans)[:10]:
            print(f"  {isin}.pdf")

    print("=" * 60)
    print("Run --sync to download missing KIIDs.")


# ---------------------------------------------------------------------------
# PHASE 5 — Download
# ---------------------------------------------------------------------------
def _validate_pdf(content: bytes) -> tuple[bool, str]:
    if len(content) <= MIN_PDF_BYTES:
        return False, f"size={len(content)} bytes (< 10 KB)"
    if content[:4] != b"%PDF":
        return False, f"not a PDF (magic={content[:4]!r})"
    return True, ""


def _download_with_retry(session: requests.Session, href: str) -> tuple[bool, bytes, str]:
    backoff = [2, 4, 8]
    last_reason = "unknown"
    for attempt in range(4):
        try:
            resp = session.get(href, headers=HEADERS, timeout=TIMEOUT_S)
            if 400 <= resp.status_code < 500:
                return False, b"", f"HTTP {resp.status_code}"
            if resp.status_code >= 500:
                last_reason = f"HTTP {resp.status_code}"
                if attempt < 3:
                    time.sleep(backoff[attempt])
                continue
            ct = resp.headers.get("Content-Type", "")
            if "application/pdf" not in ct:
                return False, b"", f"Content-Type={ct!r} (not PDF)"
            return True, resp.content, ""
        except requests.exceptions.Timeout:
            last_reason = "timeout"
            if attempt < 3:
                time.sleep(backoff[attempt])
        except requests.exceptions.RequestException as exc:
            return False, b"", str(exc)
    return False, b"", last_reason


def cmd_sync(args, conn=None, backend=None) -> None:
    """Phase 5 — Download net-new KIID PDFs and record each in kiid_lifecycle.

    conn: injected connection — used by tests and any future dialect-aware caller. When None
    (the CLI's default), opens (and closes) its own connection via get_connection(backend=
    backend) (migration addendum, Stage 5, 2026-09-20).
    """
    limit: int | None = args.limit
    today = date.today().isoformat()   # YYYY-MM-DD

    log.info("Phase 5 — KIID sync%s", f" (limit={limit})" if limit else "")

    if not KIID_DIR.exists():
        log.error("[ERROR-002] KIID_DIR does not exist: %s", KIID_DIR)
        return

    own_conn = conn is None
    if own_conn:
        conn = get_connection(DB_PATH, backend=backend)

    delta   = compute_delta(conn=conn)
    target  = delta["target"]
    missing = sorted(delta["missing"])
    orphans = delta["orphans"]

    if limit is not None:
        missing = missing[:limit]

    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = _HARVEST_DIR / f"kiid_sync_report_{ts}.json"

    log.info("To download: %d  |  already present: %d  |  orphans: %d",
             len(missing), len(delta["present"]), len(orphans))

    _ensure_lifecycle_table(conn)

    downloaded = []
    failed     = []

    if not missing:
        log.info("Nothing to download — catalogue already in sync.")
    else:
        session = requests.Session()
        for i, isin in enumerate(missing, 1):
            href = target[isin]
            log.info("[%d/%d] %s  %s", i, len(missing), isin, href[:80])

            if i > 1:
                time.sleep(RATE_LIMIT_S)

            success, content, reason = _download_with_retry(session, href)
            if not success:
                log.warning("[NORM-010] Download failed: isin=%s reason=%s", isin, reason)
                failed.append({"isin": isin, "reason": reason})
                continue

            ok, reason = _validate_pdf(content)
            if not ok:
                log.warning("[NORM-011] Validation failed: isin=%s reason=%s", isin, reason)
                failed.append({"isin": isin, "reason": reason})
                continue

            # Atomic write
            pdf_path = KIID_DIR / f"{isin}.pdf"
            tmp_path = KIID_DIR / f"{isin}.pdf.tmp"
            try:
                tmp_path.write_bytes(content)
                os.replace(tmp_path, pdf_path)
                downloaded.append(isin)
                log.info("  OK saved %s  (%d KB)", pdf_path.name, len(content) // 1024)
                # Lifecycle: record new active period
                _lifecycle_activate(conn, isin, href, today)
            except OSError as exc:
                log.error("[ERROR-003] Write failed: isin=%s exc=%s", isin, exc)
                failed.append({"isin": isin, "reason": str(exc)})
                tmp_path.unlink(missing_ok=True)

    conn.commit()
    if own_conn:
        conn.close()

    _write_report(report_path, downloaded, failed, len(delta["present"]), sorted(orphans))
    log.info(
        "Sync complete: downloaded=%d  failed=%d  skipped_existing=%d  orphans=%d",
        len(downloaded), len(failed), len(delta["present"]), len(orphans),
    )
    print(f"\nReport -> {report_path}")


def _write_report(path: Path, downloaded: list, failed: list,
                  skipped_existing: int, orphans: list) -> None:
    report = {
        "downloaded":       downloaded,
        "failed":           failed,
        "skipped_existing": skipped_existing,
        "orphans":          orphans,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("Report written: %s", path.name)


# ---------------------------------------------------------------------------
# RETIRE ORPHANS
# ---------------------------------------------------------------------------
def cmd_retire_orphans(args, conn=None, backend=None) -> None:  # noqa: ARG001
    """
    Move orphan KIIDs to kiid_retired/YYYYMMDD/ and record retirement in kiid_lifecycle.
    Orphans = local PDFs whose ISIN is not in the latest harvest's codSus='KIID' set.
    Files are moved (never deleted) — the archive is permanent and queryable.

    conn: injected connection — used by tests and any future dialect-aware caller. When None
    (the CLI's default), opens (and closes) its own connection via get_connection(backend=
    backend) (migration addendum, Stage 5, 2026-09-20).
    """
    today    = date.today().isoformat()          # YYYY-MM-DD  e.g. 2026-07-18
    today_d  = datetime.now().strftime("%Y%m%d") # YYYYMMDD    e.g. 20260718  (retire_dir)
    dest_dir = KIID_RETIRED_BASE / today_d

    own_conn = conn is None
    if own_conn:
        conn = get_connection(DB_PATH, backend=backend)

    delta   = compute_delta(conn=conn)
    orphans = sorted(delta["orphans"])

    log.info("Retire orphans — %d orphans found", len(orphans))

    if not orphans:
        log.info("Nothing to retire.")
        if own_conn:
            conn.close()
        return

    print(f"\nOrphans to retire: {len(orphans)}")
    print(f"Source : {KIID_DIR}")
    print(f"Dest   : {dest_dir}")
    print(f"Sample (first 10):")
    for isin in orphans[:10]:
        print(f"  {isin}.pdf")
    if len(orphans) > 10:
        print(f"  ... +{len(orphans) - 10} more")

    dest_dir.mkdir(parents=True, exist_ok=True)

    _ensure_lifecycle_table(conn)

    moved   = []
    skipped = []

    for isin in orphans:
        src = KIID_DIR / f"{isin}.pdf"
        dst = dest_dir / f"{isin}.pdf"

        if not src.exists():
            log.warning("Orphan file not found (already moved?): %s", src.name)
            skipped.append(isin)
            continue
        if dst.exists():
            log.warning("Destination already exists, skipping: %s", dst)
            skipped.append(isin)
            continue

        src.rename(dst)
        moved.append(isin)
        # Lifecycle: close the current active period
        _lifecycle_retire(conn, isin, today, today_d)

    conn.commit()
    if own_conn:
        conn.close()

    log.info("Retire complete: moved=%d  skipped=%d  dest=%s",
             len(moved), len(skipped), dest_dir)
    print(f"\nMoved {len(moved)} KIID PDFs to {dest_dir}")
    if skipped:
        print(f"Skipped {len(skipped)} (file missing or dest already exists)")


# ---------------------------------------------------------------------------
# BACKFILL LIFECYCLE — one-time population from disk state
# ---------------------------------------------------------------------------
def cmd_backfill_lifecycle(args, conn=None, backend=None) -> None:  # noqa: ARG001
    """
    One-time command: populate kiid_lifecycle from the current disk state.

    Active  : every PDF in kiid/           → status='commercializing'
    Retired : every PDF in kiid_retired/*/ → status='retired'

    start_date  : file modification date (best available approximation)
    end_date    : directory YYYYMMDD parsed to YYYY-MM-DD (retired only)
    href        : from db_document_catalogue (latest harvest, codSus='KIID') where available

    conn: injected connection — used by tests and any future dialect-aware caller. When None
    (the CLI's default), opens (and closes) its own connection via get_connection(backend=
    backend) (migration addendum, Stage 5, 2026-09-20).
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection(DB_PATH, backend=backend)
    pg = is_postgres_connection(conn)
    ph = "%s" if pg else "?"
    _ensure_lifecycle_table(conn)

    # Build href lookup from latest harvest
    href_map = {
        r[0]: r[1] for r in conn.execute("""
            SELECT DISTINCT isin, href
            FROM db_document_catalogue
            WHERE cod_sus = 'KIID'
              AND isin IS NOT NULL
              AND harvest_ts = (SELECT MAX(harvest_ts) FROM db_document_catalogue)
        """).fetchall()
    }

    inserted = 0
    skipped  = 0

    # --- Active KIIDs ---
    log.info("Scanning kiid/ ...")
    for pdf in sorted(KIID_DIR.glob("*.pdf")):
        isin       = pdf.stem
        start_date = datetime.fromtimestamp(pdf.stat().st_mtime).strftime("%Y-%m-%d")
        href       = href_map.get(isin)

        cur = conn.execute(
            f"SELECT 1 FROM kiid_lifecycle WHERE isin={ph} AND end_date IS NULL", (isin,)
        ).fetchone()
        if cur:
            skipped += 1
            continue

        if pg:
            conn.execute("""
                INSERT INTO kiid_lifecycle
                    (isin, start_date, end_date, status, href, retire_dir)
                VALUES (%s, %s, NULL, 'commercializing', %s, NULL)
                ON CONFLICT (isin, start_date) DO NOTHING
            """, (isin, start_date, href))
        else:
            conn.execute("""
                INSERT OR IGNORE INTO kiid_lifecycle
                    (isin, start_date, end_date, status, href, retire_dir)
                VALUES (?, ?, NULL, 'commercializing', ?, NULL)
            """, (isin, start_date, href))
        inserted += 1

    log.info("Active: %d inserted, %d already had an active row", inserted, skipped)

    # --- Retired KIIDs ---
    retired_inserted = 0
    retired_skipped  = 0

    if KIID_RETIRED_BASE.exists():
        for retire_subdir in sorted(KIID_RETIRED_BASE.iterdir()):
            if not retire_subdir.is_dir():
                continue
            # Parse YYYYMMDD → YYYY-MM-DD
            dname = retire_subdir.name
            if len(dname) != 8 or not dname.isdigit():
                log.warning("Skipping unrecognised subdirectory: %s", retire_subdir)
                continue
            end_date   = f"{dname[:4]}-{dname[4:6]}-{dname[6:]}"
            retire_dir = dname

            for pdf in sorted(retire_subdir.glob("*.pdf")):
                isin       = pdf.stem
                start_date = datetime.fromtimestamp(pdf.stat().st_mtime).strftime("%Y-%m-%d")
                # start_date must be <= end_date; clamp if file mtime is after retirement date
                if start_date > end_date:
                    start_date = end_date
                href = href_map.get(isin)  # may be None (fund no longer in catalogue)

                cur = conn.execute(
                    f"SELECT 1 FROM kiid_lifecycle WHERE isin={ph} AND retire_dir={ph}",
                    (isin, retire_dir),
                ).fetchone()
                if cur:
                    retired_skipped += 1
                    continue

                if pg:
                    conn.execute("""
                        INSERT INTO kiid_lifecycle
                            (isin, start_date, end_date, status, href, retire_dir)
                        VALUES (%s, %s, %s, 'retired', %s, %s)
                        ON CONFLICT (isin, start_date) DO NOTHING
                    """, (isin, start_date, end_date, href, retire_dir))
                else:
                    conn.execute("""
                        INSERT OR IGNORE INTO kiid_lifecycle
                            (isin, start_date, end_date, status, href, retire_dir)
                        VALUES (?, ?, ?, 'retired', ?, ?)
                    """, (isin, start_date, end_date, href, retire_dir))
                retired_inserted += 1

        log.info("Retired: %d inserted, %d already had a row", retired_inserted, retired_skipped)

    conn.commit()

    # Summary
    total_active  = conn.execute(
        "SELECT COUNT(*) FROM kiid_lifecycle WHERE status='commercializing'"
    ).fetchone()[0]
    total_retired = conn.execute(
        "SELECT COUNT(*) FROM kiid_lifecycle WHERE status='retired'"
    ).fetchone()[0]
    if own_conn:
        conn.close()

    print(f"\nBackfill complete")
    print(f"  commercializing rows : {total_active:,}")
    print(f"  retired rows         : {total_retired:,}")
    print(f"  (new this run: active={inserted}, retired={retired_inserted})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="KIID sync, lifecycle & archive (SPEC-KIID-001-rev2)"
    )
    ap.add_argument("--dry-run",           action="store_true", default=False,
                    help="Phase 4: compute delta only, no downloads (DEFAULT)")
    ap.add_argument("--sync",              action="store_true", default=False,
                    help="Phase 5: download net-new KIIDs")
    ap.add_argument("--limit",             type=int, default=None,
                    help="Limit downloads to N (smoke test)")
    ap.add_argument("--retire-orphans",    action="store_true", default=False,
                    dest="retire_orphans",
                    help="Move orphan KIIDs to kiid_retired/YYYYMMDD/")
    ap.add_argument("--backfill-lifecycle", action="store_true", default=False,
                    dest="backfill_lifecycle",
                    help="One-time: populate kiid_lifecycle from disk state")
    ap.add_argument("--backend", choices=["sqlite", "postgres"], default=None,
                    help="Backend de BD (migracion, addendum 2026-09-20). Si se omite, resuelve "
                         "FONDOS_DB_BACKEND ('sqlite' si no esta definida).")
    args = ap.parse_args()

    if args.backfill_lifecycle:
        cmd_backfill_lifecycle(args, backend=args.backend)
    elif args.retire_orphans:
        cmd_retire_orphans(args, backend=args.backend)
    elif args.sync:
        cmd_sync(args, backend=args.backend)
    else:
        cmd_dry_run(args, backend=args.backend)


if __name__ == "__main__":
    main()
