from compression import zstd
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
from urllib.parse import urlsplit

from codex_probe import ProxyRecorder


@contextmanager
def session(tmp_path, handler, *, hostname="127.0.0.1", **config):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    backend = f"http://{hostname}:{server.server_port}/api"
    recorder = ProxyRecorder({"base_url": backend, "log_dir": tmp_path, **config})
    address = urlsplit(recorder.start())
    try:
        yield recorder, address, backend
    finally:
        recorder.stop()
        server.shutdown()
        server.server_close()
        worker.join()


def post(address, data=b"{}", headers=None):
    conn = http.client.HTTPConnection(address.hostname, address.port, timeout=6)
    conn.request("POST", "/v1/responses", data, headers or {})
    return conn, conn.getresponse()


def test_stream_is_live_and_lossless(tmp_path):
    release = Event()
    first = b"event: response.output_text.delta\ndata: "
    last = 'Hello—world\n\ndata: [DONE]\n\n'.encode()
    raw = b'{"model":"sample", "stream":true}'
    request_bytes = zstd.compress(raw)
    observed = {}

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            observed["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            observed["path"] = self.path
            observed["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            if release.wait(5):
                self.wfile.write(last)

    with session(tmp_path, Backend) as (r, addr, backend):
        conn, resp = post(addr, request_bytes, {"Content-Encoding": "zstd", "Authorization": "Bearer dummy"})
        assert resp.read(len(first)) == first
        release.set()
        assert resp.read() == last
        conn.close()
        calls = r.stop()
        assert len(calls) == 1
        call = calls[0]
        assert observed == {"body": request_bytes, "path": "/api/responses", "auth": None}
        assert call["request"]["text"] == raw.decode()
        assert call["response"]["text"] == (first + last).decode()
        assert call["backend"] == backend and call["complete"] and call["status"] == 200
        assert call["elapsed_ms"] >= 0 and call["timestamp"]
        assert json.loads(r.log_path.read_text()) == call
        assert "Bearer dummy" not in r.log_path.read_text()


def test_incomplete_upstream(tmp_path):
    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Length", "20")
            self.end_headers()
            self.wfile.write(b"partial")

    with session(tmp_path, Backend) as (r, addr, _):
        conn, resp = post(addr)
        try:
            resp.read()
        except http.client.IncompleteRead:
            pass
        conn.close()
        call, = r.stop()
        assert not call["complete"] and call["error"]
        assert call["response"]["text"] == "partial"


def test_arrival_order_and_error_status(tmp_path):
    entered, release = Event(), Event()

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if body == b"one":
                entered.set()
                release.wait(5)
            self.send_response(429)
            self.end_headers()
            self.wfile.write(body)

    def finish(address, body):
        conn, resp = post(address, body)
        assert resp.status == 429 and resp.read() == body
        conn.close()

    with session(tmp_path, Backend) as (r, addr, _), ThreadPoolExecutor() as pool:
        first = pool.submit(finish, addr, b"one")
        assert entered.wait(5)
        finish(addr, b"two")
        release.set()
        first.result()
        calls = r.stop()
        assert [c["sequence"] for c in calls] == [1, 2]
        assert [c["response"]["text"] for c in calls] == ["one", "two"]
        assert [json.loads(line) for line in r.log_path.read_text().splitlines()] == calls


def test_upstream_tail_survives_client_disconnect(tmp_path):
    release = Event()
    prefix = b"data: completed\n\n"
    tail = b": final upstream bytes\n\n" * 8192

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(prefix)
            self.wfile.flush()
            if release.wait(5):
                self.wfile.write(tail)

    with session(tmp_path, Backend) as (r, addr, _):
        conn, resp = post(addr)
        assert resp.read(len(prefix)) == prefix
        resp.close()
        conn.close()
        release.set()
        call, = r.stop()
        assert call["complete"]
        assert call["response"]["text"].encode() == prefix + tail


def test_early_disconnect_keeps_response(tmp_path):
    import time
    started, release = Event(), Event()
    body = b"data: completed\n\n"

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            started.set()
            assert release.wait(5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    with session(tmp_path, Backend) as (r, addr, _):
        conn = http.client.HTTPConnection(addr.hostname, addr.port)
        conn.request("POST", "/v1/responses", b"{}")
        assert started.wait(5)
        conn.close()
        # Allow the local TCP close to be observed before the backend emits headers.
        time.sleep(0.1)
        release.set()
        call, = r.stop()
        assert call["complete"] and call["downstream_closed"]
        assert call["response"]["text"].encode() == body


def test_backend_cookies_are_not_replayed(tmp_path):
    received = []

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(self.headers.get("Cookie"))
            self.send_response(200)
            self.send_header("Set-Cookie", "format=compact; Path=/")
            self.end_headers()

    # A DNS hostname exercises cookie storage, which aiohttp disables for IP literals.
    with session(tmp_path, Backend, hostname="localhost") as (r, addr, _):
        for _ in range(2):
            conn, resp = post(addr)
            assert resp.read() == b""
            conn.close()
        assert len(r.stop()) == 2
    assert received == [None, None]


def test_configured_drain_deadline_records_partial_response(tmp_path):
    release = Event()

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"prefix")
            self.wfile.flush()
            release.wait(5)

    with session(tmp_path, Backend, drain_timeout=0.1) as (r, addr, _):
        conn, resp = post(addr)
        try:
            assert resp.read(6) == b"prefix"
            call, = r.stop()
            assert call["complete"] is False
            assert call["error"] == "CancelledError"
            assert call["response"]["text"] == "prefix"
        finally:
            release.set()
            resp.close()
            conn.close()


def test_graceful_drain_outlasts_framework_shutdown(tmp_path, monkeypatch):
    from threading import Timer
    from codex_probe import recorder as module

    # Compress the HTTP library's shutdown window to keep this regression fast.
    app_runner = module.web.AppRunner
    def short_http_shutdown(*args, **kwargs):
        kwargs["shutdown_timeout"] = 0.02
        return app_runner(*args, **kwargs)
    monkeypatch.setattr(module.web, "AppRunner", short_http_shutdown)
    release = Event()

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"first")
            self.wfile.flush()
            if release.wait(3):
                self.wfile.write(b"last")

    with session(tmp_path, Backend) as (r, addr, _):
        conn, resp = post(addr)
        assert resp.read(5) == b"first"
        timer = Timer(0.25, release.set)
        timer.start()
        try:
            call, = r.stop()
            assert call["complete"] and call["response"]["text"] == "firstlast"
        finally:
            release.set()
            timer.join()
            resp.close()
            conn.close()


def test_replaced_log_path_is_recovered_on_stop(tmp_path):
    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"recorded body")

    with session(tmp_path, Backend) as (r, addr, _):
        conn, response = post(addr)
        assert response.read() == b"recorded body"
        conn.close()
        replacement = tmp_path / "empty-copy"
        replacement.touch()
        replacement.replace(r.log_path)
        call, = r.stop()
        assert json.loads(r.log_path.read_text()) == call
        assert call["response"]["text"] == "recorded body"

def test_structured_function_call_passthrough(tmp_path):
    function_call = {
        "type": "function_call",
        "id": "fc_001",
        "name": "exec_command",
        "call_id": "call_123",
        "arguments": '{"cmd":"pwd"}',
        "status": "completed"
    }

    response_body = json.dumps({
        "id": "resp_001",
        "object": "response",
        "status": "completed",
        "output": [function_call]
    }).encode("utf-8")

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(
                int(self.headers["Content-Length"])
            )

            self.send_response(200)
            self.send_header(
                "Content-Type", "application/json"
            )
            self.send_header(
                "Content-Length", str(len(response_body))
            )
            self.end_headers()
            self.wfile.write(response_body)

    with session(tmp_path, Backend) as (recorder, addr, _):

        request_body = b'{"model":"mock-model","input":"run pwd"}'

        conn, resp = post(
            addr,
            request_body,
            {"Content-Type": "application/json"}
        )

        assert resp.status == 200

        received_body = resp.read()
        conn.close()

        calls = recorder.stop()

        assert received_body == response_body

        result = json.loads(received_body)
        assert result["output"][0] == function_call

        assert len(calls) == 1
        assert calls[0]["complete"] is True
        assert calls[0]["response"]["text"] == response_body.decode("utf-8")

        saved = json.loads(recorder.log_path.read_text())
        assert saved["response"]["text"] == response_body.decode("utf-8")

def test_gzip_response_headers_and_body(tmp_path):
    import gzip

    original = b'{"status":"completed","output":[]}'

    compressed = gzip.compress(original)

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(
                int(self.headers["Content-Length"])
            )
            # response headers
            self.send_response(200)
            self.send_header(
                "Content-Type", "application/json"
            )
            self.send_header(
                "Content-Encoding", "gzip"
            )
            self.send_header(
                "Content-Length", str(len(compressed))
            )
            # hop-by-hop headers
            self.send_header(
                "Connection", "close, X-Temporary-Hop"
            )
            self.send_header(
                "X-Temporary-Hop", "internal"
            )

            self.end_headers()
            self.wfile.write(compressed)

    with session(tmp_path, Backend) as (recorder, addr, _):
        conn, resp = post(addr)

        received = resp.read()

        assert resp.status == 200
        assert received == compressed
        assert gzip.decompress(received) == original

        assert resp.getheader("Content-Type") == "application/json"
        assert resp.getheader("Content-Encoding") == "gzip"
        assert int(resp.getheader("Content-Length")) == len(received)

        assert resp.getheader("X-Temporary-Hop") is None

        conn.close()

        calls = recorder.stop()
        assert len(calls) == 1
        assert calls[0]["complete"] is True

        import base64
        saved_bytes = base64.b64decode(
            calls[0]["response"]["base64"]
        )
        assert saved_bytes == compressed
        assert calls[0]["response"]["text"] == original.decode()

def test_backend_auth_replacement(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_BACKEND_KEY", "backend-secret")

    observed = {}

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            observed["auth"] = self.headers.get("Authorization")

            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

    with session(
        tmp_path, Backend, env_key="TEST_BACKEND_KEY"
    ) as (recorder, addr, _):

        conn, resp = post(
            addr,
            b"{}",
            {"Authorization": "Bearer client-secret"}
        )

        assert resp.status == 200
        assert resp.read() == b"OK"
        conn.close()

        calls = recorder.stop()

        assert observed["auth"] == "Bearer backend-secret"


        saved = recorder.log_path.read_text()
        assert "client-secret" not in saved
        assert "backend-secret" not in saved

        assert len(calls) == 1

def test_large_payload_and_log_schema(tmp_path):
    request_body = json.dumps({
        "model": "mock-model",
        "input": [{"role": "user", "content": "A" * 100000}],
        "tools": [{
            "type": "function",
            "name": "exec_command",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"}
                },
                "required": ["cmd"]
            }
        }]
    }).encode("utf-8")

    response_body = b'{"status":"completed"}'
    observed = {}

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            observed["body"] = self.rfile.read(length)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

    with session(tmp_path, Backend) as (recorder, addr, _):
        conn, resp = post(
            addr, request_body,
            {"Content-Type": "application/json"}
        )
        assert resp.status == 200
        assert resp.read() == response_body
        conn.close()

        calls = recorder.stop()
        assert len(calls) == 1

        call = calls[0]

        # verify request is not truncated
        assert observed["body"] == request_body
        assert call["request"]["text"].encode() == request_body

        # verify log fields
        required = {
            "sequence", "timestamp", "backend",
            "method", "path", "status",
            "request", "response",
            "elapsed_ms", "complete"
        }
        assert required.issubset(call)
        assert call["sequence"] == 1
        assert call["status"] == 200
        assert call["complete"] is True
        assert isinstance(call["elapsed_ms"], (int, float))

        # verify persisted log
        saved = json.loads(recorder.log_path.read_text())
        assert saved == call


def test_no_semantic_rewriting(tmp_path):
    tool_text = json.dumps({
        "name": "exec_command",
        "arguments": {"cmd": "pwd"}
    })

    response_body = json.dumps({
        "object": "response",
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": tool_text
            }]
        }]
    }).encode("utf-8")

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response_body)

    with session(tmp_path, Backend) as (recorder, addr, _):
        conn, resp = post(addr)
        received = resp.read()
        conn.close()

        calls = recorder.stop()

        # Verify response bytes are consistent
        assert received == response_body

        # Still a message, not a function_call
        result = json.loads(received)
        item = result["output"][0]

        assert item["type"] == "message"
        assert item["content"][0]["type"] == "output_text"
        assert item["content"][0]["text"] == tool_text

        # Log preserves original response
        assert calls[0]["response"]["text"] == response_body.decode()
        assert calls[0]["complete"] is True

def test_fragmented_sse_bytes(tmp_path):
    release = Event()

    sse = (
        'event: response.output_text.delta\n'
        'data: {"delta":"Hello—world"}\n\n'
        'event: response.completed\n'
        'data: {"type":"response.completed"}\n\n'
    ).encode("utf-8")

    # Split the three-byte UTF-8 em dash across network chunks.
    pos = sse.index("—".encode("utf-8"))
    first = sse[:pos + 1]
    remaining = sse[pos + 1:]

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            self.wfile.write(first)
            self.wfile.flush()

            if release.wait(5):
                self.wfile.write(remaining)
                self.wfile.flush()

    with session(tmp_path, Backend) as (recorder, addr, _):
        conn, resp = post(addr)

        try:
            assert resp.status == 200
            assert resp.read(len(first)) == first

            release.set()
            received = first + resp.read()

            assert received == sse

            conn.close()
            calls = recorder.stop()
            assert calls[0]["response"]["text"].encode("utf-8") == sse
            assert calls[0]["complete"] is True

        finally:
            release.set()
            conn.close()



def test_concurrent_streaming_isolation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event

    n = 10

    # Barrier to ensure all clients have received the first segment
    first_received = Barrier(n)

    # Event to control when Backend sends the second segment
    release = Event()

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            request_id = int(self.rfile.read(length).decode())

            first = f"data: start-{request_id}\n\n".encode()
            second = f"data: end-{request_id}\n\n".encode()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header(
                "Content-Length",
                str(len(first) + len(second))
            )
            self.end_headers()

            # Send the first segment first
            self.wfile.write(first)
            self.wfile.flush()

            # Wait for all clients to receive the first segment before continuing
            if release.wait(15):
                self.wfile.write(second)
                self.wfile.flush()

    def client(request_id, addr):
        conn, resp = post(addr, str(request_id).encode())

        try:
            assert resp.status == 200

            first = f"data: start-{request_id}\n\n".encode()
            second = f"data: end-{request_id}\n\n".encode()

            # Must receive the first segment before continuing
            assert resp.read(len(first)) == first

            # Wait for other 9 clients to receive the first segment
            first_received.wait(timeout=15)

            # Allow Backend to continue only after all clients have received the first segment
            if request_id == 0:
                release.set()

            # Read and verify the second segment
            assert resp.read() == second
            return request_id

        finally:
            conn.close()

    with session(tmp_path, Backend) as (recorder, addr, _):
        try:
            # Start 10 client threads simultaneously
            with ThreadPoolExecutor(max_workers=n) as pool:
                results = list(
                    pool.map(lambda i: client(i, addr), range(n))
                )
        finally:
            release.set()

        assert sorted(results) == list(range(n))

        calls = recorder.stop()

        # Must save 10 independent calls
        assert len(calls) == n

        # Each log's request and response must be correctly matched
        for call in calls:
            request_id = int(call["request"]["text"])

            expected = (
                f"data: start-{request_id}\n\n"
                f"data: end-{request_id}\n\n"
            )

            assert call["status"] == 200
            assert call["complete"] is True
            assert call["response"]["text"] == expected

        # Each log has a unique sequence number
        sequences = [call["sequence"] for call in calls]
        assert len(set(sequences)) == n


def test_failed_stream_isolated_from_parallel_request(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from http.client import IncompleteRead

    partial_sent = Event()
    close_bad = Event()

    bad_chunk = b"data: partial\n\n"
    good_stream = b"data: fine\n\ndata: done\n\n"

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")

            if body == b"bad":
                # Claim to send more data, but only send a partial chunk
                self.send_header(
                    "Content-Length", str(len(bad_chunk) + 30)
                )
                self.end_headers()

                self.wfile.write(bad_chunk)
                self.wfile.flush()
                partial_sent.set()

                # Keep the connection open, then prematurely close it
                close_bad.wait(8)
                self.close_connection = True

            else:
                self.send_header(
                    "Content-Length", str(len(good_stream))
                )
                self.end_headers()
                self.wfile.write(good_stream)
                self.wfile.flush()

    def client(addr, payload):
        conn, resp = post(addr, payload)

        try:
            try:
                data = resp.read()
            except IncompleteRead as exc:
                # Keep the received bytes when the response is too short
                data = exc.partial

            return resp.status, data
        finally:
            conn.close()

    with session(tmp_path, Backend) as (recorder, addr, _):
        with ThreadPoolExecutor(max_workers=2) as pool:
            bad_future = pool.submit(client, addr, b"bad")

            try:
                assert partial_sent.wait(8)

                good_future = pool.submit(client, addr, b"good")

                # B must complete independently while A is not yet finished
                good_status, good_data = good_future.result(timeout=8)

                assert good_status == 200
                assert good_data == good_stream
            finally:
                close_bad.set()

            bad_status, bad_data = bad_future.result(timeout=8)

        assert bad_status == 200
        assert bad_data == bad_chunk

        calls = recorder.stop()
        assert len(calls) == 2

        by_request = {
            call["request"]["text"]: call
            for call in calls
        }

        # Healthy request must be complete
        assert by_request["good"]["complete"] is True
        assert by_request["good"]["response"]["text"] == good_stream.decode()

        # Failed request only records the received partial data
        assert by_request["bad"]["complete"] is False
        assert by_request["bad"]["response"]["text"] == bad_chunk.decode()


def test_shutdown_drains_parallel_streams(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event

    n = 2
    first_received = Barrier(n + 1)
    release = Event()
    stop_called = Event()

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            request_id = self.rfile.read(length).decode()

            first = f"data: start-{request_id}\n\n".encode()
            second = f"data: end-{request_id}\n\n".encode()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header(
                "Content-Length",
                str(len(first) + len(second))
            )
            self.end_headers()

            self.wfile.write(first)
            self.wfile.flush()

            # Wait until the test allows streaming to continue.
            if release.wait(10):
                self.wfile.write(second)
                self.wfile.flush()

    def client(addr, request_id):
        conn, resp = post(addr, str(request_id).encode())

        try:
            first = f"data: start-{request_id}\n\n".encode()
            second = f"data: end-{request_id}\n\n".encode()

            assert resp.status == 200
            assert resp.read(len(first)) == first

            # Wait for both clients to receive the first segment     
            first_received.wait(timeout=10)

            # Read the remaining data
            assert resp.read() == second
            return request_id

        finally:
            conn.close()

    with session(tmp_path, Backend) as (recorder, addr, _):

        def stop_probe():
            stop_called.set()
            return recorder.stop()

        with ThreadPoolExecutor(max_workers=3) as pool:
            try:
                a = pool.submit(client, addr, 0)
                b = pool.submit(client, addr, 1)

                # Wait for both clients to receive the first segment
                first_received.wait(timeout=10)

                # Initiate shutdown while streaming is still in progress
                stopping = pool.submit(stop_probe)
                assert stop_called.wait(5)

                # Allow Backend to complete the response
                release.set()

                assert sorted([
                    a.result(timeout=10),
                    b.result(timeout=10)
                ]) == [0, 1]

                calls = stopping.result(timeout=10)

            finally:
                release.set()

        assert len(calls) == 2

        for call in calls:
            request_id = call["request"]["text"]

            expected = (
                f"data: start-{request_id}\n\n"
                f"data: end-{request_id}\n\n"
            )

            assert call["complete"] is True
            assert call["response"]["text"] == expected


def test_large_sse_stream_recording(tmp_path):
    # Each SSE event is exactly 4096 bytes
    event = b"data: " + b"A" * 4088 + b"\n\n"
    assert len(event) == 4096

    # Construct 1 MiB and 5 MiB streams
    streams = {
        1: event * 256,
        5: event * 1280,
    }

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            size_mb = int(
                self.rfile.read(length).decode("ascii")
            )

            body = streams[size_mb]

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

            # Send at most 64 KiB per chunk
            for offset in range(0, len(body), 65536):
                self.wfile.write(
                    body[offset:offset + 65536]
                )

            self.wfile.flush()

    with session(tmp_path, Backend) as (recorder, addr, _):

        for size_mb in (1, 5):
            conn, resp = post(
                addr, str(size_mb).encode()
            )

            try:
                assert resp.status == 200
                assert resp.read() == streams[size_mb]
            finally:
                conn.close()

        calls = recorder.stop()

        # Both HTTP requests must have logs
        assert len(calls) == 2

        for call in calls:
            size_mb = int(call["request"]["text"])

            assert call["complete"] is True

            # Verify that the log retains the complete SSE data
            recorded = call["response"]["text"].encode("utf-8")
            assert recorded == streams[size_mb]

        # Verify JSONL logs on disk
        saved = [
            json.loads(line)
            for line in recorder.log_path.read_text().splitlines()
            if line.strip()
        ]

        assert len(saved) == 2
        assert saved == calls
