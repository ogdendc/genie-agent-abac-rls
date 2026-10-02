-- =============================================================================
-- 02 — The ABAC row-filter policy (tag-driven, schema-scoped)
-- =============================================================================
-- Attribute-Based Access Control (ABAC) binds the row-filter function to tables
-- by a GOVERNED TAG rather than naming each table/column. Tag the ownership
-- column once; the policy then applies automatically to EVERY table in the schema
-- that carries that tag. One policy governs the whole domain (clients, prospects,
-- accounts, ...) instead of one filter per table.
--
-- ABAC row-filter/column-mask policies are GA. If your workspace does not have
-- ABAC available, use the classic approach in 99_classic_row_filter_fallback.sql
-- instead — same function, applied per table.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Step 1: register the governed tag (ACCOUNT-level, one time).
-- ---------------------------------------------------------------------------
-- Governed tags are created/managed at the account level. The simplest path is
-- the Databricks CLI (requires an account-admin context):
--
--     databricks tag-policies create-tag-policy --json '{
--       "tag_policy": {
--         "tag_key": "fa_owner",
--         "description": "Marks the column that holds the owning advisor identity",
--         "value_options": [ {"value": "email"} ]
--       }
--     }'
--
-- GOTCHA: if you register the tag with an EMPTY value list, applying the tag WITH
-- a value later fails ("not an allowed value"). Either declare the allowed values
-- up front (as above) or apply the tag key-only. The policy's has_tag('fa_owner')
-- matches on the KEY, so either works for enforcement — declaring values just
-- keeps the catalog tidy. See docs/01-unity-catalog-abac.md.

-- ---------------------------------------------------------------------------
-- Step 2: tag the ownership column on each governed table.
-- ---------------------------------------------------------------------------
ALTER TABLE <catalog>.<schema>.client_book
  ALTER COLUMN primary_fa_email
  SET TAGS ('fa_owner' = 'email');

-- ---------------------------------------------------------------------------
-- Step 3: create the schema-scoped ABAC row-filter policy.
-- ---------------------------------------------------------------------------
-- MATCH COLUMNS has_tag('fa_owner') AS fa_col  -> finds the tagged column and
--   aliases it as fa_col. USING COLUMNS (fa_col) passes that column's value as the
--   argument to fa_book_filter(fa_email). ABAC is strictly tag-driven: USING
--   COLUMNS must reference a column surfaced by a MATCH COLUMNS has_tag(...) clause
--   (you cannot name the column directly).
CREATE OR REPLACE POLICY fa_book_rls
ON SCHEMA <catalog>.<schema>
COMMENT 'Row-level security: each advisor sees only their own book of business; managers see their team.'
ROW FILTER <catalog>.<schema>.fa_book_filter
TO `account users`
FOR TABLES
MATCH COLUMNS has_tag('fa_owner') AS fa_col
USING COLUMNS (fa_col);

-- ---------------------------------------------------------------------------
-- Verify
-- ---------------------------------------------------------------------------
-- The policy as Unity Catalog stored it:
--   SELECT policy_name, policy_type, on_securable_type, securable_name,
--          to_principals, match_columns, when_condition, created_by
--   FROM <catalog>.information_schema.abac_policy_definitions
--   WHERE schema_name = '<schema>';
--
-- The governed tag on the ownership column:
--   SELECT catalog_name, schema_name, table_name, column_name, tag_name, tag_value
--   FROM <catalog>.information_schema.column_tags
--   WHERE schema_name = '<schema>' AND tag_name = 'fa_owner';
