#!/usr/bin/env python3
"""
Book-of-Business — external OAuth (U2M) reference client / backend-for-frontend
for per-user row filtering through the Databricks Genie Conversation API.

Runs OUTSIDE Databricks (Python standard library only — no pip, no npm), mirroring
the target: your own (non-Databricks) web app calling Genie, with per-user row-level
security intact.

How it preserves RLS:
  * Each user signs in with Databricks (OAuth U2M: authorization-code + PKCE).
  * The app calls the Genie Conversation API with THAT user's access token.
  * Genie runs the generated SQL as that user, so Unity Catalog's ABAC row filter
    returns only the caller's rows. No shared service principal in the query path,
    so per-user row-level security is preserved.

Run:
    export DBX_HOST="https://<your-workspace>.cloud.databricks.com"
    export GENIE_SPACE_ID="<your-genie-space-id>"
    # OAUTH_CLIENT_ID defaults to the built-in public client "databricks-cli";
    # set it to your registered custom OAuth app in production.
    python3 server.py
    # open http://localhost:8050

Self-test the Genie proxy without the browser flow (e.g. with a user/SP token):
    python3 server.py test "<access_token>" "How many clients are in my book?"
"""
import base64
import hashlib
import http.cookies
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _require(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing required env var {name}. See the module docstring.")
    return v


HOST = _require("DBX_HOST").rstrip("/")
SPACE_ID = _require("GENIE_SPACE_ID")
CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "databricks-cli")  # register your own in prod
PORT = int(os.environ.get("PORT", "8050"))
REDIRECT = f"http://localhost:{PORT}"
SCOPES = os.environ.get("OAUTH_SCOPES", "all-apis offline_access")

SESSIONS = {}   # sid   -> {"token","user","conv_id"}
PENDING = {}    # state -> code_verifier


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _api(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        HOST + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}


def whoami(token):
    _, me = _api("GET", "/api/2.0/preview/scim/v2/Me", token)
    return me.get("userName") or me.get("displayName") or "unknown"


def genie_ask(token, question, conv_id=None):
    """Start (or continue) a Genie conversation as the token's identity; return the answer.

    This is the whole Conversation API dance: start/continue -> poll the message
    until COMPLETED -> fetch the query result rows. Every call carries the user's
    token, so Genie runs the generated SQL as that user and UC row filters apply.
    """
    if conv_id:
        _, r = _api("POST", f"/api/2.0/genie/spaces/{SPACE_ID}/conversations/{conv_id}/messages",
                    token, {"content": question})
        msg = r.get("message_id") or (r.get("message") or {}).get("id")
    else:
        _, r = _api("POST", f"/api/2.0/genie/spaces/{SPACE_ID}/start-conversation",
                    token, {"content": question})
        conv_id = r.get("conversation_id") or (r.get("conversation") or {}).get("id")
        msg = r.get("message_id") or (r.get("message") or {}).get("id")
    if not msg:
        return {"status": "ERROR", "text": f"Genie start failed: {r}", "sql": None,
                "columns": [], "rows": []}, conv_id

    mpath = f"/api/2.0/genie/spaces/{SPACE_ID}/conversations/{conv_id}/messages/{msg}"
    m = {}
    for _ in range(45):
        _, m = _api("GET", mpath, token)
        if m.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"):
            break
        time.sleep(2)

    text = sql = att = None
    for a in (m.get("attachments") or []):
        if a.get("text"):
            text = a["text"].get("content")
        if a.get("query"):
            sql = a["query"].get("query")
            att = a.get("attachment_id")
    out = {"status": m.get("status"), "text": text, "sql": sql, "columns": [], "rows": []}
    if att:
        _, qr = _api("GET", mpath + f"/query-result/{att}", token)
        sr = qr.get("statement_response") or {}
        out["rows"] = (sr.get("result") or {}).get("data_array") or []
        out["columns"] = [c.get("name") for c in
                          ((sr.get("manifest") or {}).get("schema") or {}).get("columns", [])]
    return out, conv_id


def authorize_url():
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_hex(16)
    PENDING[state] = verifier
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
        "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return HOST + "/oidc/v1/authorize?" + urllib.parse.urlencode(params)


def exchange_code(code, verifier):
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": CLIENT_ID, "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(HOST + "/oidc/v1/token", data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# --------------------------------------------------------------------------- UI
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Book of Business — Genie RLS reference</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font:15px -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}}
 header{{background:#0D1117;color:#fff;padding:14px 22px;display:flex;justify-content:space-between;align-items:center}}
 header b{{color:#FF3621}}
 .who{{font-size:13px;opacity:.9}} .who a{{color:#9fb3c8;margin-left:12px}}
 main{{max-width:860px;margin:22px auto;padding:0 16px}}
 .card{{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px 18px;margin-bottom:16px}}
 .ex{{display:inline-block;background:#eef1f5;border:1px solid #d8dde3;border-radius:16px;padding:5px 11px;margin:3px 4px;cursor:pointer;font-size:13px}}
 .ex:hover{{background:#e2e7ee}}
 textarea{{width:100%;box-sizing:border-box;height:58px;padding:9px;border:1px solid #ccd2d9;border-radius:8px;font:inherit}}
 button.ask{{background:#FF3621;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer;margin-top:8px}}
 button.ask:disabled{{opacity:.5}}
 .ans{{white-space:pre-wrap}} .sql{{background:#0D1117;color:#e6edf3;padding:11px;border-radius:8px;overflow:auto;font:12.5px SFMono-Regular,Menlo,monospace}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}} th,td{{border:1px solid #e3e6ea;padding:5px 8px;text-align:left}} th{{background:#f0f2f5}}
 .muted{{color:#697077;font-size:13px}}
 .signin{{background:#FF3621;color:#fff;text-decoration:none;border-radius:8px;padding:11px 20px;font-weight:600;display:inline-block}}
</style></head><body>
<header>
  <div><b>Book of Business</b> <span class="muted" style="color:#9fb3c8">(Genie + row-level security reference)</span></div>
  <div class="who">{who}</div>
</header>
<main>{body}</main>
<script>
function setq(t){{document.getElementById('q').value=t;}}
async function ask(){{
  var q=document.getElementById('q').value.trim(); if(!q)return;
  var btn=document.getElementById('go'); btn.disabled=true; btn.textContent='Asking Genie…';
  var out=document.getElementById('out'); out.innerHTML='<p class="muted">Running as you against the Genie space… row filter applied to your identity.</p>';
  try{{
    var r=await fetch('/ask',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:q}})}});
    var d=await r.json(); render(d);
  }}catch(e){{ out.innerHTML='<p style="color:#b00">Error: '+e+'</p>'; }}
  btn.disabled=false; btn.textContent='Ask Genie';
}}
function render(d){{
  var h='';
  if(d.text) h+='<p class="ans">'+esc(d.text)+'</p>';
  if(d.rows&&d.rows.length){{
    h+='<table><tr>'+d.columns.map(function(c){{return '<th>'+esc(c)+'</th>';}}).join('')+'</tr>';
    d.rows.slice(0,50).forEach(function(row){{h+='<tr>'+row.map(function(v){{return '<td>'+esc(v==null?'':v)+'</td>';}}).join('')+'</tr>';}});
    h+='</table>'; if(d.rows.length>50)h+='<p class="muted">'+d.rows.length+' rows (showing 50)</p>';
  }}
  if(d.sql) h+='<p class="muted" style="margin:12px 0 4px">SQL Genie ran (row filter applied transparently):</p><pre class="sql">'+esc(d.sql)+'</pre>';
  if(!d.text&&!d.rows) h+='<p style="color:#b00">'+esc(JSON.stringify(d))+'</p>';
  document.getElementById('out').innerHTML=h;
}}
function esc(s){{s=String(s);return s.replace(/[&<>]/g,function(c){{return{{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c];}});}}
</script></body></html>"""

SIGNED_OUT_BODY = """
<div class="card">
  <h2 style="margin-top:0">Sign in to view your book of business</h2>
  <p class="muted">This is a standalone web app running outside Databricks. When you sign in,
  it obtains <b>your</b> Databricks token (OAuth U2M) and asks Genie as you — so Unity Catalog's
  ABAC row filter returns only the rows in your book.</p>
  <a class="signin" href="/login">Sign in with Databricks</a>
</div>"""

CHAT_BODY = """
<div class="card">
  <p class="muted" style="margin:0 0 8px">Ask about your clients. You'll only ever see rows for
  the user you signed in as — the row filter is enforced by Unity Catalog, not by this app.</p>
  <div>
    <span class="ex" onclick="setq('How many clients are in my book of business?')">How many clients are in my book?</span>
    <span class="ex" onclick="setq('What is the total household AUM across my clients?')">Total household AUM</span>
    <span class="ex" onclick="setq('List my top 10 clients by household AUM')">Top 10 by AUM</span>
    <span class="ex" onclick="setq('Break down my clients by value tier')">By value tier</span>
  </div>
  <textarea id="q" placeholder="Ask Genie about your book…"></textarea>
  <button class="ask" id="go" onclick="ask()">Ask Genie</button>
</div>
<div class="card" id="out"><p class="muted">Your answer will appear here.</p></div>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _sid(self):
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c["sid"].value if "sid" in c else None

    def _session(self):
        return SESSIONS.get(self._sid() or "")

    def _bytes(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, location, headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        if u.path == "/" and "code" in q:                       # OAuth callback (root redirect)
            verifier = PENDING.pop(q.get("state", [""])[0], None)
            if not verifier:
                return self._bytes(400, "Bad or expired OAuth state. <a href='/'>Retry</a>")
            try:
                tok = exchange_code(q["code"][0], verifier)
            except urllib.error.HTTPError as e:
                return self._bytes(500, "Token exchange failed: " + e.read().decode()[:300], "text/plain")
            sid = secrets.token_hex(16)
            token = tok["access_token"]
            SESSIONS[sid] = {"token": token, "user": whoami(token), "conv_id": None}
            return self._redirect("/", headers=[("Set-Cookie", f"sid={sid}; Path=/; HttpOnly")])

        if u.path == "/login":
            return self._redirect(authorize_url())

        if u.path == "/logout":
            SESSIONS.pop(self._sid() or "", None)
            return self._redirect("/", headers=[("Set-Cookie", "sid=; Path=/; Max-Age=0")])

        if u.path == "/":
            s = self._session()
            if s:
                who = f"Signed in as <b style='color:#fff'>{s['user']}</b><a href='/logout'>sign out</a>"
                return self._bytes(200, PAGE.format(who=who, body=CHAT_BODY))
            return self._bytes(200, PAGE.format(who="", body=SIGNED_OUT_BODY))

        return self._bytes(404, "not found", "text/plain")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/ask":
            return self._bytes(404, "not found", "text/plain")
        s = self._session()
        if not s:
            return self._bytes(401, json.dumps({"error": "not signed in"}), "application/json")
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or "{}")
        ans, conv_id = genie_ask(s["token"], body.get("question", ""), s.get("conv_id"))
        s["conv_id"] = conv_id
        return self._bytes(200, json.dumps(ans), "application/json")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "test":
        token = sys.argv[2]
        question = sys.argv[3] if len(sys.argv) > 3 else "How many clients are in my book of business?"
        print("whoami:", whoami(token))
        ans, _ = genie_ask(token, question)
        print(json.dumps(ans, indent=2)[:1500])
        return
    print(f"Genie RLS reference → http://localhost:{PORT}")
    print(f"  host={HOST}\n  space={SPACE_ID}\n  oauth_client={CLIENT_ID}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
