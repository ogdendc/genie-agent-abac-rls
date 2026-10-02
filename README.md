# Genie + Row-Level Security (ABAC) reference

How to let business users ask natural-language questions of a Databricks **Genie**
space from your own application — and have **Unity Catalog row-level security**
(RLS) hold, so each user only ever sees the rows they're allowed to see.

The worked example is a wealth-management **book of business**: a table holds every
advisor's clients, and each Financial Advisor (FA) must see **only their own book**.
A manager sees their whole team. Nobody changes the query — Unity Catalog filters the
rows by the caller's identity, and that enforcement follows the caller **through the
Genie Conversation API**.

> This repository is a sanitized reference extracted from a working proof. All
> workspace URLs, catalog/schema names, Genie space IDs, OAuth client IDs, emails,
> and secrets are placeholders (`<like-this>`) supplied via environment variables.
> Nothing here contains credentials.

---

## The one idea that makes this work

**Genie runs its generated SQL as whoever's token calls the Conversation API.**

```
                       ┌─────────────────────────────────────────────┐
                       │             Unity Catalog                    │
   "How many clients   │  client_book  (every advisor's clients)      │
    are in my book?"   │     + ABAC row-filter policy keyed on        │
          │            │       current_user()                         │
          ▼            └───────────────▲──────────────────────────────┘
   ┌─────────────┐                     │ SQL executed AS the end user
   │ Your app /  │   Conversation API  │
   │ "Ask Genie" │────────────────────►│  Genie space
   │   button    │   (end-user token)  │  (text → SQL)
   └─────────────┘                     │
          ▲                            │
          │  rows for THIS user only ──┘
          ▼
   Advisor A → their book   Advisor B → their book   Manager → whole team
   (same app, same question, same generated SQL — different rows)
```

- Call Genie with an **end-user token** (OAuth U2M / on-behalf-of-user) → the UC row
  filter applies per user. ✅
- Call Genie with one **shared service-principal token** → every user runs as that one
  SP, and per-user RLS is **silently lost**. ❌ This is the anti-pattern to avoid.

So this reference has two independent halves, and **both** must be right:

1. **[Unity Catalog ABAC policy](docs/01-unity-catalog-abac.md)** — the row filter on your tables.
2. **[App authentication](docs/03-app-auth.md)** — passing each user's *own* identity to Genie.

---

## Hard prerequisite (read before you design)

For `current_user()` to resolve to a real person, **each end user must be a Databricks
account user** (typically SCIM-provisioned from your IdP). The supported production
pattern is: your app obtains *that user's own* token and calls Genie with it.

**Not supported today:** one application credential impersonating many users who are
*not* Databricks account users ("app-delegated auth" is still in flight). If your users
aren't Databricks account users, this architecture does not hold — resolve that first.

---

## Repository map

| Path | What it is |
|---|---|
| [`docs/01-unity-catalog-abac.md`](docs/01-unity-catalog-abac.md) | Governed tag → row-filter function → ABAC policy. The security layer. |
| [`docs/02-genie-space.md`](docs/02-genie-space.md) | Create the Genie space, attach the table, required grants. |
| [`docs/03-app-auth.md`](docs/03-app-auth.md) | **The crux.** OAuth U2M token flow; why per-user identity; what's supported. |
| [`docs/04-react-integration.md`](docs/04-react-integration.md) | Wiring an "Ask Genie" button in an existing React app via a backend-for-frontend. |
| [`docs/05-appendix-databricks-app-obo.md`](docs/05-appendix-databricks-app-obo.md) | Alternative: host inside Databricks Apps with on-behalf-of-user auth. |
| [`sql/`](sql/) | Copy-paste DDL: filter function, ABAC policy, grants, classic fallback. |
| [`reference-app/server.py`](reference-app/server.py) | Runnable reference BFF (Python stdlib only): OAuth U2M + Genie proxy. |
| [`notebook/walkthrough.py`](notebook/walkthrough.py) | A notebook that *proves* RLS holds through Genie, run as different users. |

## Quick start

1. Stand up the security layer: [`docs/01-unity-catalog-abac.md`](docs/01-unity-catalog-abac.md).
2. Create and grant the Genie space: [`docs/02-genie-space.md`](docs/02-genie-space.md).
3. Prove it end-to-end with the notebook: [`notebook/walkthrough.py`](notebook/walkthrough.py).
4. Wire it into your app: [`docs/03-app-auth.md`](docs/03-app-auth.md) + [`docs/04-react-integration.md`](docs/04-react-integration.md),
   using [`reference-app/server.py`](reference-app/server.py) as a working template.
