-- =============================================================================
-- 01 — The row-filter function
-- =============================================================================
-- A row filter is an ordinary SQL UDF returning BOOLEAN. Unity Catalog calls it
-- for every row at query time; the row is returned only when it evaluates TRUE.
--
-- The function receives the value of the OWNERSHIP COLUMN (here, the advisor's
-- email) and compares it to the caller. There is no per-user logic in any query
-- or in the app — the identity decision lives entirely in this one function.
--
-- Replace the <placeholders> with your own catalog/schema and managers group.
-- =============================================================================

CREATE OR REPLACE FUNCTION <catalog>.<schema>.fa_book_filter(fa_email STRING)
RETURNS BOOLEAN
RETURN
       is_account_group_member('<managers_group>')  -- managers / oversight see every row
    OR fa_email = current_user();                    -- every advisor sees only their own rows

-- Notes
-- -----
-- * current_user() resolves to the identity whose token is executing the query.
--   When Genie is called with an end-user token, that is the end user — which is
--   exactly why RLS follows through the Conversation API.
-- * is_account_group_member('<managers_group>') is evaluated against ACCOUNT groups.
--   Note that being a *workspace admin* is NOT the same as membership in an account
--   group named 'admins' — don't rely on admin status to bypass the filter.
-- * For a tiered hierarchy (a manager sees only their team, an exec sees all),
--   replace the manager branch with a lookup, e.g.:
--       OR EXISTS (SELECT 1 FROM <catalog>.<schema>.fa_hierarchy h
--                  WHERE h.manager_email = current_user()
--                    AND h.fa_email      = fa_email)
-- * CREATE OR REPLACE works even while the function is referenced by an active
--   policy, so you can iterate on the logic without dropping the policy.
