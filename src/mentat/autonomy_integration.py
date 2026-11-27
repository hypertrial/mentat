"""Capability-protected local JSON integration for an autonomous session."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import stat
import threading
import time
import urllib.parse
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mentat.autonomy_state import IntegrationCapacityError, Store

_RESOURCES = ("requirements", "claims", "jobs", "findings", "decisions", "evidence", "inventory")
_LIMITS = {"connections": 8, "header_bytes": 16384, "body_bytes": 16384,
           "feedback_bytes": 8192, "request_id_characters": 128, "page_items": 100,
           "response_bytes": 262144, "snapshot_bytes": 8388608, "snapshot_count": 2,
           "snapshot_ttl_seconds": 30, "raw_bytes": 33554432, "raw_events": 20000,
           "raw_jobs_records": 10000, "requests_per_second": 30, "snapshot_builds_per_second": 2,
           "socket_timeout_seconds": 3, "request_lifetime_seconds": 10, "sqlite_deadline_seconds": 2,
           "request_id_pattern": "[A-Za-z0-9._~-]{1,128}", "resource_item_bytes": 240000,
           "page_array_bytes": 240000, "json_depth": 16, "json_collection_items": 2000, "controls_per_session": 1000}
_ORIGIN = re.compile(r"http://(?:127\.0\.0\.1|localhost):([1-9][0-9]{0,4})\Z")
_EVENT_KINDS = frozenset({
    "session_started", "session_status", "session_resumed", "session_invalidated",
    "authority_pinned", "toolchain_pinned", "inventory", "record_added", "record_revised",
    "claim_refreshed", "dependencies_staled", "transition_staled", "transition_finished",
    "job_enqueued", "job_claimed", "job_finished", "job_lease_expired", "job_retry_limit",
    "source_barrier_activated", "source_barrier_released", "source_epoch_stale",
    "source_cycle_superseded", "source_correction_blocked", "source_correction_checkpoint",
    "source_correction_handoff", "human_feedback", "feedback_consumed", "integration_stop_requested",
    "remote_reconciliation_pending", "private_merge_resolved", "publication", "merge", "capabilities",
    "repair_checkpoint_selected", "trial_expired",
})
_KEY = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")


class _Failure(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False, reset: bool = False):
        self.status, self.code, self.message = status, code, message
        self.retryable, self.reset = retryable, reset


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()


def _secure_directory(path: Path, repo: Path) -> tuple[int, str]:
    path = Path(os.path.abspath(path))
    if path.parent == path or path.is_relative_to(repo.resolve()):
        raise ValueError("invalid rendezvous location")
    parts = path.parts
    fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts[1:-1], start=1):
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            final = index == len(parts) - 2
            if final:
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ValueError("private directory required")
            elif info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
                raise ValueError("untrusted directory ancestor")
        if len(parts) < 3:
            raise ValueError("private directory required")
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _check_json(value: Any, depth: int = 0) -> int:
    if depth > 16:
        raise ValueError("JSON depth")
    if isinstance(value, dict):
        count = len(value) + sum(_check_json(v, depth + 1) for v in value.values())
    elif isinstance(value, list):
        count = len(value) + sum(_check_json(v, depth + 1) for v in value)
    else:
        count = 0
    if count > 2000:
        raise ValueError("JSON items")
    return count


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, adapter: "IntegrationAdapter"):
        self.adapter = adapter
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", 0), _Handler)

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request: socket.socket, client_address: tuple) -> None:
        pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _Server)
        assert isinstance(server.server_address, tuple)
        adapter = server.adapter
        self.request.settimeout(3)
        start = time.monotonic()
        origin = None
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                if len(data) >= 16384 or time.monotonic() - start > 10:
                    raise _Failure(400, "invalid_request", "Invalid request.")
                self.request.settimeout(min(3, max(0.001, 10 - (time.monotonic() - start))))
                chunk = self.request.recv(min(4096, 16385 - len(data)))
                if not chunk:
                    return
                data += chunk
            header, body = data.split(b"\r\n\r\n", 1)
            if len(header) + 4 > 16384:
                raise _Failure(400, "invalid_request", "Invalid request.")
            lines = header.decode("ascii").split("\r\n")
            method, target, version = lines[0].split(" ")
            if version != "HTTP/1.1" or method not in ("GET", "POST", "OPTIONS") or not target.startswith("/"):
                raise _Failure(400, "invalid_request", "Invalid request.")
            headers: dict[str, str] = {}
            for line in lines[1:]:
                name, value = line.split(":", 1)
                name = name.lower()
                if not re.fullmatch(r"[a-z0-9-]+", name) or name in headers or "\r" in value or "\n" in value:
                    raise _Failure(400, "invalid_request", "Invalid request.")
                headers[name] = value.strip()
            if headers.get("host") != f"127.0.0.1:{server.server_address[1]}":
                raise _Failure(403, "forbidden", "Host is not allowed.")
            origin = headers.get("origin")
            if origin is not None and origin not in adapter.allowed_origins:
                origin = None
                raise _Failure(403, "forbidden", "Origin is not allowed.")
            if "transfer-encoding" in headers or "expect" in headers:
                raise _Failure(400, "invalid_request", "Invalid request.")
            length_string = headers.get("content-length", "0")
            if not re.fullmatch(r"0|[1-9][0-9]{0,5}", length_string):
                raise _Failure(400, "invalid_request", "Invalid request.")
            length = int(length_string)
            if length > 16384:
                raise _Failure(413, "capacity", "Request exceeds capacity.")
            if method != "POST" and length:
                raise _Failure(400, "invalid_request", "Invalid request.")
            if method == "POST" and headers.get("content-type", "").lower() != "application/json":
                raise _Failure(400, "invalid_request", "Invalid request.")
            if len(body) > length:
                raise _Failure(400, "invalid_request", "Invalid request.")
            if not adapter.admit():
                raise _Failure(429, "capacity", "Request rate exceeded.", retryable=True)
            if method == "OPTIONS":
                if origin is None or headers.get("access-control-request-method") not in ("GET", "POST"):
                    raise _Failure(403, "forbidden", "Preflight is not allowed.")
                requested = {x.strip().lower() for x in headers.get("access-control-request-headers", "").split(",") if x.strip()}
                if not requested <= {"authorization", "content-type"}:
                    raise _Failure(403, "forbidden", "Preflight is not allowed.")
                self._send(200, {"api_version": 1}, origin, preflight=True)
                return
            auth = headers.get("authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + adapter.token):
                raise _Failure(401, "unauthorized", "Authentication required.")
            while len(body) < length:
                if time.monotonic() - start > 10:
                    raise _Failure(400, "invalid_request", "Invalid request.")
                self.request.settimeout(min(3, max(0.001, 10 - (time.monotonic() - start))))
                chunk = self.request.recv(length - len(body))
                if not chunk:
                    raise _Failure(400, "invalid_request", "Invalid request.")
                body += chunk
            status, result = adapter.route(method, target, body, start)
            self._send(status, result, origin)
        except _Failure as exc:
            self._send(exc.status, {"api_version": 1, "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable, "reset_required": exc.reset}}, origin)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self._send(400, {"api_version": 1, "error": {"code": "invalid_request", "message": "Invalid request.", "retryable": False, "reset_required": False}}, origin)
        except (OSError, TimeoutError):
            pass
        except Exception:
            self._send(503, {"api_version": 1, "error": {"code": "unavailable", "message": "Service unavailable.", "retryable": True, "reset_required": False}}, origin)

    def _send(self, status: int, payload: dict, origin: str | None, preflight: bool = False) -> None:
        data = _encoded(payload)
        if len(data) > 262144:
            status = 413
            data = _encoded({"api_version": 1, "error": {"code": "capacity", "message": "Response exceeds capacity.", "retryable": False, "reset_required": False}})
        extra = ""
        if origin:
            extra = f"Access-Control-Allow-Origin: {origin}\r\nVary: Origin\r\n"
        if preflight:
            extra += "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Authorization, Content-Type\r\n"
        headers = f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n{extra}\r\n".encode()
        try:
            self.request.sendall(headers + data)
        except OSError:
            pass

class IntegrationAdapter:
    def __init__(self, *, store: Store, repo: Path, session_id: Callable[[], str | None],
                 project: Callable[..., dict[str, Any]], connection_file: Path,
                 allowed_origins: Sequence[str] = (), stop: Callable[[], object] | None = None,
                 is_active: Callable[[], bool] | None = None):
        if (stop is None) != (is_active is None):
            raise ValueError("stop and activity callback must be supplied together")
        if len(allowed_origins) > 8 or any(not _ORIGIN.fullmatch(origin) or int(origin.rsplit(":", 1)[1]) > 65535 for origin in allowed_origins):
            raise ValueError("invalid origin")
        self.store, self.repo, self.session_getter, self.project = store, Path(repo), session_id, project
        self.connection_file = Path(connection_file)
        self.allowed_origins = frozenset(allowed_origins)
        self.stop, self.is_active = stop, is_active
        self.server_id, self.token = secrets.token_hex(16), secrets.token_urlsafe(32)
        self.store_id = store.integration_store_id()
        self.generation = secrets.token_hex(16) if stop else None
        self.lock, self.build_lock = threading.RLock(), threading.Lock()
        self.rates: deque[float] = deque()
        self.build_rates: deque[float] = deque()
        self.snapshots: dict[str, tuple[float, dict]] = {}
        self.observer: sqlite3.Connection | None = None
        self.observer_identity: tuple[int, int] | None = None
        self.reusable: tuple[float, tuple[tuple[int, int], int], dict, dict] | None = None
        self.selected: str | None = None
        self.stopping, self.closed = False, False
        self.server: _Server | None = None
        self.thread: threading.Thread | None = None
        self.dirfd: int | None = None
        self.filefd: int | None = None

    def _session(self) -> str | None:
        with self.lock:
            current = self.session_getter()
            if self.selected is None and current is not None:
                self.selected = current
            elif self.selected is not None and current != self.selected:
                raise _Failure(503, "unavailable", "Session is unavailable.", retryable=True)
            return self.selected

    def _controller(self) -> dict:
        active = bool(self.stop and not self.closed and not self.stopping and self.is_active and self.is_active())
        return {"availability": "available" if active else "unavailable", "generation": self.generation if active else None}

    def _verify_directory(self) -> None:
        assert self.dirfd is not None
        checkfd, _ = _secure_directory(self.connection_file, self.repo)
        try:
            original, current = os.fstat(self.dirfd), os.fstat(checkfd)
            if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("rendezvous directory replaced")
        finally:
            os.close(checkfd)

    def start(self) -> None:
        if self.server is not None or self.closed:
            raise ValueError("adapter already started or closed")
        fd, name = _secure_directory(self.connection_file, self.repo)
        self.dirfd = fd
        temp = ".mentat-" + secrets.token_hex(16)
        temp_created = False
        try:
            self.server = _Server(self)
            self._verify_directory()
            filefd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            temp_created = True
            try:
                info = os.fstat(filefd)
                if info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                    raise ValueError("invalid rendezvous file")
                contents = _encoded({"api_version": 1, "base_url": f"http://127.0.0.1:{self.server.server_address[1]}", "capability": self.token, "server_id": self.server_id, "session_id": self._session()})
                with os.fdopen(filefd, "wb", closefd=False) as stream:
                    stream.write(contents)
                    stream.flush()
                os.fsync(filefd)
                self._verify_directory()
                os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                # Keep the published inode alive even if its pathname is replaced.
                self.filefd = filefd
                os.unlink(temp, dir_fd=fd)
                temp_created = False
                os.fsync(fd)
            finally:
                if self.filefd is None:
                    os.close(filefd)
            self.thread = threading.Thread(target=self.server.serve_forever, name="mentat-integration", daemon=True)
            self.thread.start()
        except BaseException:
            if temp_created:
                try:
                    os.unlink(temp, dir_fd=fd)
                except OSError:
                    pass
            self.close()
            raise

    def wait(self) -> None:
        if self.thread is None:
            raise ValueError("adapter not started")
        while self.thread.is_alive():
            self.thread.join(0.5)

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            if self.server:
                if self.thread and self.thread.is_alive():
                    self.server.shutdown()
                    self.thread.join()
                self.server.server_close()
            if self.dirfd is not None and self.filefd is not None:
                try:
                    self._verify_directory()
                    info = os.stat(self.connection_file.name, dir_fd=self.dirfd, follow_symlinks=False)
                    original = os.fstat(self.filefd)
                    if (original.st_dev, original.st_ino) == (info.st_dev, info.st_ino) and stat.S_ISREG(info.st_mode):
                        os.unlink(self.connection_file.name, dir_fd=self.dirfd)
                        os.fsync(self.dirfd)
                except (OSError, ValueError):
                    pass
        finally:
            with self.build_lock:
                if self.observer is not None:
                    self.observer.close()
                    self.observer = None
            if self.filefd is not None:
                os.close(self.filefd)
                self.filefd = None
            if self.dirfd is not None:
                os.close(self.dirfd)
                self.dirfd = None

    def admit(self, *, build: bool = False) -> bool:
        with self.lock:
            queue = self.build_rates if build else self.rates
            now = time.monotonic()
            while queue and queue[0] <= now - 1:
                queue.popleft()
            if len(queue) >= (2 if build else 30):
                return False
            queue.append(now)
            return True

    def _envelope(self, session: str | None) -> dict:
        return {"api_version": 1, "server_id": self.server_id, "store_id": self.store_id, "session_id": session}

    def _ledger_version(self) -> tuple[tuple[int, int], int] | None:
        """Observe commits on a dedicated connection; uncertainty disables reuse."""
        try:
            stat = self.store.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if self.observer is None or identity != self.observer_identity:
                if self.observer is not None:
                    self.observer.close()
                self.reusable = None
                self.observer = sqlite3.connect(self.store.path.as_uri() + "?mode=ro", uri=True, timeout=2, check_same_thread=False)
                self.observer_identity = identity
            version = self.observer.execute("PRAGMA data_version").fetchone()[0]
            stat = self.store.path.stat()
            if (stat.st_dev, stat.st_ino) != identity:
                raise OSError("ledger replaced")
            return identity, version
        except (OSError, sqlite3.Error, TypeError, IndexError):
            if self.observer is not None:
                self.observer.close()
            self.observer = None
            self.observer_identity = None
            self.reusable = None
            return None

    def _cursor(self, session: str, seq: int) -> str:
        body = _encoded([self.store_id, session, self.server_id, seq])
        signature = hmac.new(self.token.encode(), body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")

    def _parse_cursor(self, cursor: str, session: str) -> int:
        try:
            if len(cursor) > 1024:
                raise ValueError
            data = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            body, signature = data[:-32], data[-32:]
            if not hmac.compare_digest(signature, hmac.new(self.token.encode(), body, hashlib.sha256).digest()):
                raise ValueError
            value = json.loads(body)
            if not isinstance(value, list) or len(value) != 4 or value[:3] != [self.store_id, session, self.server_id] or type(value[3]) is not int or value[3] < 0:
                raise ValueError
            return value[3]
        except (ValueError, TypeError, UnicodeError):
            raise _Failure(409, "reset_required", "Reacquire a snapshot for this server generation.", reset=True) from None

    def route(self, method: str, target: str, body: bytes, started: float) -> tuple[int, dict]:
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise _Failure(400, "invalid_request", "Invalid request.")
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if any(len(value) != 1 for value in query.values()):
            raise _Failure(400, "invalid_request", "Invalid request.")
        session = self._session()
        common = self._envelope(session)
        if method == "GET" and parsed.path == "/v1/capabilities" and not query:
            return 200, {**common, "capabilities": {"snapshots": True, "changes": True, "feedback": True, "stop": self.stop is not None, "raw_artifacts": False}, "controller": self._controller(), "limits": _LIMITS, "restart": "reacquire_snapshot"}
        if session is None:
            raise _Failure(503, "unavailable", "Session is starting.", retryable=True)
        if method == "GET" and parsed.path == "/v1/snapshot" and not query:
            if not self.build_lock.acquire(blocking=False):
                raise _Failure(429, "capacity", "Snapshot builder is busy.", retryable=True)
            try:
                version = self._ledger_version()
                controller = self._controller()
                if self.reusable is not None:
                    observed, saved_version, saved_controller, response = self.reusable
                    saved = self.snapshots.get(response["snapshot_id"])
                    if (version is not None and version == saved_version and controller == saved_controller
                            and time.monotonic() - observed < 5 and response["expires_at"] > time.time()
                            and saved is not None and saved[0] > time.time()):
                        return 200, response
                if not self.admit(build=True):
                    raise _Failure(429, "capacity", "Snapshot builder is busy.", retryable=True)
                observed = time.monotonic()
                try:
                    raw = self.store.integration_snapshot(session, deadline=started + 10)
                except TimeoutError:
                    raise _Failure(503, "unavailable", "Snapshot timed out.", retryable=True) from None
                projection = self.project(raw, timeout=max(0.01, 10 - (time.monotonic() - started)))
                if time.monotonic() - started > 10:
                    raise _Failure(503, "unavailable", "Snapshot timed out.", retryable=True)
                if not isinstance(projection, dict) or any(not isinstance(projection.get(key), list) for key in _RESOURCES):
                    raise ValueError("invalid projection")
                projection["session"]["controller"] = self._controller()
                if len(_encoded(projection)) > 8388608:
                    raise _Failure(413, "capacity", "Snapshot exceeds capacity.")
                for key in _RESOURCES:
                    if any(len(_encoded(item)) > 240000 for item in projection[key]):
                        raise _Failure(413, "capacity", "Snapshot item exceeds capacity.")
                ident, now = secrets.token_urlsafe(18), time.time()
                with self.lock:
                    self.snapshots = {k: v for k, v in self.snapshots.items() if v[0] > now}
                    while len(self.snapshots) >= 2:
                        self.snapshots.pop(next(iter(self.snapshots)))
                    self.snapshots[ident] = (now + 30, projection)
                response = {**common, "snapshot_id": ident, "expires_at": now + 30, "observed_at": now, "refresh_seconds": 5,
                             "high_water": raw["revision"], "cursor": self._cursor(session, raw["revision"]), "session": projection["session"], "freshness": projection["freshness"], "counts": {k: len(projection[k]) for k in _RESOURCES}}
                after = self._ledger_version()
                if version is not None and version == after and controller == projection["session"]["controller"]:
                    self.reusable = (observed, version, controller, response)
                else:
                    self.reusable = None
                return 200, response
            except IntegrationCapacityError:
                raise _Failure(413, "capacity", "Snapshot exceeds capacity.") from None
            except (OSError, RuntimeError, TypeError, ValueError):
                raise _Failure(503, "unavailable", "Snapshot is unavailable.", retryable=True) from None
            finally:
                self.build_lock.release()
        parts = parsed.path.split("/")
        if method == "GET" and len(parts) == 5 and parts[1:3] == ["v1", "snapshots"] and parts[4] in _RESOURCES and set(query) <= {"offset", "limit"}:
            offset, limit = self._page_args(query)
            with self.lock:
                saved = self.snapshots.get(parts[3])
                if saved is None or saved[0] <= time.time():
                    self.snapshots.pop(parts[3], None)
                    raise _Failure(409, "reset_required", "Reacquire a snapshot for this server generation.", reset=True)
                items = saved[1][parts[4]]
                page = items[offset:offset + limit]
            # Bound the serialized response by reducing the page, without dropping items.
            while page and len(_encoded(page)) > 240000:
                page.pop()
            if offset < len(items) and not page:
                raise _Failure(413, "capacity", "Snapshot item exceeds capacity.")
            end = offset + len(page)
            return 200, {**common, "snapshot_id": parts[3], "resource": parts[4], "offset": offset, "total": len(items), "next_offset": end if end < len(items) else None, "items": page}
        if method == "GET" and parsed.path == "/v1/changes" and set(query) <= {"cursor", "limit"} and "cursor" in query:
            _, limit = self._page_args(query, offset_allowed=False)
            after = self._parse_cursor(query["cursor"][0], session)
            try:
                high, rows, more = self.store.integration_changes(session, after, limit)
            except ValueError:
                raise _Failure(409, "reset_required", "Reacquire a snapshot for this server generation.", reset=True) from None
            notices = [{"seq": row["seq"], "kind": row["kind"]} if row["kind"] in _EVENT_KINDS else {"seq": row["seq"], "kind": "state_changed", "unavailable_fields": ["kind"]} for row in rows]
            return 200, {**common, "high_water": high, "cursor": self._cursor(session, rows[-1]["seq"] if rows else after), "has_more": more, "events": notices}
        if method == "POST" and parsed.path == "/v1/controls" and not query:
            value = json.loads(body, object_pairs_hook=_unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            _check_json(value)
            if not isinstance(value, dict) or set(value) != {"request_id", "action", "payload"} or not isinstance(value["request_id"], str) or not _KEY.fullmatch(value["request_id"]) or value["action"] not in ("feedback", "stop") or not isinstance(value["payload"], dict):
                raise _Failure(400, "invalid_request", "Invalid request.")
            action, payload = value["action"], value["payload"]
            if action == "feedback":
                if set(payload) != {"message"} or not isinstance(payload["message"], str) or not payload["message"].strip() or len(payload["message"].encode()) > 8192:
                    raise _Failure(400, "invalid_request", "Invalid request.")
            elif payload:
                raise _Failure(400, "invalid_request", "Invalid request.")
            with self.lock:
                if self.closed:
                    raise _Failure(503, "unavailable", "Service unavailable.", retryable=True)
                active = bool(self.stop and not self.closed and not self.stopping and self.is_active and self.is_active())
                try:
                    receipt, created = self.store.integration_control(session, value["request_id"], action, payload, self.generation, active=active)
                except ValueError as exc:
                    if str(exc) == "control capacity":
                        raise _Failure(429, "capacity", "Control limit reached.") from None
                    raise _Failure(409, "conflict", "Control unavailable or conflicts with a prior request.") from None
                if action == "stop" and created:
                    self.stopping = True
                    assert self.stop is not None
                    try:
                        self.stop()
                    except Exception:
                        pass
            return 202, {**common, "receipt": receipt}
        raise _Failure(404, "not_found", "Resource not found.")

    @staticmethod
    def _page_args(query: dict, *, offset_allowed: bool = True) -> tuple[int, int]:
        offset = query.get("offset", ["0"])[0] if offset_allowed else "0"
        limit = query.get("limit", ["100"])[0]
        if not re.fullmatch(r"0|[1-9][0-9]{0,8}", offset) or not re.fullmatch(r"[1-9][0-9]{0,2}", limit) or int(limit) > 100:
            raise _Failure(400, "invalid_request", "Invalid request.")
        return int(offset), int(limit)


def create_adapter(*, store: Store, repo: Path, session_id: Callable[[], str | None],
                   project: Callable[..., dict[str, Any]], connection_file: Path,
                   allowed_origins: Sequence[str] = (), stop: Callable[[], object] | None = None,
                   is_active: Callable[[], bool] | None = None) -> IntegrationAdapter:
    return IntegrationAdapter(store=store, repo=repo, session_id=session_id, project=project,
                              connection_file=connection_file, allowed_origins=allowed_origins,
                              stop=stop, is_active=is_active)
