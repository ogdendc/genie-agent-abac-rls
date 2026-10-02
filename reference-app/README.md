# Reference app

Two runnable reference implementations of the Genie proxy, both Python **standard
library only** (no `pip`, no `npm`). They do the same thing — call the Genie
Conversation API with the **end user's** token so Unity Catalog row filters apply —
and differ only in how they obtain that token.

| File | Pattern | Use when |
|---|---|---|
| [`server.py`](server.py) | External app, OAuth U2M + PKCE (a backend-for-frontend) | Your app runs outside Databricks (e.g. a button in your own React app). **This is the primary reference.** |
| [`dbxapp_app.py`](dbxapp_app.py) + [`app.yaml`](app.yaml) | Databricks App, on-behalf-of-user (`x-forwarded-access-token`) | You host the UI inside Databricks Apps and want Databricks to handle sign-in. |

Both are reference templates to port into your stack (Node/Express, FastAPI, Spring,
…) — not production servers (in-memory sessions, minimal error handling).

## Run the external reference (`server.py`)

```bash
export DBX_HOST="https://<your-workspace>.cloud.databricks.com"
export GENIE_SPACE_ID="<your-genie-space-id>"
# OAUTH_CLIENT_ID defaults to the built-in public client "databricks-cli";
# set it to your registered custom OAuth app in production.
python3 server.py
# open http://localhost:8050, sign in, ask a question
```

Self-test the Genie proxy without the browser flow (e.g. with any user token):

```bash
python3 server.py test "<access_token>" "How many clients are in my book?"
```

## Deploy the Databricks App variant (`dbxapp_app.py`)

Set `GENIE_SPACE_ID` in `app.yaml`, deploy as a Databricks App, then (once compute is
ACTIVE) enable OBO scopes:

```bash
databricks apps update <app-name> --json '{"user_api_scopes": ["genie", "sql"]}'
```

See [../docs/05-appendix-databricks-app-obo.md](../docs/05-appendix-databricks-app-obo.md)
for the gotchas (scope timing, allowlist, per-user grants).

## The one function that matters

`genie_ask(token, question, conv_id)` is identical in both files: it runs the
Conversation API **start → poll → query-result** sequence with the supplied token.
Everything else is just how each variant gets that token to be the end user's.
