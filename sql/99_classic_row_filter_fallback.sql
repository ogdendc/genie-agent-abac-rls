-- =============================================================================
-- 99 — Classic row filter (fallback when ABAC is not available)
-- =============================================================================
-- If your workspace doesn't have ABAC row-filter policies, use the classic
-- ALTER TABLE ... SET ROW FILTER approach. It uses the SAME filter function; the
-- only difference is that you bind it to each table/column explicitly instead of
-- once at the schema via a governed tag. There is no "one policy, firm-wide"
-- scaling with the classic approach — you repeat the ALTER TABLE per table.
--
-- Enforcement through Genie is identical: both approaches run as current_user().
-- =============================================================================

-- Same filter function as sql/01_filter_udf.sql
CREATE OR REPLACE FUNCTION <catalog>.<schema>.fa_book_filter(fa_email STRING)
RETURNS BOOLEAN
RETURN
       is_account_group_member('<managers_group>')
    OR fa_email = current_user();

-- Bind it to the table, naming the ownership column directly.
ALTER TABLE <catalog>.<schema>.client_book
  SET ROW FILTER <catalog>.<schema>.fa_book_filter ON (primary_fa_email);

-- To remove:
--   ALTER TABLE <catalog>.<schema>.client_book DROP ROW FILTER;
