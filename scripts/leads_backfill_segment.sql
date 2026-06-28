-- One-time: classify segment for leads imported before the segment column existed (from the OSM
-- shop type stored in `category`). Idempotent (only touches NULL-segment rows).
update leads set segment = case
  when category in ('supermarket','department_store','mall','general') then 'modern_trade'
  when category in ('wholesale','trade')                              then 'wholesale'
  when category in ('mobile_phone','telecommunication')               then 'mobile'
  when category in ('electronics','computer')                         then 'electronics'
  else 'general' end
where segment is null;
