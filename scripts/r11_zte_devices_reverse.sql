-- Reverse of scripts/r11_zte_devices_migration.sql (release r11, 02-Nov-2026): the ZTE handsets go back to having no
-- product category, so v_sales calls their lines Accessories again (as before r11).
--
-- Removes only what r11 added, dependents first; nothing is dropped. A row that something else has come to point at
-- since stays, and the statement that would remove it fails loudly instead (no CASCADE). Idempotent.
--   python -m scripts.apply_sql scripts/r11_zte_devices_reverse.sql --rehearse     (runs it, then rolls back)
--   python -m scripts.apply_sql scripts/r11_zte_devices_reverse.sql

set lock_timeout = '2s';

delete from division_rules
where pattern = '(^|[^a-z])zte([^a-z]|$)' and division = 'Devices';

delete from product_aliases
where alias_text in ('ZTE-A35e MOBILE', 'ZTE-A36 MOBILE')
  and product_id in (select p.id from products p where p.sku_code in ('ZTE-A35e', 'ZTE-A36'));

delete from products p
where p.sku_code in ('ZTE-A35e', 'ZTE-A36')
  and not exists (select 1 from product_aliases pa where pa.product_id = p.id);

delete from categories c
where c.name = 'Mobile Handsets' and c.division = 'Devices'
  and not exists (select 1 from products p where p.category_id = c.id);

-- the self-check: no ZTE line in v_sales is Devices through r11 any more
do $$
begin
  if exists (select 1 from product_aliases pa
             join products p on p.id = pa.product_id
             where pa.alias_text in ('ZTE-A35e MOBILE', 'ZTE-A36 MOBILE') and p.sku_code in ('ZTE-A35e', 'ZTE-A36')) then
    raise exception 'r11 reverse self-check: a ZTE alias is still there';
  end if;
  raise notice 'r11 reverse: the ZTE handsets have no product category again';
end $$;
