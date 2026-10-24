# WAIVER_MDD_TEMPLATE — mandate waiver for a maximum drawdown above the mandate limit (FND-0223)

**What this is.** The mandate limit is a max drawdown of 15% (`AGENTS.md` Context). A *waiver* is the owner's written, dated, time-boxed decision to run the model
with an accepted drawdown above that limit. It **does not change the mandate**: the mandate stays in `shared/config.py` and changes only by a reviewed
commit. A waiver is an exception on record, with an expiry.

**When it is required.** Only if the evidence for a go-live (FND-0158 hysteresis go-live, or any later change that goes to PRODUCTION) shows the
corrected model's point-in-time (PIT) max drawdown **above 15%**. Without a signed waiver that go-live does not start. It is a non-extendable milestone:
a waiver that has lapsed is treated as absent.

**Reference point (2026-10-24, so nobody signs one by habit).** The V0 / SIMPLIFY configuration measured max DD **-14.04%** in the FND-0303 PIT run
(`out/reports/pit_variants/FND-0303_20261023_180546/REPORT_FND-0303.md`), inside the 15% limit: **no waiver is needed today**. The FND-0303 variants that
breached it (V1 -15.75%, V2 -19.6%) were rejected by the pre-registered rules, not waived.

## 1. Preconditions (all must hold before the owner signs)

- [ ] The evidence run is a **full-universe** PIT run (not a sample) on the code commit that would be deployed; its manifest, series stats and report are kept.
- [ ] The run was **pre-registered** (rules fixed before the result), or the deviation is stated in section 3.
- [ ] The accepted MDD is a **measured number from that run**, rounded up to the next 0.5 pp, never a target chosen afterwards.
- [ ] A change that would bring the MDD back under 15% was considered and the reason it is not taken is stated (cost in return / Sharpe, evidence).
- [ ] Scope is explicit: the whole portfolio, or the named sub-portfolio, and the regimes it applies to.
- [ ] The **review date** is at most 12 months out, and earlier if a forward checkpoint falls before it (FND-0299: 2028-10-31, 2029-10-31).
- [ ] Monitoring: the metric that will show the waiver is being breached (live max DD, `DD_CAT_P03` alarms in `fund_metric_alerts`) and the action if it is.

## 2. Waiver record (copy this block into the decision note and the log entry)

```
WAIVER-MDD  id: WVR-YYYYMMDD-NN          status: PROPOSED | SIGNED | LAPSED | REVOKED
mandate limit (max DD)        : 15.0 %
accepted max DD (PIT, model)  : __.__ %  (measured: __.__ %, rounded up to 0.5 pp)
scope                         : whole portfolio | sub-portfolio ____ | regimes ____
evidence run                  : path ____ | stamp ____ | git commit ____ | series_stats sha256 ____
pre-registration              : path / tag ____ | deviations: none | ____
alternative considered        : ____ | why not taken: ____
compensating controls         : ____ (e.g. alarm-watch gate FND-0289, hysteresis, FX budget)
monitoring / breach action    : ____
review date (expiry)          : YYYY-MM-DD   (no later than 12 months after signature)
backlog ticket (AP)           : FND-nnnn  (the go-live this waiver unblocks)
owner                         : <name>      signed on: YYYY-MM-DD
```

"Signed" means the owner states it in a message in their own words and the entry below records that message; an agent never signs, proposes
or back-dates it. A waiver is `PROPOSED` until the owner's confirmation exists.

## 3. Deviations from the pre-registration

State each one (rule, what was done instead, why). An undisclosed deviation voids the waiver.

## 4. Registration procedure (BACKLOG_LOGS is append-only: one INSERT, never an edit)

1. Save the filled record as `doc/decisiones/WAIVER_MDD_<id>.md` and commit it (reviewed commit, no waiver text only in a chat).
2. Append it to the owning ticket's history with the client that already enforces the append-only rule (never a hand-written `UPDATE`):

```bat
cd C:\desarrollo\fondos
C:\data\envs\des\python.exe -c "from shared.backlog_client import append_log; print(append_log('FND-nnnn', 'owner', 'WAIVER-MDD WVR-YYYYMMDD-NN SIGNED: accepted max DD __.__ pct, scope ___, review YYYY-MM-DD, note doc/decisiones/WAIVER_MDD_WVR-YYYYMMDD-NN.md'))"
```

   Keep Windows paths out of the comment text or write them with forward slashes: a backslash followed by `n`, `t` or `u` is an escape and corrupts the text.
3. Re-run `python scripts/audit/backlog_links.py --for FND-nnnn` and add a `RELATED` link from the waiver's ticket to the go-live ticket if it is not there.
4. **Expiry:** on the review date the waiver is `LAPSED` unless renewed with a **new** record (new evidence run, new signature). Renewal by edit is not valid.
   Revocation (the owner withdraws it, or a breach is observed) is a new log entry `WAIVER-MDD <id> REVOKED: <reason>`.

## 5. What a waiver never covers

- The currency-exposure limit (30%) and the structural-cash limit (10%): hard gates with no override (`GOBIERNO_CONTROLES.md`).
- A hard data defect on a selected fund (`HARD_INVARIANT`, `BLOCK2`, `TIMESERIES_DUPLICATE`): those have their own accepted-residual procedure.
- Any change to the mandate itself.
