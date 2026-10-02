# 2. The Genie space

Genie turns a user's natural-language question into SQL against the tables you
attach, runs it, and returns the answer. Nothing special is required of the space to
make RLS work — the security is in Unity Catalog (step 1) and the identity is in the
app auth (step 3). The space just needs the governed table attached and the right
people granted access.

## Create the space and attach the table

Easiest in the UI: **Genie → New** → attach `<catalog>.<schema>.client_book` → pick a
SQL warehouse. You can also script it with the REST API
(`POST /api/2.0/genie/spaces` and friends); the attach call requires a
`display_name`, the `warehouse_id`, and `table_identifiers`.

Note the resulting **space ID** (looks like `01f1...`) and the **warehouse ID** — the
app and notebook need both.

## Grants every end user needs

For a non-creator user to query the space successfully, **all** of these must be true
for that user (grant to a group in production):

| Grant | On | Why |
|---|---|---|
| `CAN RUN` | the Genie space | query the space |
| `CAN USE` | the SQL warehouse | run the generated SQL |
| `SELECT` | `client_book` | read the table |
| `EXECUTE` | `fa_book_filter` | the row filter runs as the user |
| `USE CATALOG` / `USE SCHEMA` | catalog / schema | traverse to the table |

The SQL grants are in [`sql/03_grants.sql`](../sql/03_grants.sql); the space `CAN RUN`
and warehouse `CAN USE` are set in the UI (or via permissions APIs).

## Steering instructions (optional but recommended)

Add a short general instruction so Genie answers in terms of "your book" naturally,
e.g.: *"Each user sees only the clients in their own book of business; answer in terms
of the signed-in advisor's clients. Don't filter by advisor in SQL — row-level
security already scopes results to the caller."* Genie instructions live inside the
space's `serialized_space` (set via `PATCH /api/2.0/genie/spaces/{id}`), or just type
them in the space's **Instructions** box in the UI.

> Do **not** try to enforce RLS via Genie instructions — instructions are advisory
> prompt text, not a security boundary. The enforcement is the UC policy.

Next: [3. App authentication (the crux) →](03-app-auth.md)
