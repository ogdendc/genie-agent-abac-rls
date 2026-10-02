# 1. The Unity Catalog security layer (ABAC row filter)

This is where the row-level security actually lives. None of it is in the app or in
any query — it's three Unity Catalog objects that make the data self-governing.

## The data

A single table holds every advisor's clients. One column identifies the owning
advisor. In the worked example:

| object | example | notes |
|---|---|---|
| table | `<catalog>.<schema>.client_book` | one row per client |
| ownership column | `primary_fa_email` | the owning advisor's email |

The ownership column must contain a value that matches `current_user()` for the
owning user — i.e. the advisor's **account email/username**, not an internal code.
(If your table only has an internal advisor ID, add/compute an email column, or map
it in the filter function via a lookup table.)

## The three objects

### 1. A row-filter function — [`sql/01_filter_udf.sql`](../sql/01_filter_udf.sql)

An ordinary SQL UDF returning `BOOLEAN`. Unity Catalog evaluates it per row; the row
is returned only when it's `TRUE`. It receives the ownership-column value and
compares it to the caller:

```sql
CREATE OR REPLACE FUNCTION <catalog>.<schema>.fa_book_filter(fa_email STRING)
RETURNS BOOLEAN
RETURN
       is_account_group_member('<managers_group>')
    OR fa_email = current_user();
```

`current_user()` is the whole trick: it resolves to the identity whose token is
executing the query. Because Genie runs its SQL as the caller, this evaluates to the
end user when the app calls Genie with the end user's token.

> **Admin ≠ exempt.** A workspace admin or the table owner is *not* automatically
> exempt — they see rows only if the function returns `TRUE` for them. That's the
> correct, auditable behavior (and often surprises people who expect the owner to see
> everything).

### 2. A governed tag — `fa_owner`

ABAC binds policies to columns by a **governed tag** instead of by name. Register the
tag once at the account level, then tag the ownership column:

```bash
databricks tag-policies create-tag-policy --json '{
  "tag_policy": {
    "tag_key": "fa_owner",
    "description": "Marks the column holding the owning advisor identity",
    "value_options": [ {"value": "email"} ]
  }
}'
```

```sql
ALTER TABLE <catalog>.<schema>.client_book
  ALTER COLUMN primary_fa_email
  SET TAGS ('fa_owner' = 'email');
```

> **Gotcha:** registering the tag with an empty value list and then applying it
> *with* a value fails ("not an allowed value"). Declare the allowed values up front
> (as above) or apply the tag key-only. The policy matches on the tag **key**, so
> enforcement works either way.

### 3. The ABAC policy — [`sql/02_abac_policy.sql`](../sql/02_abac_policy.sql)

```sql
CREATE OR REPLACE POLICY fa_book_rls
ON SCHEMA <catalog>.<schema>
COMMENT 'Each advisor sees only their own book; managers see their team.'
ROW FILTER <catalog>.<schema>.fa_book_filter
TO `account users`
FOR TABLES
MATCH COLUMNS has_tag('fa_owner') AS fa_col
USING COLUMNS (fa_col);
```

- `ON SCHEMA` scopes the policy to the whole schema — it auto-applies to **any**
  table whose column carries the `fa_owner` tag. Add a new governed table later and
  it's protected the moment you tag its ownership column. This is the "one policy,
  firm-wide" story.
- `MATCH COLUMNS has_tag('fa_owner') AS fa_col` finds the tagged column; `USING
  COLUMNS (fa_col)` passes its value into the filter function. ABAC is strictly
  tag-driven — you can't name the column directly in `USING COLUMNS`.

## Verify it

```sql
-- The stored policy
SELECT policy_name, policy_type, on_securable_type, securable_name,
       to_principals, match_columns, when_condition, created_by
FROM <catalog>.information_schema.abac_policy_definitions
WHERE schema_name = '<schema>';

-- The tag on the ownership column
SELECT table_name, column_name, tag_name, tag_value
FROM <catalog>.information_schema.column_tags
WHERE schema_name = '<schema>' AND tag_name = 'fa_owner';

-- The proof: the SAME query returns a different row count per caller,
-- with NO advisor filter written in the SQL.
SELECT current_user() AS i_am,
       count(*)        AS rows_i_can_see,
       count(DISTINCT primary_fa_email) AS advisors_i_can_see
FROM <catalog>.<schema>.client_book;
```

Run that last query as two different advisors and you get two different counts —
Unity Catalog scoped the rows to each caller. The app never sees the difference; it
just gets the right rows.

## No ABAC in your workspace?

Use the classic equivalent in
[`sql/99_classic_row_filter_fallback.sql`](../sql/99_classic_row_filter_fallback.sql) —
same function, bound per table with `ALTER TABLE ... SET ROW FILTER`. Enforcement
through Genie is identical; you just lose the tag-driven, schema-wide auto-apply.

## Reference

- ABAC policies: `https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies`
- Governed tags: `https://docs.databricks.com/aws/en/admin/governed-tags/`
- Classic row filters: `https://docs.databricks.com/aws/en/tables/row-and-column-filters`

Next: [2. The Genie space →](02-genie-space.md)
