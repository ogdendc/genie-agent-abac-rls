# Databricks notebook source
# MAGIC %md
# MAGIC # Genie Conversation API honors end-user permissions (row-level security)
# MAGIC
# MAGIC **What this proves:** when the Genie Conversation API is called **as an end user**, Unity Catalog's
# MAGIC fine-grained access controls are enforced — an advisor sees **only their own book of business**, even
# MAGIC though the underlying table holds every advisor's clients. The filtering is done by Unity Catalog (an
# MAGIC **ABAC** row-filter policy), not by application code, and it follows the caller's identity through Genie.
# MAGIC
# MAGIC **Key idea — identity is everything:** Genie runs its generated SQL as *whoever's token calls the API*.
# MAGIC - Called with an **end-user token** (this notebook, or an app via on-behalf-of-user) → per-user row filter applies.
# MAGIC - Called with a shared **service principal** → everyone looks identical (RLS lost). The anti-pattern to avoid.
# MAGIC
# MAGIC **This notebook runs as *you*** — so every query and Genie call below is scoped to what your identity may see.
# MAGIC Run it as an advisor and you see only that advisor's book; run it as a manager and you see the whole team.
# MAGIC Same code, different rows — decided by Unity Catalog, not the query.
# MAGIC
# MAGIC > Fill in the four placeholders in the next cell for your workspace, then run top to bottom.

# COMMAND ----------

CATALOG  = "<catalog>"
SCHEMA   = "<schema>"
TABLE    = f"{CATALOG}.{SCHEMA}.client_book"      # one row per client; every advisor's clients
FILTER_FN = f"{CATALOG}.{SCHEMA}.fa_book_filter"
SPACE_ID = "<your-genie-space-id>"                # Genie space with client_book attached

print("Table :", TABLE)
print("Genie :", SPACE_ID)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. The data model
# MAGIC `client_book` is one row per client. The column **`primary_fa_email`** is the owning advisor —
# MAGIC this is what the row filter keys on. Note we never filter by advisor in any query below; Unity Catalog does it.

# COMMAND ----------

display(spark.sql(f"DESCRIBE TABLE {TABLE}"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. The Unity Catalog governance objects (where the security lives)
# MAGIC Three pieces make the ABAC policy: a **governed tag** `fa_owner` marking the ownership column, a
# MAGIC **row-filter UDF** returning TRUE only for the caller's rows, and an **ABAC POLICY** binding the UDF to
# MAGIC any table whose column carries that tag.

# COMMAND ----------

# The filter logic — plain and auditable.
display(spark.sql(f"DESCRIBE FUNCTION EXTENDED {FILTER_FN}"))

# COMMAND ----------

# The ABAC policy itself, from Unity Catalog's system metadata.
display(spark.sql(f"""
  SELECT policy_name, policy_type, on_securable_type, securable_name,
         to_principals, match_columns, when_condition, created_by
  FROM {CATALOG}.information_schema.abac_policy_definitions
  WHERE schema_name = '{SCHEMA}'
"""))

# COMMAND ----------

# The governed tag on the ownership column (what the policy's MATCH COLUMNS has_tag('fa_owner') binds to).
display(spark.sql(f"""
  SELECT catalog_name, schema_name, table_name, column_name, tag_name, tag_value
  FROM {CATALOG}.information_schema.column_tags
  WHERE schema_name = '{SCHEMA}' AND tag_name = 'fa_owner'
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Proof #1 — plain SQL is already identity-scoped
# MAGIC The exact same `SELECT` returns a different book for each person who runs it. There is **no advisor filter in the SQL**.

# COMMAND ----------

display(spark.sql("SELECT current_user() AS i_am"))

# COMMAND ----------

# Same query for everyone — Unity Catalog scopes the rows to the caller.
display(spark.sql(f"""
  SELECT count(*)                         AS clients_i_can_see,
         count(DISTINCT primary_fa_email) AS advisors_i_can_see,
         round(sum(household_aum), 0)      AS total_household_aum
  FROM {TABLE}
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Proof #2 — the **Genie Conversation API** honors the same permissions
# MAGIC We call Genie **as the notebook's user** (same identity as above), ask a natural-language question, and
# MAGIC Genie's generated SQL comes back scoped to *your* book — because Genie ran it as you. In an app,
# MAGIC on-behalf-of-user / OAuth U2M forwards each user's token, so every user gets this same per-user behavior.

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()  # runs as the notebook's user
question = "How many clients are in my book of business, and what is their total household AUM?"

msg = w.genie.start_conversation_and_wait(SPACE_ID, question)

# Pull the natural-language answer and the SQL Genie generated.
answer_text, generated_sql = None, None
for att in (getattr(msg, "attachments", None) or []):
    txt = getattr(att, "text", None)
    if txt is not None:
        answer_text = getattr(txt, "content", None) or answer_text
    qy = getattr(att, "query", None)
    if qy is not None:
        generated_sql = getattr(qy, "query", None) or generated_sql

print("Question :", question)
print("\nGenie's answer:\n", answer_text)
print("\nSQL Genie generated (NO advisor filter — Unity Catalog applies it by your identity):\n", generated_sql)

# COMMAND ----------

# MAGIC %md
# MAGIC The count Genie returned matches your `clients_i_can_see` from Proof #1 — same identity, same UC row
# MAGIC filter, whether the query originates from SQL or from Genie.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The demo lineup (for a live compare-and-contrast)
# MAGIC | Signs in as | Role | Book they see |
# MAGIC |---|---|---|
# MAGIC | advisor A | advisor | only their own book |
# MAGIC | advisor B | advisor | only their own book (a different count) |
# MAGIC | a manager | manager / oversight | their whole team |
# MAGIC
# MAGIC Run this notebook (or open the app) as each person: same code, same generated SQL, different rows.
# MAGIC The manager's broader access is *also* just the row-filter UDF returning TRUE for more rows — still
# MAGIC enforced by Unity Catalog, still honored through Genie, no special app path.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. How it maps to production
# MAGIC - **The app calls Genie with the end user's token** (OAuth U2M / on-behalf-of-user, or token federation
# MAGIC   from your IdP). This is the crux: a shared service-principal token collapses all users into one identity
# MAGIC   and loses RLS. Each end user must be a Databricks account user so `current_user()` resolves.
# MAGIC - **The ABAC policy is tag-driven and schema-scoped**, so it auto-applies to *any* table whose ownership
# MAGIC   column carries the `fa_owner` governed tag — one policy governs the whole domain, not one filter per table.
# MAGIC - **Supported today:** per-user tokens where the end user is a Databricks account user. **Not yet a
# MAGIC   supported prod pattern:** one app credential impersonating many non-Databricks users.
