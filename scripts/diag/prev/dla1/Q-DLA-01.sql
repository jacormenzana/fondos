-- BL-DLA-1 resolvió la patología de serialización de párrafos en layouts 2-columna. 
-- Q-DLA-01 — Distribución de longitudes y línguas (corpus baseline)
SELECT
    Language,
    COUNT(*) AS n_funds,
    AVG(LENGTH(Raw_KIID_Text)) AS avg_len,
    MIN(LENGTH(Raw_KIID_Text)) AS min_len,
    MAX(LENGTH(Raw_KIID_Text)) AS max_len
FROM fund_kiid_metadata
WHERE Raw_KIID_Text IS NOT NULL
  AND LENGTH(Raw_KIID_Text) > 100
GROUP BY Language
ORDER BY n_funds DESC;