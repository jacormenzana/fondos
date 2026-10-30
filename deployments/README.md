# deployments/

The system of record for deployments of this repository: one file per deployment, named `YYYY-MM-DD_<topic>.md`. A record states the exact refs and SHAs, what is in the payload, the evidence, how to publish and deploy it, and what was done afterwards (applied phases, ENFORCE confirmation, rollbacks).

Rule (owner order, 2026-10-29): deployment artifacts, bridgehead references and release logs live here, in version control. A temporary or scratch directory is never used for them.
