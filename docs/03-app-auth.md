# 3. App authentication — the crux

This is the part that decides whether RLS holds. The Unity Catalog policy from step 1
scopes rows by `current_user()`. So the only question that matters is:

> **Whose identity does the Genie Conversation API call run as?**

Genie executes its generated SQL as the identity of **the token in the API call**.
Get the token right and RLS is automatic; get it wrong and it silently collapses.

## Right vs. wrong

| Token the app sends to Genie | `current_user()` resolves to | RLS result |
|---|---|---|
| **The end user's own token** (OAuth U2M / OBO) | the end user | ✅ each user sees only their rows |
| One **shared service principal** for all users | that single SP | ❌ everyone sees the SP's rows — RLS lost |

The failure mode is dangerous because the app still *works* — it returns data, just
the same data for everyone. There's no error. So the auth design is the thing to get
right and to test with two different users.

## Hard prerequisite

`current_user()` can only scope correctly if the caller is a **real Databricks
account user**. That means:

- Each end user is provisioned as a Databricks account user (normally SCIM from your
  IdP), and
- Your app obtains **that user's own** access token and calls Genie with it.

**Not supported today:** a single app credential impersonating many end users who are
*not* Databricks account users ("app-delegated auth" is still in flight; row-filter
propagation through it is not a supported production pattern). If your users aren't
Databricks account users, resolve that before building — the rest of this doc assumes
they are.

## The supported pattern for an external app: OAuth U2M + a backend

Your app is your own web app (not hosted in Databricks). The clean pattern is **OAuth
User-to-Machine (U2M), authorization-code + PKCE**, with a small **backend-for-
frontend (BFF)** that holds the token and talks to Genie. (Browser JavaScript should
not hold tokens or call Genie directly — see [the React doc](04-react-integration.md)
for why.)

```
Browser (React)                 Your BFF                    Databricks
  │  click "Ask Genie"            │                             │
  │ ───────────────────────────► │                             │
  │                               │  (first time) redirect user │
  │ ◄──── 302 to Databricks ──────┤  to /oidc/v1/authorize      │
  │ ──────── user signs in (SSO), consents to scopes ─────────► │
  │ ◄──── redirect back with ?code ───────────────────────────┤
  │ ───────────────────────────► │  exchange code → USER TOKEN │
  │                               │  (held server-side)         │
  │                               │  Genie Conversation API     │
  │                               │ ───── Bearer <user token> ─►│  runs SQL AS the user
  │                               │ ◄──── answer + rows ────────┤  (UC row filter applied)
  │ ◄──── rows for THIS user ─────┤                             │
```

### The OAuth flow (what the BFF does)

1. **Authorize:** redirect the user to
   `https://<workspace-host>/oidc/v1/authorize` with `response_type=code`,
   `client_id`, `redirect_uri`, a PKCE `code_challenge` (`S256`), `state`, and
   `scope`.
2. **Callback:** Databricks redirects back to your `redirect_uri` with `?code&state`.
3. **Token exchange:** `POST https://<workspace-host>/oidc/v1/token` with
   `grant_type=authorization_code`, the `code`, `redirect_uri`, `client_id`, and the
   PKCE `code_verifier` → you receive the **user's** `access_token` (and a refresh
   token if you requested `offline_access`).
4. **Call Genie** with `Authorization: Bearer <user access_token>` (see sequence
   below). Keep the token server-side, keyed to the user's session.

A complete, runnable implementation of exactly this is
[`reference-app/server.py`](../reference-app/server.py) (Python standard library only).

### Scopes

Request the scopes your app needs. `all-apis` works and is simplest for a demo; for
least privilege, scope down to Genie/SQL access. If you use granular scopes and hit
`Invalid scope, required scopes: genie`, the workspace's **user-authorization scope
allowlist** (an admin setting) must permit those scopes.

### The OAuth client

- **Demo / quick start:** the built-in public client `databricks-cli` works with the
  PKCE flow and needs zero registration.
- **Production:** register **your own custom OAuth app integration** in the account
  console (account admin), add your app's redirect URI(s), and set its client ID. The
  flow is otherwise identical — a one-line config change.

## The Genie Conversation API call sequence

Once the BFF has the user's token, every question is three logical steps (poll until
the message completes):

1. **Start / continue a conversation**
   - new: `POST /api/2.0/genie/spaces/{space_id}/start-conversation` with
     `{"content": "<question>"}` → returns `conversation_id` + `message_id`
   - follow-up: `POST /api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages`
2. **Poll the message** until `status` is `COMPLETED` (or `FAILED` / `CANCELLED`):
   `GET /api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}`
   — the response carries the natural-language `text` answer and, if a query ran, a
   `query` attachment (the generated SQL + an `attachment_id`).
3. **Fetch the rows** for the query attachment:
   `GET .../messages/{message_id}/query-result/{attachment_id}` → statement response
   with columns + `data_array`.

All three calls carry the **user's** bearer token, so step 2's generated SQL runs
under the user's identity and the UC row filter applies. The Databricks SDK wraps this
as `w.genie.start_conversation_and_wait(space_id, question)` if you prefer the SDK over
raw REST.

## Test it like an auditor

The only meaningful test is **two different users, same question**:

- Sign in as advisor A → "how many clients in my book?" → A's count.
- Sign in as advisor B (different browser / incognito) → same question → B's count.
- Confirm the **generated SQL is identical** in both — proving the difference is
  identity + UC policy, not app logic.

Next: [4. Wiring the React button →](04-react-integration.md) ·
Alternative hosting: [5. Databricks App OBO →](05-appendix-databricks-app-obo.md)
