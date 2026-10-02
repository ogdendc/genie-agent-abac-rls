# 4. Wiring an "Ask Genie" button into an existing React app

The goal: add a **"Chat with Genie"** button to an app you already have. The button
opens a chat panel, the user types a question, and answers come back scoped to that
user's book of business — because the request reaches Genie under the user's identity.

## Architecture: keep tokens and Genie calls on a backend

A browser single-page app **should not** hold the Databricks access token or call the
Genie Conversation API directly. Two reasons:

1. **Secrets/tokens in the browser are exposed.** Anything the browser holds, the user
   (and any script on the page) can read. Access tokens must stay server-side.
2. **CORS.** The Databricks REST APIs are not meant to be called from arbitrary
   browser origins; direct `fetch` from your SPA will generally be blocked.

So put a thin **backend-for-frontend (BFF)** between your React app and Databricks. It
holds each user's token (from the OAuth flow in [doc 3](03-app-auth.md)) and exposes a
tiny internal API your React app calls. [`reference-app/server.py`](../reference-app/server.py)
is a complete BFF you can port to your stack (Node/Express, FastAPI, Spring, etc.).

```
React app ──/api/genie/ask──► Your BFF ──Genie Conversation API (user token)──► Databricks
   (session cookie)             (holds the user's token, keyed to their session)
```

## The two endpoints your BFF needs

- `GET /api/auth/login` → kicks off the OAuth redirect; after callback, stores the
  user's token against their session and sets a session cookie.
- `POST /api/genie/ask` `{ "question": "..." }` → runs the Genie start → poll →
  query-result sequence with that user's token; returns `{ text, sql, columns, rows }`.

(Names are yours; these mirror `reference-app/server.py`.)

## The React side

Minimal: a button that toggles a panel, posts the question to your BFF, and renders
the answer. No Databricks SDK or token handling in the browser at all.

```jsx
import { useState } from "react";

function AskGenie() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [ans, setAns] = useState(null);
  const [loading, setLoading] = useState(false);

  async function ask() {
    if (!q.trim()) return;
    setLoading(true);
    setAns(null);
    try {
      const r = await fetch("/api/genie/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",            // send the session cookie to the BFF
        body: JSON.stringify({ question: q }),
      });
      if (r.status === 401) {
        window.location.href = "/api/auth/login"; // not signed in → start OAuth
        return;
      }
      setAns(await r.json());              // { text, sql, columns, rows }
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button onClick={() => setOpen((v) => !v)}>Chat with Genie</button>
      {open && (
        <div className="genie-panel">
          <textarea
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask about your book of business…"
          />
          <button onClick={ask} disabled={loading}>
            {loading ? "Asking…" : "Ask Genie"}
          </button>
          {ans?.text && <p>{ans.text}</p>}
          {ans?.rows?.length > 0 && (
            <table>
              <thead>
                <tr>{ans.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {ans.rows.slice(0, 50).map((row, i) => (
                  <tr key={i}>{row.map((v, j) => <td key={j}>{String(v ?? "")}</td>)}</tr>
                ))}
              </tbody>
            </table>
          )}
          {/* Showing the generated SQL is a great trust-builder in a demo:
              it has no advisor filter, yet rows are scoped — proving UC did it. */}
          {ans?.sql && <pre>{ans.sql}</pre>}
        </div>
      )}
    </>
  );
}

export default AskGenie;
```

## What makes RLS hold here

Nothing in this React code enforces anything — and that's the point. The row scoping
happens because:

1. The user authenticated via OAuth, so the **BFF holds that user's token**.
2. The BFF calls Genie with **that token**, so Genie's SQL runs as the user.
3. Unity Catalog's ABAC policy filters rows by `current_user()`.

Swap the signed-in user and the same button returns different rows, with no code
change. That's the demo.

## Multi-turn

Keep the `conversation_id` from the first `start-conversation` response in the user's
server-side session and pass it on follow-up questions so Genie keeps context. The
reference app does this per session.

Previous: [3. App authentication →](03-app-auth.md)
