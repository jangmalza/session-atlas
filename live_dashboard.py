"""Local views, display settings and explicitly authorized analysis jobs."""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
from topic_display import TopicDisplayConflict, set_topic_display, set_session_display, set_item_display


def fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_tailscale_access(origin: str | None, user: str | None) -> tuple[str | None, str | None]:
    """Validate the explicitly configured trusted Serve proxy before any I/O."""
    if origin is None and user is None:
        return None, None
    if not isinstance(origin, str) or not isinstance(user, str):
        raise ValueError("--tailscale-origin and --tailscale-user must be provided together")
    # Accept one canonical HTTPS tailnet origin, never a URL prefix, wildcard,
    # arbitrary public host, alternate port, or user-info authority.
    if not re.fullmatch(r"https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.ts\.net", origin):
        raise ValueError("--tailscale-origin must be an exact HTTPS .ts.net origin without a port or path")
    hostname = urlsplit(origin).hostname
    if not hostname or len(hostname) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise ValueError("Invalid --tailscale-origin hostname")
    if not 1 <= len(user) <= 256 or any(ord(char) < 33 or ord(char) > 126 or char == "," for char in user):
        raise ValueError("--tailscale-user must be a single nonempty Tailscale login identity")
    return origin, user


def atomic_write(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def embedded_json(document: str, identifier: str) -> dict:
    expression = r'<script\b(?=[^>]*\bid=["\']' + re.escape(identifier) + r'["\'])[^>]*>(.*?)</script\s*>'
    matches = re.findall(expression, document, re.I | re.S)
    if len(matches) != 1:
        raise ValueError("Expected a self-contained graph snapshot")
    value = json.loads(matches[0])
    if not isinstance(value, dict):
        raise ValueError("Invalid graph snapshot")
    return value


def load_graph(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text) if path.suffix.lower() == ".json" else embedded_json(text, "graph-data")
    if not isinstance(value, dict) or value.get("schemaVersion") != "mvp-1":
        raise ValueError("Expected a Session Atlas snapshot")
    nodes, edges = value.get("nodes"), value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("Invalid graph snapshot arrays")
    identifiers = set()
    for node in nodes:
        if not isinstance(node, dict) or not all(isinstance(node.get(k), str) for k in ("id", "title", "classification", "project")):
            raise ValueError("Invalid graph snapshot node")
        if node["id"] in identifiers or not isinstance(node.get("provenance"), list):
            raise ValueError("Invalid graph snapshot identity")
        identifiers.add(node["id"])
        for item in node["provenance"]:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("session_id", "source_node")):
                raise ValueError("Invalid graph provenance")
    edge_ids = set()
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("from") not in identifiers or edge.get("to") not in identifiers:
            raise ValueError("Invalid graph snapshot relationship")
        if not isinstance(edge.get("id"), str) or edge["id"] in edge_ids:
            raise ValueError("Invalid graph snapshot relationship identity")
        edge_ids.add(edge["id"])
    return value


def validate_map_snapshot(document: str) -> None:
    data = embedded_json(document, "cm-data")
    if not isinstance(data.get("nodes"), list) or not isinstance(data.get("messages"), list):
        raise ValueError("Expected a complete Conversation Map HTML")
    scripts = re.findall(r'<script\b([^>]*)>(.*?)</script\s*>', document, re.I | re.S)
    executable = [body for attrs,body in scripts if not attrs.strip()]
    styles = re.findall(r'<style\b[^>]*>(.*?)</style\s*>', document, re.I | re.S)
    policy = re.search(r'<meta\b[^>]*http-equiv="Content-Security-Policy"[^>]*content="([^"]+)"', document, re.I)
    if len(scripts) != 2 or len(executable) != 1 or len(styles) != 1 or policy is None:
        raise ValueError("Expected self-contained offline viewer assets")
    directives = {}
    for directive in policy[1].split(';'):
        pieces = directive.strip().split(None, 1)
        if len(pieces) == 2:
            directives[pieces[0]] = pieces[1]
    if directives.get("default-src") != "'none'" or directives.get("connect-src", "'none'") != "'none'":
        raise ValueError("Conversation Map must remain offline")
    for kind, asset in (("script-src", executable[0]), ("style-src", styles[0])):
        token = base64.b64encode(hashlib.sha256(asset.encode('utf-8')).digest()).decode()
        if directives.get(kind) != "'sha256-" + token + "'":
            raise ValueError("Invalid Conversation Map asset hash")


def _index_html(status: dict) -> bytes:
    script = '\n'.join(Path(__file__).with_name(name).read_text(encoding='utf-8') for name in ('ui_theme.js', 'live_view.js', 'analysis_ui.js')).strip()
    style = '\n'.join(Path(__file__).with_name(name).read_text(encoding='utf-8') for name in ('ui_theme.css', 'live_view.css', 'analysis_ui.css')).strip()
    analysis_markup = Path(__file__).with_name('analysis_ui.html').read_text(encoding='utf-8')
    hashes = [base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() for s in (script, style)]
    policy = ("default-src 'none'; script-src 'sha256-" + hashes[0] + "'; style-src 'sha256-" + hashes[1] +
              "'; connect-src 'self'; frame-src 'self'; img-src data:; base-uri 'none'; form-action 'none'")
    payload = json.dumps(status, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{policy}"><meta name="referrer" content="no-referrer">
<title>Session Atlas · 자동 수집</title><style>{style}</style></head><body>
<header><div class="heading"><div class="brand"><svg viewBox="0 0 54 24" width="45" height="24" aria-hidden="true"><path d="M6 12H48" fill="none" stroke="currentColor"/><path d="M3 9h6v6H3zM18 9h6v6h-6zM33 9h6v6h-6zM45 9h6v6h-6z" fill="currentColor"/></svg><h1>SESSION ATLAS</h1></div><span id="collector-state" role="status">수집 준비 중</span><span id="last-checked">첫 수집을 기다리고 있습니다.</span><label class="ui-theme">화면<select data-ui-theme aria-label="화면 테마"><option value="system">기기 설정 따름</option><option value="paper">종이 화면</option><option value="dark">어두운 화면</option></select></label><details class="collection-stats"><summary>수집 현황</summary><div class="metrics"><div><strong data-testid="session-count">0</strong><span>수집한 세션</span></div>
<div><strong data-testid="message-count">0</strong><span>수집한 메시지</span></div>
<div><strong data-testid="analysed-count">0</strong><span>분석된 세션</span></div>
<div><strong data-testid="pending-count">0</strong><span>분석 대기 세션</span></div>
<div><strong data-testid="pending-message-count">0</strong><span>분석 대기 메시지</span></div><p class="policy-note">수집과 열람은 모델을 호출하지 않습니다. 분석은 요청한 세션에만 실행됩니다.</p></div></details></div>
<p class="explanation">새 대화는 수집하여 저장합니다. 그래프에는 요청하여 분석한 결과만 표시하며, 기존 분석과 근거는 유지합니다.</p></header>
<main><p id="initial-wait">저장된 분석 결과를 준비하고 있습니다.</p><iframe id="live-frame" title="분석 그래프" hidden></iframe></main>
{analysis_markup}<script type="application/json" id="live-status">{payload}</script><script>{script}</script></body></html>'''.encode("utf-8")


class LiveDashboard:
    """Serve managed views and read-only timeline queries on 127.0.0.1."""
    def __init__(self, directory: Path, database: Path, port: int = 8765,
                 graph_snapshot: Path | None = None, map_snapshot: Path | None = None, *,
                 tailscale_origin: str | None = None, tailscale_user: str | None = None,
                 codex_home: Path | None = None):
        self.tailscale_origin, self.tailscale_user = validate_tailscale_access(tailscale_origin, tailscale_user)
        self.directory = directory.resolve()
        marker = self.directory / ".context-graph-live.json"
        signature = fingerprint(os.path.normcase(str(database.resolve())).encode())
        expected = {"schemaVersion": "live-1", "databaseFingerprint": signature}
        if self.directory.exists() and any(self.directory.iterdir()) and not marker.is_file():
            raise ValueError("Choose an empty dedicated directory for --live-dir")
        self.directory.mkdir(parents=True, exist_ok=True)
        if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != expected:
            raise ValueError("The live directory belongs to another database")
        seed = self.directory / "graph.html"
        seed = seed if seed.is_file() else graph_snapshot
        self.graph = load_graph(seed) if seed else None
        self.map_snapshot = map_snapshot or (self.directory / "map.html" if (self.directory / "map.html").exists() else None)
        if self.map_snapshot:
            validate_map_snapshot(self.map_snapshot.read_bytes().decode("utf-8"))
        atomic_write(marker, json.dumps(expected).encode())
        self.analysis_revision = ""
        self.port = port
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        from session_timeline import TimelineReader
        from context_graph import codex_home as default_codex_home
        self.timeline = TimelineReader(database, codex_home if codex_home is not None else default_codex_home())
        from analysis_control import AnalysisControl
        self.analysis = AnalysisControl(database, codex_home if codex_home is not None else default_codex_home())
        assets=('graph_view.html','graph_view.js','graph_view.css','graph_design.css','explorer_tools.js','explorer_tools.css','raw_turns.js','mind_tree.js','mind_tree.css','ui_theme.js','ui_theme.css','live_view.js','live_view.css')
        self._ui_revision=fingerprint(b''.join(Path(__file__).with_name(name).read_bytes() for name in (*assets,'analysis_ui.html','analysis_ui.js','analysis_ui.css')))
        self._status = {"state": "starting", "autoAnalysis": False, "graphReady": False, "mapReady": False,
                        "analysisRevision": "",
                        "graphRevision": "", "mapRevision": "", "lastCheckedUtc": "", "scanErrors": 0,
                        **{k: 0 for k in ("sessionCount", "messageCount", "analysedSessionCount", "pendingSessionCount", "pendingMessageCount")}}
        self._save_status(self._status)

    @property
    def url(self) -> str:
        if not self._server:
            raise RuntimeError("Dashboard server is not started")
        return f"http://127.0.0.1:{self._server.server_port}/"

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _save_status(self, status: dict) -> None:
        atomic_write(self.directory / "index.html", _index_html(status))
        atomic_write(self.directory / "status.json", json.dumps(status, ensure_ascii=False).encode("utf-8"))
        with self._lock:
            self._status = dict(status)

    def publish(self, graph: dict, collection: dict, analysis_revision: str, render_graph) -> None:
        state = self.status()
        # Topic assignments change the derived view, not graph-data. Collection
        # counts and new message revisions deliberately do not trigger a render.
        graph_revision = fingerprint(json.dumps([graph, self.timeline.topic_assignment_revision(),self._ui_revision],
                                                ensure_ascii=False, sort_keys=True).encode())
        # Validate and prepare snapshots before replacing a displayed result.
        map_bytes = self.map_snapshot.read_bytes() if self.map_snapshot else None
        if map_bytes is not None:
            validate_map_snapshot(map_bytes.decode("utf-8"))
        graph_path = self.directory / "graph.html"
        if graph_revision != state["graphRevision"] or not graph_path.exists():
            descriptor, name = tempfile.mkstemp(prefix=".render-", suffix=".html", dir=self.directory)
            os.close(descriptor)
            temporary = Path(name)
            try:
                render_graph(graph, temporary)
                graph_bytes = temporary.read_bytes()
                placeholder = b'<script type="application/json" id="qa-context">{}</script>'
                if placeholder in graph_bytes:
                    sessions = {source["session_id"] for node in graph["nodes"]
                                if node["classification"] in ("open_question", "answered_question")
                                for source in node["provenance"]}
                    context = json.dumps(self.timeline.question_context(sessions), ensure_ascii=False).replace(
                        "<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
                    graph_bytes = graph_bytes.replace(placeholder, ('<script type="application/json" id="qa-context">' + context + '</script>').encode('utf-8'))
            finally:
                temporary.unlink(missing_ok=True)
            atomic_write(graph_path, graph_bytes)
        if map_bytes is not None:
            atomic_write(self.directory / "map.html", map_bytes)
        status = {k: collection.get(k, 0) for k in ("sessionCount", "messageCount", "analysedSessionCount",
                  "pendingSessionCount", "pendingMessageCount", "changedAnalysedSessionCount", "scanErrors")}
        status.update({"state": "warning" if status["scanErrors"] else "watching", "autoAnalysis": False,
                       "collector":collection.get('collector',{'state':'unknown'}),
                       "uiRevision":self._ui_revision,
                       "readOnlyViewer": bool(collection.get("readOnlyViewer")),
                       "analysisRevision": analysis_revision,
                       "lastCheckedUtc": collection["lastCheckedUtc"], "graphReady": True,
                       "mapReady": map_bytes is not None, "graphRevision": graph_revision,
                       "mapRevision": fingerprint(map_bytes) if map_bytes is not None else ""})
        self._save_status(status)
        self.graph = graph
        self.analysis_revision = analysis_revision

    def report_error(self, when: str) -> None:
        state = self.status()
        state.update({"state": "error", "lastCheckedUtc": when, "autoAnalysis": False})
        self._save_status(state)

    def start(self) -> None:
        dashboard = self
        routes = {"/": "index.html", "/index.html": "index.html", "/graph.html": "graph.html", "/map.html": "map.html"}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorize_request(self, *, search: bool = False) -> bool:
                hosts = self.headers.get_all("Host", [])
                allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                if dashboard.tailscale_origin:
                    allowed_hosts.add(urlsplit(dashboard.tailscale_origin).netloc)
                if len(hosts) != 1 or hosts[0] not in allowed_hosts:
                    self.send_error(400)
                    return False
                if dashboard.tailscale_origin:
                    # This dedicated server remains loopback-only. Tailscale
                    # Serve strips client identity headers and injects its own;
                    # forwarded-host headers are never trusted for access.
                    identities = self.headers.get_all("Tailscale-User-Login", [])
                    if len(identities) != 1 or identities[0] != dashboard.tailscale_user:
                        self.send_error(403)
                        return False
                if search:
                    origins = self.headers.get_all("Origin", [])
                    allowed_origins = ({dashboard.tailscale_origin} if dashboard.tailscale_origin else
                                       {f"http://127.0.0.1:{self.server.server_port}",
                                        f"http://localhost:{self.server.server_port}"})
                    if len(origins) > 1 or (origins and origins[0] not in allowed_origins):
                        self.send_error(403)
                        return False
                return True

            def do_GET(self):
                if not self._authorize_request():
                    return
                parsed = urlsplit(self.path)
                path = parsed.path
                if path in ('/analysis/settings.json', '/analysis/job.json'):
                    if parsed.query:
                        self.send_error(400)
                        return
                    try:
                        result = dashboard.analysis.settings() if path.endswith('settings.json') else dashboard.analysis.status()
                        self._analysis_json(result)
                    except Exception:
                        self._analysis_json({'error': 'settings_unavailable'}, 503)
                    return
                if path == "/status.json":
                    payload = json.dumps(dashboard.status(), ensure_ascii=False).encode("utf-8")
                    content_type = "application/json; charset=utf-8"
                elif path in ("/timeline/sessions.json", "/timeline/messages.json", "/timeline/context.json", "/timeline/turns.json"):
                    try:
                        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=5)
                        if path == "/timeline/sessions.json":
                            if query:
                                raise ValueError("Unexpected timeline query")
                            result = dashboard.timeline.read()
                        elif path in ("/timeline/context.json","/timeline/turns.json"):
                            if set(query)!={'session'} or len(query['session'])!=1 or not 1<=len(query['session'][0])<=128:raise ValueError('Invalid context query')
                            session=query['session'][0]
                            if path=='/timeline/turns.json':result=dashboard.timeline.turns(session)
                            else:
                                contexts=dashboard.timeline.question_context({session},live=True)
                                if session not in contexts:raise KeyError(session)
                                result=contexts[session]
                        else:
                            if set(query) - {"session", "offset", "limit", "message"} or any(len(values) != 1 for values in query.values()):
                                raise ValueError("Invalid timeline query")
                            session = query.get("session", [""])[0]
                            if not 1 <= len(session) <= 128:
                                raise ValueError("Invalid timeline session")
                            offset = int(query["offset"][0]) if "offset" in query else None
                            limit = int(query.get("limit", ["100"])[0])
                            message = query.get("message", [None])[0]
                            result = dashboard.timeline.read(session, offset, limit, message)
                        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    except (ValueError, TypeError):
                        self.send_error(400, "Invalid timeline request")
                        return
                    except KeyError:
                        self.send_error(404, "Timeline entry not found")
                        return
                    except (OSError, sqlite3.Error):
                        self.send_error(503, "Timeline temporarily unavailable")
                        return
                elif path in routes:
                    try:
                        payload = (dashboard.directory / routes[path]).read_bytes()
                    except OSError:
                        self.send_error(404)
                        return
                    content_type = "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_POST(self):
                # Search terms stay in a bounded POST body, never request URLs
                # or access logs. Every query uses a read-only DB connection.
                if not self._authorize_request(search=True):
                    return
                if urlsplit(self.path).path.startswith('/analysis/'):
                    self._analysis_post()
                    return
                if urlsplit(self.path).path in ("/topic-display.json", "/session-display.json", "/item-display.json"):
                    self._edit_display_preferences(urlsplit(self.path).path)
                    return
                if urlsplit(self.path).path != "/timeline/search.json":
                    self.send_error(405)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("Invalid request size")
                    self.connection.settimeout(10)
                    arguments = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(arguments, dict) or set(arguments) - {"query","project","role","session","offset","limit"}:
                        raise ValueError("Invalid request")
                    result = dashboard.timeline.search(**arguments)
                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                except (ValueError,TypeError,UnicodeError):
                    self.send_error(400, "Invalid search request")
                    return
                except (sqlite3.Error,OSError):
                    self.send_error(503, "Search temporarily unavailable")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                    pass

            def _analysis_json(self, result, status=200):
                payload = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def _analysis_post(self):
                from analysis_control import ControlError
                # All actions require the same authenticated origin, JSON and an
                # action-specific custom header. GET never checks login or starts work.
                path = urlsplit(self.path).path
                if (len(self.headers.get_all('Origin', [])) != 1
                        or self.headers.get_all('X-Context-Graph-Action', []) != [path.rsplit('/', 1)[-1].removesuffix('.json')]):
                    self._analysis_json({'error': 'origin_required'}, 403)
                    return
                try:
                    if (urlsplit(self.path).query or self.headers.get_all('Transfer-Encoding', [])
                            or len(self.headers.get_all('Content-Length', [])) != 1
                            or len(self.headers.get_all('Content-Type', [])) != 1
                            or self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json'):
                        raise ControlError('invalid_request')
                    length = int(self.headers['Content-Length'])
                    if not 0 < length <= 8192:
                        raise ControlError('invalid_request')
                    self.connection.settimeout(10)
                    arguments = json.loads(self.rfile.read(length).decode('utf-8'))
                    if path == '/analysis/settings.json':
                        result = dashboard.analysis.edit_settings(arguments)
                    elif path == '/analysis/check-codex.json' and arguments == {}:
                        result = dashboard.analysis.check_codex()
                    elif path == '/analysis/preview.json' and isinstance(arguments, dict) and set(arguments) == {'session', 'mode'}:
                        result = dashboard.analysis.preview(**arguments)
                    elif path == '/analysis/start.json':
                        result = dashboard.analysis.submit(arguments, dashboard.graph)
                    else:
                        raise ControlError('invalid_request')
                    self._analysis_json(result)
                except ControlError as error:
                    self._analysis_json({'error': error.code}, error.status)
                except (ValueError, TypeError, UnicodeError):
                    self._analysis_json({'error': 'invalid_request'}, 400)
                except Exception:
                    # Never forward credential, provider or filesystem diagnostics.
                    self._analysis_json({'error': 'operation_failed'}, 503)

            def _edit_display_preferences(self, path: str):
                # Browser-only mutation: exact same origin, JSON and a custom
                # header; cross-origin forms and unauthenticated proxies cannot
                # change even this display-only sidecar.
                if (len(self.headers.get_all('Origin', [])) != 1
                        or self.headers.get_all('X-Context-Graph-Action', []) != [path.removeprefix('/').removesuffix('.json')]):
                    self.send_error(403)
                    return
                if (urlsplit(self.path).query or self.headers.get_all('Transfer-Encoding', [])
                        or len(self.headers.get_all('Content-Length', [])) != 1
                        or len(self.headers.get_all('Content-Type', [])) != 1
                        or self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json'):
                    self.send_error(400, 'Invalid display edit')
                    return
                try:
                    length = int(self.headers['Content-Length'])
                    if not 0 < length <= 4096:
                        raise ValueError
                    self.connection.settimeout(10)
                    arguments = json.loads(self.rfile.read(length).decode('utf-8'))
                    if path == '/session-display.json':
                        if not isinstance(arguments, dict) or set(arguments) != {'session','displayName','revision'}:
                            raise ValueError
                        result = set_session_display(dashboard.timeline.database, session=arguments['session'],
                            display_name=arguments['displayName'], expected_revision=arguments['revision'])
                    elif path == '/item-display.json':
                        common = {'session','item','displayTitle','sourceMapSha256','revision'}
                        if not isinstance(arguments, dict) or set(arguments) not in (common, common | {'evidenceMessage'}):
                            raise ValueError
                        result = set_item_display(dashboard.timeline.database, session=arguments['session'], item=arguments['item'],
                            display_title=arguments['displayTitle'], source_map_sha256=arguments['sourceMapSha256'],
                            expected_revision=arguments['revision'], evidence_message=arguments.get('evidenceMessage'))
                    else:
                        common = {'session','topic','sourceMapSha256','revision'}
                        if not isinstance(arguments, dict) or set(arguments) not in (common | {'group'}, common | {'displayName'}):
                            raise ValueError
                        edit = {'display_name':arguments['displayName']} if 'displayName' in arguments else {'group':arguments['group']}
                        result = set_topic_display(dashboard.timeline.database, session=arguments['session'],
                            topic=arguments['topic'], source_map_sha256=arguments['sourceMapSha256'],
                            expected_revision=arguments['revision'], **edit)
                    payload = json.dumps(result, ensure_ascii=False).encode('utf-8')
                except TopicDisplayConflict:
                    self.send_error(409, 'Display settings changed; reload and retry')
                    return
                except KeyError:
                    self.send_error(404, 'Saved entry not found')
                    return
                except (ValueError, TypeError, UnicodeError):
                    self.send_error(400, 'Invalid display edit')
                    return
                except (OSError, sqlite3.Error):
                    self.send_error(503, 'Display settings temporarily unavailable')
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None
