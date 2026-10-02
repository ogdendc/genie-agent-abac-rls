# Appendix: hosting inside Databricks Apps (on-behalf-of-user)

If instead of an external app you host the UI as a **Databricks App**, you get the
same per-user RLS with *less* auth plumbing — Databricks handles sign-in and forwards
the user's token for you. This is the simplest way to stand up a hosted proof, but
note it's **not** the "button in our own external React app" architecture; it's an app
that lives inside Databricks.

## How it differs from the external pattern

| | External app (doc 3) | Databricks App (this appendix) |
|---|---|---|
| Hosting | your infrastructure | Databricks Apps |
| Sign-in | you implement OAuth U2M | Databricks SSO gates the app |
| User token | you obtain + hold it | forwarded in a request header |
| Code you write | OAuth flow + Genie proxy | just the Genie proxy |

## The mechanism

With user authorization enabled, Databricks forwards the signed-in user's access
token to your app in the **`x-forwarded-access-token`** header (and their email in
`x-forwarded-email`). Your app simply reads that header and uses it as the bearer
token for the Genie Conversation API — same start → poll → query-result sequence as
doc 3. No OAuth code, no token storage.

```python
# inside the request handler
token = self.headers.get("x-forwarded-access-token")   # the signed-in user's token
email = self.headers.get("x-forwarded-email")
answer, conv_id = genie_ask(token, question, conv_id)  # runs AS that user → UC RLS applies
```

A complete version is `reference-app/dbxapp_app.py` (companion to the external
`server.py`), with its `app.yaml`.

## Enabling on-behalf-of-user

Declare the user API scopes the app needs:

```bash
databricks apps update <app-name> --json '{"user_api_scopes": ["genie", "sql"]}'
```

Gotchas learned in practice:

- Scopes can only be set when the app's compute is **ACTIVE** or **STOPPED**, not
  while **STARTING**. Setting `user_api_scopes` at `apps create` time can silently
  no-op — set it with `apps update` after the app is ACTIVE.
- Use the `genie` scope (it replaced the deprecated `dashboards.genie`).
- On first open, each user consents to the requested scopes. If they get
  `Invalid scope: genie`, the workspace **user-authorization scope allowlist** (admin
  setting) must include `genie`/`sql` (or be `*`).
- For a non-creator user to even open the app, grant them `CAN USE` on the app (by
  default only the creator and admins can open it) — in addition to the Genie space
  `CAN RUN`, warehouse `CAN USE`, table `SELECT`, and function `EXECUTE` grants.

## Which to choose

- **"Add a button to our existing external app"** → the external OAuth U2M pattern in
  [doc 3](03-app-auth.md). This appendix doesn't fit that requirement.
- **"We just want a hosted internal tool, fast"** → Databricks App OBO (this
  appendix). Least code, Databricks owns the sign-in.

Either way the RLS story is identical: the request reaches Genie under the end user's
identity, and Unity Catalog does the filtering.
