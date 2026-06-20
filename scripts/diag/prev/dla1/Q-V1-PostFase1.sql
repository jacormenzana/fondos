-- V-1C: comparar Raw_KIID_Text antes/después en ISINs piloto.
-- (Requiere snapshot pre-piloto del Raw_KIID_Text de los 25 fondos.)
SELECT km.ISIN,
       LENGTH(km.Raw_KIID_Text) AS len_post,
       p.len_pre,
       fm.Type, fm.Family, fm.Entry_Fee_Pct, fm.Exit_Fee_Pct
FROM fund_kiid_metadata km
JOIN fund_master fm ON fm.ISIN = km.ISIN
JOIN _piloto_snapshot p ON p.ISIN = km.ISIN
WHERE km.ISIN IN (...lista 25...);