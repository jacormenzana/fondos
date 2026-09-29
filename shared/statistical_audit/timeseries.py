"""Function #11 (AUDITORIA_ESTADISTICA.md §4): check_timeseries_integrity.

Gap detection is deliberately opt-in via an externally supplied
expected_dates index/mapping rather than "next month = this month + 1" —
that would fabricate false gaps from the source calendar's own irregularities
(AUDITORIA_ESTADISTICA.md §3, reinforcing the reviewed spec's own point on
expected_date_index). A monotonicity check on data already sorted for
grouping would be tautological, so it is not implemented here — duplicates
and gaps are the only two integrity signals this generic function can assert
without seeing the true source calendar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd


@dataclass
class TimeseriesIntegrityResult:
    n_series: int
    duplicates: pd.DataFrame
    gaps: pd.DataFrame


def check_timeseries_integrity(
    df: pd.DataFrame,
    entity_keys: Sequence[str],
    date_column: str,
    expected_dates: "pd.DatetimeIndex | Mapping[tuple, pd.DatetimeIndex] | None" = None,
) -> TimeseriesIntegrityResult:
    entity_keys = list(entity_keys)

    if df.empty:
        return TimeseriesIntegrityResult(0, df.copy(), pd.DataFrame(columns=entity_keys + [date_column]))

    n_series = df.drop_duplicates(subset=entity_keys).shape[0]

    dup_mask = df.duplicated(subset=entity_keys + [date_column], keep=False)
    duplicates = df.loc[dup_mask]

    gap_rows = []
    if expected_dates is not None:
        for key, group in df.groupby(entity_keys, dropna=False):
            key_tuple = key if isinstance(key, tuple) else (key,)
            actual = pd.DatetimeIndex(pd.to_datetime(group[date_column]))
            exp = expected_dates.get(key_tuple) if isinstance(expected_dates, Mapping) else expected_dates
            if exp is None:
                continue
            missing = pd.DatetimeIndex(exp).difference(actual)
            if len(missing):
                gap_df = pd.DataFrame({date_column: missing})
                for k, v in zip(entity_keys, key_tuple):
                    gap_df[k] = v
                gap_rows.append(gap_df)

    gaps = (
        pd.concat(gap_rows, ignore_index=True)
        if gap_rows
        else pd.DataFrame(columns=entity_keys + [date_column])
    )

    return TimeseriesIntegrityResult(n_series, duplicates, gaps)


def build_window_deflation_frame(
    ts: pd.DataFrame, nav_dates: pd.DataFrame, ipc: pd.DataFrame, periods_per_year: int = 12,
) -> pd.DataFrame:
    """Per (isin, date, window_label) row: stored nominal/real return_ann plus the deflator
    implied by them and the one expected from CPI -- the frame WINDOW_NOMINAL_IDENTITY,
    WINDOW_DEFLATION_STRICT and WINDOW_FISHER_IDENTITY (catalog_invariants.py, FND-0114) evaluate
    on.

    ts:        isin, date, window_label, w_return_nominal, w_return_real, w_n_obs -- one row per
               stored fund_metric_timeseries return_ann point that has BOTH real_flag variants.
    nav_dates: isin, date, nav -- the exact row set proyecto2's load_nav() feeds the pipeline
               (fund_nav_monthly, unfiltered).
    ipc:       date, ipc_index -- raw series_inflation rows for one geography (ES today).

    Reproduces deflation.py::deflate_nav()'s exact contract (merge_asof(direction='backward'),
    then bfill() only the genuinely-uncovered leading gap, per-ISIN) so the frame is checking the
    calculation AGAINST its own spec, not against a different one. Locates each window's start
    NAV by position (end_pos - (n_obs - 1)) in each fund's own NAV series -- correct even when
    run_pipeline.py's --from-date/--to-date clips a contiguous prefix off the front, because a
    contiguous clip only shifts which positions exist, never their relative offsets.

    Rows whose window start/end cannot be located in nav_dates (end date not itself a NAV date,
    or n_obs would reach before the series start) get NaN deflators and are therefore left out of
    n_applicable by check_invariant -- undecidable, never "violating" (P#1/R-4).
    """
    ipc = ipc[["date", "ipc_index"]].copy()
    ipc["date"] = pd.to_datetime(ipc["date"]) + pd.offsets.MonthEnd(0)  # matches db_readers.load_ipc()
    ipc["ipc_index"] = ipc["ipc_index"].astype(float)
    ipc = ipc.sort_values("date")

    nav = nav_dates[["isin", "date", "nav"]].copy()
    nav["date"] = pd.to_datetime(nav["date"])
    nav["nav"] = nav["nav"].astype(float)
    nav = nav.sort_values("date")
    nav = pd.merge_asof(nav, ipc, on="date", direction="backward")
    nav = nav.sort_values(["isin", "date"]).reset_index(drop=True)
    # Contract under test (deflate_nav): a NAV date before the fund's own IPC-aligned coverage
    # takes the earliest known IPC value instead of being dropped or given a later one.
    # audit: bfill-ok -- reproduces the production deflation contract, not silent imputation.
    nav["ipc_index"] = nav.groupby("isin")["ipc_index"].bfill()
    nav["pos"] = nav.groupby("isin").cumcount()

    out = ts.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.merge(
        nav.rename(columns={"pos": "end_pos", "ipc_index": "ipc_end", "nav": "nav_end"}),
        on=["isin", "date"], how="left",
    )
    out["end_pos"] = out["end_pos"].astype("Int64")
    out["start_pos"] = (out["end_pos"] - (out["w_n_obs"] - 1)).astype("Int64")

    start = nav[["isin", "pos", "ipc_index", "nav"]].rename(
        columns={"pos": "start_pos", "ipc_index": "ipc_start", "nav": "nav_start"}
    )
    start["start_pos"] = start["start_pos"].astype("Int64")
    out = out.merge(start, on=["isin", "start_pos"], how="left")

    years = out["w_n_obs"] / periods_per_year
    # Guard rail (not FND-0114 itself): stored nominal return_ann must still match today's
    # fund_nav_monthly, else the row is stale vs a rewritten NAV history, not a deflation defect
    # -- WINDOW_NOMINAL_IDENTITY reports that separately and gates the two deflation rules.
    out["w_nominal_gap"] = (
        (1 + out["w_return_nominal"]) ** years / (out["nav_end"] / out["nav_start"]) - 1
    ).abs()
    out["w_deflator_expected"] = out["ipc_end"] / out["ipc_start"]
    out["w_deflator_implied"] = (
        (1 + out["w_return_nominal"]) / (1 + out["w_return_real"])
    ) ** years
    out["w_cpi_ann"] = out["w_deflator_expected"] ** (1 / years) - 1
    return out
