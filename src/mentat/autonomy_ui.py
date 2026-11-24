"""A local, capability-protected viewer for an autonomy session."""

from __future__ import annotations

import html
import json
import re
import secrets
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

Snapshot = Callable[[], dict[str, Any]]
Control = Callable[[str, dict[str, Any]], dict[str, Any]]


class AutonomyServer(ThreadingHTTPServer):
    """Loopback HTTP server with a browser capability and launch URL."""

    token: str
    url: str

# Only these presentation fields cross the browser boundary. In particular, raw
# command output and arbitrary paths in event payloads are never served.
_FIELDS = {
    "session": ("id", "status", "repo", "base", "head", "started_at", "deadline", "policy"),
    "requirements": ("id", "area", "statement", "origin", "citations", "status"),
    "claims": ("id", "requirement_ids", "statement", "assumptions", "status", "freshness", "evidence_ids", "formal_meaning", "proof_scope", "declared_status"),
    "jobs": ("id", "loop", "role", "state", "status", "summary"),
    "findings": ("id", "statement", "summary", "status", "severity", "requirement_ids"),
    "decisions": ("id", "statement", "summary", "status"),
    "evidence": ("id", "statement", "summary", "status", "freshness", "kind", "claim_ids", "requirement_ids", "claim_id", "method", "assumptions", "source_identity", "contract", "precondition", "violation", "reason", "solver_version", "python_version", "solver_library_sha256", "frontend_sha256", "contract_sha256", "counterexample"),
    "events": ("id", "sequence", "seq", "type", "kind", "summary", "status", "timestamp"),
}
_NESTED_FIELDS = frozenset(("id", "title", "label", "source", "reference", "text", "status", "method", "revision", "assumptions", "claim_assumptions", "qualification", "requires", "ensures"))


def _source_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {}
    path = value.get("path")
    if isinstance(path, str) and path and not path.startswith("/") and "\\" not in path and all(p not in ("", ".", "..") for p in path.split("/")):
        result["path"] = path
    function = value.get("function")
    if isinstance(function, str) and function.isidentifier():
        result["function"] = function
    for key in ("revision", "source_sha256", "blob_oid"):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", item):
            result[key] = item
    return result


def _counterexample(value: Any) -> dict[str, bool | int]:
    if not isinstance(value, dict) or len(value) > 32:
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and key.isidentifier()
            and not re.search(r"token|secret|password|credential|key", key, re.I)
            and (type(item) is bool or (type(item) is int and item.bit_length() <= 256))}



def _value(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_value(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {str(k): _value(v, depth + 1) for k, v in value.items() if k in _NESTED_FIELDS}
    return str(value)[:4000]


def github_source_base(remote: str) -> str | None:
    """Accept only canonical GitHub repository remotes, without credentials."""
    match = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]+)", remote)
    if match is None:
        return None
    owner, repo = match.groups()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if repo in ("", ".", ".."):
        return None
    return f"https://github.com/{owner}/{repo}"


def _citations(value: Any, source_base: str | None) -> Any:
    def citation(item: Any, depth: int) -> Any:
        projected = _value(item, depth)
        if isinstance(item, dict) and isinstance(projected, dict):
            path = item.get("path")
            if (isinstance(path, str) and path and not any(c in path for c in ("\\", ":", "%"))
                    and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in path)
                    and all(part not in ("", ".", "..") and part.lower() != ".git" for part in path.split("/"))):
                projected["path"] = path
                revision = item.get("revision")
                if source_base and isinstance(revision, str) and re.fullmatch(r"[0-9a-fA-F]{40}", revision):
                    projected["href"] = f"{source_base}/blob/{revision.lower()}/{quote(path, safe='/')}"
        return projected

    if isinstance(value, list):
        return [citation(item, 1) for item in value]
    return citation(value, 0)


def _public_snapshot(raw: dict[str, Any], source_base: str | None = None) -> dict[str, Any]:
    if not isinstance(source_base, str) or github_source_base(source_base) != source_base:
        source_base = None
    result: dict[str, Any] = {"schema": raw.get("schema_version", raw.get("schema", 1)), "revision": raw.get("revision", 0)}
    for section, fields in _FIELDS.items():
        def project(item: Any, fields: tuple[str, ...] = fields, section: str = section) -> dict[str, Any]:
            if not isinstance(item, dict):
                return {}
            projected = {key: _value(item[key]) for key in fields if key in item}
            if section == "requirements" and "citations" in projected:
                projected["citations"] = _citations(item["citations"], source_base)
            if "source_identity" in projected:
                projected["source_identity"] = _source_identity(item["source_identity"])
            if "counterexample" in projected:
                projected["counterexample"] = _counterexample(item["counterexample"])
            if "contract" in projected and isinstance(item["contract"], dict):
                inputs = item["contract"].get("inputs")
                if isinstance(inputs, dict):
                    projected["contract"]["inputs"] = {
                        k: v for k, v in inputs.items()
                        if isinstance(k, str) and k.isidentifier() and v in ("int", "bool")
                        and not re.search(r"token|secret|password|credential|key", k, re.I)
                    }
            return projected

        value = raw.get(section, {} if section == "session" else [])
        if section == "session":
            result[section] = project(value)
        elif isinstance(value, list):
            result[section] = [project(item) for item in (value[-1000:] if section == "events" else value)]
        else:
            result[section] = []
    supplied_total = raw.get("event_total", len(raw.get("events", [])))
    total = supplied_total if isinstance(supplied_total, int) and supplied_total >= 0 else len(result["events"])
    result["event_total"] = max(total, len(result["events"]))
    result["events_truncated"] = result["event_total"] > len(result["events"])
    return result


_CSS = """
:root{color-scheme:light;--ink:#142e43;--teal:#087b78;--pale:#eef4f6;--line:#cbdbe0;--muted:#536d79}
*{box-sizing:border-box}body{margin:0;background:var(--pale);color:var(--ink);font:15px/1.55 system-ui,sans-serif}
button,input,textarea{font:inherit}button{cursor:pointer}button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid #d17d22;outline-offset:3px}
header{background:var(--ink);color:#fff;padding:24px max(24px,calc((100vw - 1440px)/2))}header h1{font:normal clamp(30px,4vw,44px)/1.1 Georgia,serif;margin:2px 0 8px}header p{margin:0;color:#c7dbe0}header .top{display:flex;justify-content:space-between;gap:20px;align-items:center}.live{color:#a6e4d7;font-weight:650}
.layout{max-width:1440px;margin:auto;display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:0}.main{padding:35px 42px 70px 24px}.rail{padding:35px 24px 70px 32px;border-left:1px solid var(--line);background:#e5eff1}
h2{font:normal 28px/1.2 Georgia,serif;margin:0 0 10px}h3{font:normal 22px/1.25 Georgia,serif;margin:0}p{margin:6px 0 12px}.intro{max-width:68ch;color:var(--muted)}.toolbar{display:flex;gap:12px;align-items:end;margin:24px 0}.toolbar label{flex:1;font-weight:650}.toolbar input{display:block;width:100%;margin-top:6px;padding:10px;border:1px solid #8caab3;background:#fff;border-radius:4px;color:var(--ink)}
.area{border-top:2px solid var(--ink);padding:18px 0 25px}.area-head{display:flex;justify-content:space-between;gap:12px;align-items:baseline}.count,.meta{color:var(--muted);font-size:13px}.entry{padding:16px 0;border-bottom:1px solid var(--line)}.entry p{font:normal 17px/1.5 Georgia,serif;margin:4px 0}.badge{display:inline-block;font:600 12px/1.4 system-ui,sans-serif;padding:3px 8px;border-radius:3px;background:#d4e7e5;color:#075d59}.badge:empty{display:none}.badge.warn{background:#f2e8cc;color:#755300}.empty{color:var(--muted);font-size:14px}details{margin-top:8px}summary{cursor:pointer;color:var(--teal);font-weight:650}.rail section{border-bottom:1px solid #bdd1d6;padding:0 0 24px;margin-bottom:25px}.rail h2{font-size:23px}.rail .entry p{font:inherit}.rail .entry{padding:10px 0}.rail .entry:last-child{border:0}.controls textarea{display:block;width:100%;min-height:80px;padding:9px;border:1px solid #8caab3;border-radius:4px;resize:vertical}.controls button{border:0;border-radius:4px;padding:9px 14px;background:var(--teal);color:white;font-weight:650;margin:10px 8px 0 0}.controls button.secondary{background:transparent;color:var(--ink);border:1px solid #7896a1}.notice{min-height:1.5em;font-size:13px;color:var(--muted)}
@media(max-width:800px){.layout{display:block}.main,.rail{padding:25px 20px 40px}.rail{border-left:0;border-top:1px solid var(--line)}header{padding:24px 20px}.toolbar{display:block}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
"""

# All session content is assigned through textContent; it is never interpreted as HTML.
_JS = r"""
'use strict';
const token = location.hash.slice(1); history.replaceState(null, '', location.pathname);
const headers = {'Authorization': 'Bearer ' + token};
const $ = id => document.getElementById(id);
const text = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
function node(tag, value, cls) { const e=document.createElement(tag); if(value != null)e.textContent=text(value); if(cls)e.className=cls; return e; }
function badge(value) {const v=text(value || 'Incomplete'); return node('span',v,'badge'+(/incomplete|gap|stale|unsupported|fail|missing/i.test(v)?' warn':''));}
function entry(item, primary) {const d=node('div',null,'entry'); const meta=node('div',null,'meta'); meta.textContent=[item.id, item.origin, item.role].filter(Boolean).join('  /  '); d.append(meta); d.append(node('p',item[primary] || item.statement || item.summary || item.kind || item.method || item.loop || 'No description available')); if(item.freshness)d.append(badge(item.freshness)); if(item.status)d.append(badge((item.freshness==='stale'?'Historical status: ':'Status: ')+item.status)); if(item.state && item.state!==item.status)d.append(badge('State: '+item.state)); return d;}
function detail(parent, title, value) {if(!value || (Array.isArray(value)&&!value.length))return; const d=node('details'); d.append(node('summary',title)); const vals=Array.isArray(value)?value:[value]; for(const v of vals){const p=node('p',null,'meta'); if(title==='Sources' && v && typeof v==='object' && v.href){const {href,...label}=v; const a=node('a',JSON.stringify(label)); a.href=href; a.rel='noopener noreferrer'; p.append(a);}else p.textContent=text(v);d.append(p);} parent.append(d);}
const areas=[['product','Product'],['function','Function'],['nonfunctional','Nonfunctional'],['architecture','Architecture'],['data-api','Data / API'],['acceptance','Acceptance']];
function normalize(s){return text(s).toLowerCase().replace(/[^a-z0-9]/g,'');}
function matches(area,key){const a=normalize(area); return a===normalize(key) || (key==='function' && ['functional','functions'].includes(a)) || (key==='data-api' && ['data','api','dataandapi'].includes(a)) || (key==='nonfunctional' && a==='nonfunctionalrequirements');}
let current={};
function render() {
 const s=current, session=s.session||{}; $('session').textContent=[session.id,session.repo,session.status].filter(Boolean).join('  /  ') || 'Session details unavailable'; $('status').textContent=session.status || 'Status unavailable';
 $('invalidation').textContent=session.status==='invalidated'?'Session invalidated. Prior claims and evidence are stale; their statuses are historical.':''; $('revision').textContent='Revision '+text(s.revision ?? 0); const query=$('search').value.toLowerCase(); const main=$('areas'); main.replaceChildren();
 for(const [key,label] of areas){const section=node('section',null,'area');const head=node('div',null,'area-head');head.append(node('h3',label));const all=(s.requirements||[]).filter(x=>matches(x.area,key));const filtered=all.filter(x=>JSON.stringify(x).toLowerCase().includes(query));head.append(node('span',all.length+' requirements','count'));section.append(head);if(!filtered.length)section.append(node('p',all.length?'No matching requirements.':'No requirements recorded for this area.','empty'));for(const r of filtered){const d=entry(r,'statement');detail(d,'Sources',r.citations);section.append(d);}main.append(section);}
 const other=(s.requirements||[]).filter(x=>!areas.some(([key])=>matches(x.area,key))); if(other.length){const section=node('section',null,'area');section.append(node('h3','Other requirements'));for(const r of other.filter(x=>JSON.stringify(x).toLowerCase().includes(query))){const d=entry(r,'statement');detail(d,'Sources',r.citations);section.append(d);}main.append(section);}
 const claims=$('claims'); claims.replaceChildren(); const matching=(s.claims||[]).filter(x=>JSON.stringify(x).toLowerCase().includes(query));if(!matching.length)claims.append(node('p','No matching claims recorded.','empty'));for(const c of matching){const d=entry(c,'statement'); if(!c.status)d.append(badge('Incomplete'));detail(d,'Formal meaning',c.formal_meaning);detail(d,'Proof scope',c.proof_scope);detail(d,'Declared status (historical)',c.declared_status);detail(d,'Assumptions',c.assumptions);detail(d,'Requirements',c.requirement_ids);detail(d,'Evidence',c.evidence_ids);claims.append(d);}
 for(const [id,items,field,empty] of [['jobs',s.jobs,'summary','No active loops recorded.'],['findings',s.findings,'statement','No findings recorded.'],['decisions',s.decisions,'statement','No decisions recorded.'],['evidence',s.evidence,'summary','No evidence recorded.'],['events',s.events,'summary','No activity recorded.']]){const el=$(id);el.replaceChildren();if(id==='events' && s.events_truncated)el.append(node('p','Showing '+items.length+' of '+s.event_total+' events; older activity is omitted.','empty'));if(!items||!items.length)el.append(node('p',empty,'empty'));for(const x of (items||[]).slice().reverse()){const d=entry(x,field);if(id==='evidence'){detail(d,'Method',x.method);detail(d,'Source identity',x.source_identity);detail(d,'Contract and conditions',x.contract);detail(d,'Assumptions',x.assumptions);detail(d,'Solver and Python versions',{solver:x.solver_version,python:x.python_version});detail(d,'Solver conditions',{precondition:x.precondition,violation:x.violation});detail(d,'Counterexample',x.counterexample);detail(d,'Claims',x.claim_ids||x.claim_id);detail(d,'Requirements',x.requirement_ids);}el.append(d);}}
}
async function refresh(){try{const response=await fetch('/api/snapshot',{headers,cache:'no-store'});if(!response.ok)throw Error('Access denied or session unavailable');current=await response.json();render();$('connection').textContent='Connected';}catch(e){$('connection').textContent='Disconnected';$('notice').textContent=text(e.message);}}
async function control(action, data){try{const response=await fetch('/api/control',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({action,...data})});const result=await response.json();if(!response.ok)throw Error(result.error || 'Request failed');$('notice').textContent=action==='feedback'?'Feedback sent.':'Stop requested.';if(action==='feedback')$('feedback').value='';await refresh();}catch(e){$('notice').textContent=text(e.message);}}
$('search').addEventListener('input',render);$('send').addEventListener('click',()=>{const message=$('feedback').value.trim();if(message)control('feedback',{message});else $('notice').textContent='Enter feedback first.';});$('stop').addEventListener('click',()=>control('stop',{}));refresh();setInterval(refresh,3000);
"""


def _page() -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mentat autonomy</title><style>{_CSS}</style></head><body>
<header><div class="top"><div><h1>Autonomy session</h1><p id="session">Loading session…</p></div><div><div id="status" class="live">Loading</div><small id="connection">Connecting</small></div></div></header>
<div class="layout"><main class="main"><p id="invalidation" class="badge warn" role="status"></p><h2>Contract and claims</h2><p class="intro">Review the requirements and the claims made against them. Missing information remains visible as a gap.</p><div class="toolbar"><label for="search">Search requirements and claims<input id="search" type="search" placeholder="Search the contract"></label><span id="revision" class="meta"></span></div><div id="areas"></div><section class="area"><h3>Formal claims</h3><p class="empty">Claims and their supporting evidence</p><div id="claims"></div></section></main>
<aside class="rail" aria-label="Session activity"><section><h2>Loops</h2><div id="jobs"></div></section><section><h2>Findings</h2><div id="findings"></div></section><section><h2>Decisions</h2><div id="decisions"></div></section><section><h2>Evidence</h2><div id="evidence"></div></section><section><h2>Activity</h2><div id="events"></div></section><section class="controls"><h2>Session controls</h2><label for="feedback">Feedback</label><textarea id="feedback" maxlength="4000"></textarea><button id="send" type="button">Send feedback</button><button id="stop" class="secondary" type="button">Request stop</button><p id="notice" class="notice" role="status"></p></section></aside></div><script>{_JS}</script></body></html>'''


def render_report(snapshot: dict[str, Any], *, source_base: str | None = None) -> str:
    """Render a standalone, escaped, non-interactive report without raw logs."""
    data = _public_snapshot(snapshot, source_base)
    esc = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    session = data["session"]
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Autonomy report</title><style>{_CSS}</style></head><body><main class="main"><h1>Autonomy report</h1>',
             f'<p>{esc(session.get("id", "Session unavailable"))} / {esc(session.get("status", "Status unavailable"))}</p>']
    if session.get("status") == "invalidated":
        parts.append('<p class="badge warn">Session invalidated. Prior claims and evidence are stale; their statuses are historical.</p>')
    for section in ("requirements", "claims", "jobs", "findings", "decisions", "evidence", "events"):
        parts.append(f'<section class="area"><h2>{esc(section.title())}</h2>')
        if section == "events" and data["events_truncated"]:
            parts.append(f'<p>Showing {len(data["events"])} of {data["event_total"]} events; older activity is omitted.</p>')
        if not data[section]:
            parts.append('<p class="empty">None recorded.</p>')
        for item in data[section]:
            parts.append('<div class="entry">')
            if item.get("freshness") == "stale":
                parts.append('<p class="badge warn">Stale</p>')
            for key, value in item.items():
                label = "Historical status" if key == "status" and item.get("freshness") == "stale" else "Declared status (historical)" if key == "declared_status" else key
                if section == "requirements" and key == "citations":
                    citations = value if isinstance(value, list) else [value]
                    parts.append(f'<p><strong>{esc(label)}:</strong></p>')
                    for citation in citations:
                        if isinstance(citation, dict) and "href" in citation:
                            text = {k: v for k, v in citation.items() if k != "href"}
                            parts.append(f'<p><a href="{esc(citation["href"])}" rel="noopener noreferrer">{esc(text)}</a></p>')
                        else:
                            parts.append(f'<p>{esc(citation)}</p>')
                else:
                    parts.append(f'<p><strong>{esc(label)}:</strong> {esc(value)}</p>')
            parts.append('</div>')
        parts.append('</section>')
    return ''.join(parts) + '</main></body></html>'


def create_server(snapshot: Snapshot, control: Control, *, host: str = "127.0.0.1", port: int = 0, source_base: str | None = None) -> AutonomyServer:
    """Create a loopback-only server; the caller owns serving and shutdown.

    Open ``server.url`` and run ``server.serve_forever()`` in a background thread.
    The fragment capability is removed from browser history by the page.
    """
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("autonomy UI must bind to loopback")
    token = secrets.token_urlsafe(32)
    actual_port = port

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # Never put capability material or user input in server logs.

        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

        def _valid_host(self) -> bool:
            return self.headers.get("Host") == f"{host}:{actual_port}"

        def _origin(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin == f"http://{host}:{actual_port}"

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}")

        def do_GET(self) -> None:
            if not self._valid_host() or not self._origin():
                self._json(403, {"error": "Invalid host or origin"})
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/" and not parsed.query:
                self._send(200, _page().encode(), "text/html; charset=utf-8")
                return
            if not self._authorized():
                self._json(401, {"error": "Unauthorized"})
                return
            if parsed.path == "/api/snapshot":
                self._json(200, _public_snapshot(snapshot(), source_base))
                return
            if parsed.path == "/api/events":
                try:
                    after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                except ValueError:
                    self._json(400, {"error": "Invalid event sequence"})
                    return
                events = _public_snapshot(snapshot(), source_base)["events"]
                self._json(200, {"events": [e for e in events if isinstance(e.get("sequence", e.get("seq")), int) and e.get("sequence", e.get("seq")) > after]})
                return
            self._json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            if not self._valid_host() or not self._origin() or self.headers.get("Origin") is None:
                self._json(403, {"error": "Invalid host or origin"})
                return
            if not self._authorized():
                self._json(401, {"error": "Unauthorized"})
                return
            if self.path != "/api/control":
                self._json(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                    raise ValueError
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or payload.get("action") not in ("feedback", "stop"):
                    raise ValueError
                action = payload["action"]
                if action == "feedback":
                    if set(payload) != {"action", "message"} or not isinstance(payload["message"], str) or not 0 < len(payload["message"].strip()) <= 4000:
                        raise ValueError
                    args = {"message": payload["message"].strip()}
                else:
                    if set(payload) != {"action"}:
                        raise ValueError
                    args = {}
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "Invalid request"})
                return
            self._json(200, control(action, args))

    server = AutonomyServer((host, port), Handler)
    actual_port = server.server_address[1]
    server.daemon_threads = True
    server.token = token
    server.url = f"http://{host}:{server.server_address[1]}/#{token}"
    return server
