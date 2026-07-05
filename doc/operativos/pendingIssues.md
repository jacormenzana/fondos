Pending Issues — Consolidated
Priority 1 — Confirmed bugs, root cause known, fix not yet implemented

#	Issue	Root cause	Scope
1	NORDEA Investment_Universe='Liquidity' recurs	My earlier fix (SQL NULL-out + FORCE_REFRESH) only cleared the symptom once — the parser re-derives "Liquidity" incorrectly from the same KIID text on every re-download. Never fixed at the extraction-logic level. Confirmed still broken (Liquidity/Mixtos combo persists after this run).	1 fund confirmed, likely more corpus-wide (never audited beyond this one)
2	BGF China Bond Family never corrects (BL-64e doesn't fire)	BL44_NATURE_SRRI_R4 (15 hits this run) reroutes RFCP+SRRI-incompatible funds through a "Restantes" fallback path before BL-64e's Family correction is reached; BL-62 then re-derives Family='Emerging Market Debt' lexically from the name, overriding whatever BL-64e would have set. Needs pipeline.py investigation of BL-44/BL-62/BL-64e ordering.	3 confirmed (BGF China Bond), likely all 15 BL44_NATURE_SRRI_R4 hits affected
3	8 families with mixed Fund_Nature across share classes	6/8 are RESTANTES-block, caused/exposed by this session's detect_nature_from_kiid whitespace-normalization fix: sibling share classes have slightly different KIID PDF wording, so phrase-matching resolves inconsistently per class. 2/8 pre-existing, unrelated.	8 families (~17 ISINs)
4	Carmignac Patrimoine-style false positive in eq_dominant	"valores de renta variable" phrase fires on capped balanced-fund clauses ("máximo el 50%..."), not just true equity-dominant mandates. Attempted fix caused a worse regression (82+33 funds lost correct RV detection) and was reverted. Documented in code as a known residual, not resolved.	At least 1 confirmed (Carmignac Patrimoine family), likely a few more silently
Priority 2 — Flagged, not yet examined at all

#	Issue	Status
5	RESTANTES_KIID WARN (1 hit this run)	Never looked at — unknown severity
Priority 3 — Large, pre-existing, explicitly out of scope for now

#	Issue	Status
6	159 double-claimed funds across blocks (e.g., "Jupiter Dynamic Bond" claimed by both rf_flexible and mixtos)	Blocks assumed mutually exclusive but aren't; pipeline's sequential execution means the last block silently wins, no visibility. Structural, predates this session. Needs a design decision (explicit priority order? warning surfaced to user? ISIN-level override?) before any fix is attempted.
Examined this turn, no action needed (closing out, not pending):

BL47_SFDR_DEFAULT (51 hits) — benign fallback (assigns SFDR Article 8 when ESG=1 but article undetected). Working as intended.
Diagnostic tool's ACI_RHP "regrid" metric doesn't mirror production's plausibility guards (P0-ACI-RHP-GUARD) — cosmetic gap in diag_cost_extraction.py, not a pipeline bug. Low priority, optional.
Already resolved this session (for reference, not pending): cost-extraction gate/persistence (71 funds), 23 orphaned funds (7 RV-bond + 16 iShares equity), StyleProfile/MarketCap_Focus INTER rules, RC-08, INTER-NTC rule, 9/12 RESTANTES misclassifications.