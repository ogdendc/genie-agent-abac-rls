#!/usr/bin/env python3
"""
Book of Business — Databricks App (on-behalf-of-user) variant.

Hosted INSIDE Databricks Apps. Databricks SSO gates the app and forwards the
signed-in user's token in the `x-forwarded-access-token` header. This app calls the
Genie Conversation API with THAT user's token, so Unity Catalog's ABAC row filter
returns only the caller's rows. No app service principal in the query path, so
per-user row-level security is preserved. See docs/05-appendix-databricks-app-obo.md.

Requires the app to declare user_api_scopes ["genie","sql"]. Python stdlib only.
Deploy with the companion app.yaml.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = (os.environ.get("DATABRICKS_HOST") or "").rstrip("/")
if HOST and not HOST.startswith("http"):
    HOST = "https://" + HOST
SPACE_ID = os.environ["GENIE_SPACE_ID"]            # set in app.yaml
PORT = int(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("PORT") or 8080)

CONV = {}   # user email -> conversation_id (for follow-ups)


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


def genie_ask(token, question, conv_id=None):
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


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Book of Business — Genie RLS</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font:15px -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}
 header{background:#0D1117;color:#fff;padding:14px 22px;display:flex;justify-content:space-between;align-items:center}
 header b{color:#FF3621}
 .who{font-size:13px;opacity:.92}
 main{max-width:860px;margin:22px auto;padding:0 16px}
 .card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px 18px;margin-bottom:16px}
 .ex{display:inline-block;background:#eef1f5;border:1px solid #d8dde3;border-radius:16px;padding:5px 11px;margin:3px 4px;cursor:pointer;font-size:13px}
 textarea{width:100%;box-sizing:border-box;height:58px;padding:9px;border:1px solid #ccd2d9;border-radius:8px;font:inherit}
 button.ask{background:#FF3621;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer;margin-top:8px}
 .ans{white-space:pre-wrap}
 .sql{background:#0D1117;color:#e6edf3;padding:11px;border-radius:8px;overflow:auto;font:12.5px SFMono-Regular,Menlo,monospace}
 table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px} th,td{border:1px solid #e3e6ea;padding:5px 8px;text-align:left} th{background:#f0f2f5}
 .muted{color:#697077;font-size:13px}
</style></head><body>
<header>
  <div><b>Book of Business</b> <span class="muted" style="color:#9fb3c8">(Genie + row-level security)</span></div>
  <div class="who">Signed in as <b style="color:#fff">__USER__</b></div>
</header>
<main>
  <div class="card">
    <p class="muted" style="margin:0 0 8px">Ask a question about the client base. Results are scoped to what
    you're allowed to see — enforced by Unity Catalog (ABAC), not by this app. Genie is called on your behalf
    (your token), so the same question returns the right rows for whoever asks.</p>
    <div>
      <span class="ex" onclick="setq('Show the total number of clients by value tier')">Clients by value tier</span>
      <span class="ex" onclick="setq('List the top 10 clients by household AUM')">Top 10 by AUM</span>
    </div>
    <textarea id="q" placeholder="Ask a question about the client data…"></textarea>
    <button class="ask" id="go" onclick="ask()">Ask Genie</button>
  </div>
  <div class="card" id="out"><p class="muted">Your answer will appear here.</p></div>
</main>
<script>
function setq(t){document.getElementById('q').value=t;}
async function ask(){
  var q=document.getElementById('q').value.trim(); if(!q)return;
  var btn=document.getElementById('go'); btn.disabled=true; btn.textContent='Asking Genie…';
  document.getElementById('out').innerHTML='<p class="muted">Running as you against the Genie space…</p>';
  try{
    var r=await fetch('ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
    render(await r.json());
  }catch(e){ document.getElementById('out').innerHTML='<p style="color:#b00">Error: '+e+'</p>'; }
  btn.disabled=false; btn.textContent='Ask Genie';
}
function render(d){
  var h='';
  if(d.text) h+='<p class="ans">'+esc(d.text)+'</p>';
  if(d.rows&&d.rows.length){
    h+='<table><tr>'+d.columns.map(function(c){return '<th>'+esc(c)+'</th>';}).join('')+'</tr>';
    d.rows.slice(0,50).forEach(function(row){h+='<tr>'+row.map(function(v){return '<td>'+esc(v==null?'':v)+'</td>';}).join('')+'</tr>';});
    h+='</table>'; if(d.rows.length>50)h+='<p class="muted">'+d.rows.length+' rows (showing 50)</p>';
  }
  if(d.sql) h+='<p class="muted" style="margin:12px 0 4px">SQL Genie ran (row filter applied transparently):</p><pre class="sql">'+esc(d.sql)+'</pre>';
  if(!d.text&&!d.rows) h+='<p style="color:#b00">'+esc(JSON.stringify(d))+'</p>';
  document.getElementById('out').innerHTML=h;
}
function esc(s){s=String(s);return s.replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _token(self):
        return self.headers.get("x-forwarded-access-token")

    def _email(self):
        return (self.headers.get("x-forwarded-email")
                or self.headers.get("x-forwarded-user") or "unknown user")

    def _bytes(self, code, body, ctype="text/html; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._bytes(200, PAGE.replace("__USER__", self._email()))
        if path == "/healthz":
            return self._bytes(200, "ok", "text/plain")
        return self._bytes(404, "not found", "text/plain")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path.rstrip("/") not in ("/ask", "ask"):
            return self._bytes(404, "not found", "text/plain")
        token = self._token()
        if not token:
            return self._bytes(200, json.dumps({
                "status": "NO_OBO_TOKEN",
                "text": ("No user token was forwarded (x-forwarded-access-token missing). "
                         "Enable user authorization on this app with scopes genie, sql.")
            }), "application/json")
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or "{}")
        email = self._email()
        ans, conv_id = genie_ask(token, body.get("question", ""), CONV.get(email))
        CONV[email] = conv_id
        return self._bytes(200, json.dumps(ans), "application/json")


if __name__ == "__main__":
    print(f"Book of Business app on :{PORT}  host={HOST}  space={SPACE_ID}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
