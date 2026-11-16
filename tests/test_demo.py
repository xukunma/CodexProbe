import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
import quickstart
from evidence import call_metrics, grades, stream_metrics
from verification import regular_bytes


def test_rank_transport_and_model_independently():
    before = {"ran": 7, "passed": 4, "exit_code": 1}
    after = {"ran": 7, "passed": 7, "exit_code": 0}
    calls = [{"sequence": 1, "status": 429, "complete": True},
             {"sequence": 2, "status": 200, "complete": True}]
    assert grades(calls, before, after, 0, True, True) == (True, True)
    assert grades(calls, before, before, 0, False, True) == (True, False)
    assert grades([], before, after, 0, True, True) == (False, True)


def test_stream_delta_evidence():
    delta = {"type": "response.function_call_arguments.delta", "sequence_number": 0,
             "output_index": 0, "delta": "abc"}
    terminal = {"type": "response.completed", "sequence_number": 1,
                "response": {"output": [{"type": "function_call", "arguments": "abc"}]}}
    def summarize():
        return stream_metrics("".join(f"data: {json.dumps(e)}\r\n\r\n" for e in [delta, terminal]))
    assert summarize()["deltas_match_terminal"] is True
    delta["delta"] = "xyz"
    terminal["sequence_number"] = 3
    evidence = summarize()
    assert evidence["sequence_contiguous"] is False
    assert evidence["deltas_match_terminal"] is False
    assert stream_metrics("data: {broken")["deltas_match_terminal"] is False


def test_public_evidence_excludes_body_values():
    marker = "example-private-path"
    call = {"sequence": 1, "status": 429, "complete": True, "elapsed_ms": 1,
            "request": {"text": marker}, "response": {"text": marker}}
    metrics = call_metrics(call)
    assert marker not in json.dumps(metrics)
    assert len(metrics["request_sha256"]) == 64


def test_missing_file_still_saves_result(tmp_path, monkeypatch):
    def rename(project, *args):
        (project / "checkout.py").rename(project / "renamed.py")
        return 0
    monkeypatch.setattr(quickstart, "run_codex", rename)
    monkeypatch.setattr(quickstart, "verify", lambda src, suite: {
        "ran": 7 if src else 0, "passed": 4 if src else 0, "exit_code": 1 if src else None})
    call = {"sequence": 1, "complete": True, "status": 200, "elapsed_ms": 1,
            "request": {"text": "{}"}, "response": {"text": "{}"}}
    monkeypatch.setattr(quickstart, "ProxyRecorder", lambda config: SimpleNamespace(
        start=lambda: "unused", stop=lambda: [call], log_path=Path("test.jsonl")))
    out = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["example", str(quickstart.EXAMPLES / "ollama.json"), "--output", str(out)])
    assert quickstart.main() == 1
    report = json.loads((out / "result.json").read_text())
    assert report["recording_ok"] and not report["task_ok"]
    assert report["source_changed"] is None


def test_candidate_must_be_a_regular_file(tmp_path):
    path = tmp_path / "candidate"
    assert regular_bytes(path) is None
    if sys.platform == "win32":
        pytest.skip("creating symlinks depends on Windows account privileges")
    path.symlink_to(__file__)
    assert regular_bytes(path) is None


def test_verifier_uses_read_only_fixture_and_clean_environment(tmp_path, monkeypatch):
    import shutil
    from verification import verify
    if not shutil.which("codex"):
        pytest.skip("verification integration requires Codex CLI")
    canary = tmp_path / "outside-fixture.txt"
    canary.write_text("synthetic fixture")
    monkeypatch.setenv("CHECKOUT_TEST_MARKER", "synthetic fixture")
    test_source = f"""
import os
from pathlib import Path
import unittest
class Permissions(unittest.TestCase):
    def test_fixture_and_environment(self):
        self.assertIsNone(os.getenv("CHECKOUT_TEST_MARKER"))
        self.assertEqual(Path("checkout.py").read_text(), "value = 1")
        with self.assertRaises(PermissionError):
            Path({str(canary)!r}).read_text()
        with self.assertRaises(PermissionError):
            Path("new-file").write_text("synthetic fixture")
"""
    result = verify(b"value = 1", test_source.encode())
    assert (result["exit_code"], result["ran"], result["passed"]) == (0, 1, 1), result["output"]
