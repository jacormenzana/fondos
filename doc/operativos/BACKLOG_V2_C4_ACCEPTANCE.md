# Backlog v2, Phase C slice 4 (FND-0334): acceptance record and first signed attestation, 2026-10-31

Record of the state in which the signed attestation of the collision gate was first exercised. Facts only; no secret appears here. Specification:
`doc/especificacion/PROPUESTA_BACKLOG_V2.md` 6.4.6 and 6.4.7; rules: `doc/reglas/NORMAS_BACKLOG.md` 4.7 and 4.8.

## State verified before this push (read-only, 2026-10-31)

- **Code on `master`:** C1 to C4, the REVERT check of the sync, the collision queue in the cycle report and the Phase C acceptance verifier are merged; CI is green on the master head (five checks, `backlog-gate` included).
- **Database (live `gestion`):** `--phase hook`, `--phase collision` and `--phase attest` are applied. `scripts/audit/backlog_phase_c_acceptance.py --verify all` reported `ALL CHECKS PASSED` for the three phases
  (it also confirms that `backlog_gate` holds no privilege on any table or view of the backlog schema).
- **Branch protection of `master`:** `enforcement_level` is `everyone` (administrators included) and the required status checks are the four CI jobs plus `backlog-gate`
  (`scripts/ops/github_protect_master.py --with-backlog-gate --verify`: PROTECTION ACTIVE AS SPECIFIED).
- **Collision gate:** mode AUDIT since 2026-10-31 12:42 (14-day audit running); the one pair raised so far, FND-0334 / FND-0335, was adjudicated RELATED by `human:jacor`; the queue is empty.
- **Signing key:** the owner's public ed25519 key (`SHA256:LMRF0+7eXkbTSsEXOrGUrq3q8Mk74fin2VB0VuvYVSE`) is listed in `.github/backlog_gate_allowed_signers` since PR #9. CI reads that file as it is in the BASE of each
  range, so it applies to the first range AFTER that merge: this one.

## What this push proves

This branch carries one commit that cites FND-0334 (this file) and, after the owner runs `python scripts/audit/backlog_sync.py --attest --key <private key>`, the signed `backlog_gate/attestation.json` and
`.sig`. The `backlog-gate` job must then report `attestation OK ... signature verified` (not `NOT ENFORCED`), which is the first time the whole chain (hook nonce, tree hash, collision pairs, owner signature, CI) is
exercised end to end.

## Still open after it

- `python scripts/audit/backlog_sync.py --apply` (records the commit events and the branch-protection reading).
- Ticket FND-0334 stays IN_PROGRESS: its prerequisites FND-0331 (Phase A, step A-2) and FND-0332 are open, and closing is the owner's decision.
- Bypass judging (`BACKLOG_HOOK_EPOCH`) is not armed yet; the collision audit runs until about 2026-11-14.
