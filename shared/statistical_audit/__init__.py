"""Generic statistical-audit engine (doc/reglas/AUDITORIA_ESTADISTICA.md §4).

Pure computation over pandas frames plus one read-only DB helper
(build_population). No metric or column name appears in this package's
functions — those live in the catalog_* modules. No DB writes anywhere here;
that is Phase C (audit_statistic/audit_finding persistence, preserve_and_write).
"""
