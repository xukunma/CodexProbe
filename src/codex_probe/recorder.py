"""Forward streaming HTTP calls and store one ordered JSONL file per session."""
import asyncio
from concurrent.futures import Future
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
from tempfile import mkstemp
from threading import Thread
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

from .bodies import capture

LOG = logging.getLogger(__name__)
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade"}
AUTH = {"authorization", "cookie", "chatgpt-account-id"}


def end_to_end(headers, exclude=()):
    skip = HOP | set(exclude)
    for value in headers.getall("Connection", []):
        skip.update(name.strip().lower() for name in value.split(","))
    return CIMultiDict((k, v) for k, v in headers.items() if k.lower() not in skip)


class ProxyRecorder:
    """Start a local model endpoint and return full calls in arrival order on stop."""

    def __init__(self, config: dict):
        self.config = dict(config)
        self.backend = self.config["base_url"].rstrip("/")
        url = urlsplit(self.backend)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be an HTTP(S) backend URL without credentials or query")
        self.thread = None
        self.calls = []
        self.log_path = None

    def start(self) -> str:
        """Open a session and return its local /v1 URL."""
        if self.thread is not None:
            raise RuntimeError("session already running")
        self.calls = []
        self.received = 0
        self.waiting_to_write = {}
        self.written = 0
        self.loop = asyncio.new_event_loop()
        ready = Future()
        self.thread = Thread(target=self._serve, args=(ready,), daemon=True)
        self.thread.start()
        try:
            return ready.result()
        except BaseException:
            self.thread.join()
            self.thread = None
            raise

    def _serve(self, ready):
        asyncio.set_event_loop(self.loop)
        try:
            ready.set_result(self.loop.run_until_complete(self._open()))
            self.loop.run_forever()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self.loop.close()

    async def _open(self):
        host = self.config.get("listen_host", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("choose an IPv4 loopback listener")
        folder = Path(self.config["log_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        self.log_path = folder / f"{uuid4().hex}.jsonl"
        self.log = open(self.log_path, "x+", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600))
        self.active = set()
        self.client = aiohttp.ClientSession(auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
            skip_auto_headers={"Accept-Encoding", "Accept", "User-Agent", "Content-Type"},
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=self.config.get("timeout", 180)))
        app = web.Application(client_max_size=0)
        app.router.add_route("*", "/v1/{tail:.*}", self._forward)
        app.on_shutdown.append(self._drain)
        self.runner = web.AppRunner(app, auto_decompress=False, access_log=None)
        try:
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", self.config.get("listen_port", 0))
            await site.start()
        except BaseException:
            await self._close()
            raise
        return f"http://127.0.0.1:{self.runner.addresses[0][1]}/v1"

    async def _forward(self, request):
        task = asyncio.current_task()
        self.active.add(task)
        track = request.method == "POST"
        index = self.received
        if track:
            self.received += 1
        started = monotonic()
        record = {"sequence": index + 1, "timestamp": datetime.now(timezone.utc).isoformat(),
                  "backend": self.backend, "wire_api": self.config.get("wire_api", "responses"),
                  "method": request.method, "path": request.raw_path, "status": None,
                  "request": {}, "complete": False}
        payload = bytearray()
        encoding = ""
        outgoing = None
        try:
            raw = await request.read()
            if track:
                record["request"] = capture(raw, request.headers.get("Content-Encoding", ""))
            excluded = {"host"}
            if not self.config.get("forward_auth", False):
                excluded.update(AUTH)
            headers = end_to_end(request.headers, excluded)
            if env_key := self.config.get("env_key"):
                headers["Authorization"] = "Bearer " + os.environ[env_key]
            target = URL(self.backend + request.raw_path[len("/v1"):], encoded=True)
            async with self.client.request(request.method, target, data=raw, headers=headers,
                                           allow_redirects=False) as upstream:
                record["status"] = upstream.status
                record["content_type"] = upstream.headers.get("Content-Type", "")
                encoding = upstream.headers.get("Content-Encoding", "")
                outgoing = web.StreamResponse(status=upstream.status, reason=upstream.reason,
                                              headers=end_to_end(upstream.headers))
                client_connected = request.transport is not None and not request.transport.is_closing()
                if client_connected:
                    try:
                        await outgoing.prepare(request)
                    except ConnectionError:
                        client_connected = False
                if not client_connected:
                    record["downstream_closed"] = True
                async for chunk in upstream.content.iter_any():
                    if track:
                        payload.extend(chunk)
                    if client_connected:
                        try:
                            await outgoing.write(chunk)
                        except ConnectionError:
                            client_connected = False
                            record["downstream_closed"] = True
                if client_connected:
                    try:
                        await outgoing.write_eof()
                    except ConnectionError:
                        record["downstream_closed"] = True
                record["complete"] = True
                return outgoing
        except (Exception, asyncio.CancelledError) as exc:
            record["error"] = type(exc).__name__
            if outgoing is not None and outgoing.prepared and request.transport:
                request.transport.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return web.Response(status=502, text="Upstream transfer failed")
        finally:
            self.active.discard(task)
            if track:
                record["response"] = capture(bytes(payload), encoding)
                record["elapsed_ms"] = round((monotonic() - started) * 1000, 2)
                self.waiting_to_write[index] = record
                while self.written in self.waiting_to_write:
                    self.log.write(json.dumps(self.waiting_to_write[self.written], ensure_ascii=False) + "\n")
                    del self.waiting_to_write[self.written]
                    self.written += 1
                self.log.flush()
                LOG.info("Call %s finished in %s ms", index + 1, record["elapsed_ms"])

    async def _drain(self, app):
        # AppRunner stops accepting connections before invoking on_shutdown.
        if self.active:
            _, pending = await asyncio.wait(self.active, timeout=self.config.get("drain_timeout"))
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _close(self):
        try:
            await self.runner.cleanup()
        finally:
            await self.client.close()
            try:
                self._publish_log()
            finally:
                self.log.close()

    def _publish_log(self):
        # A synced directory can replace the named file while its descriptor is open.
        # Recover the actual journal before releasing the descriptor.
        self.log.flush()
        try:
            if os.path.samestat(os.fstat(self.log.fileno()), self.log_path.stat()):
                return
        except FileNotFoundError:
            pass
        self.log.seek(0)
        fd, name = mkstemp(dir=self.log_path.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as snapshot:
                shutil.copyfileobj(self.log, snapshot)
            temp.replace(self.log_path)
        finally:
            temp.unlink(missing_ok=True)

    def stop(self) -> list[dict]:
        """Drain pending requests, close the server and return this session's calls."""
        if self.thread is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._close(), self.loop).result()
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join()
                self.thread = None
            with self.log_path.open(encoding="utf-8") as saved:
                self.calls = [json.loads(line) for line in saved]
            if len(self.calls) != self.received:
                raise OSError(f"Saved {len(self.calls)} of {self.received} calls")
        return list(self.calls)
