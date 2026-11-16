"""Summarize the checkout example's Responses streams without exporting message bodies."""
import base64
from collections import defaultdict
from hashlib import sha256
import json
from urllib.parse import urlsplit


def body_hash(body: dict) -> str:
    raw = base64.b64decode(body["base64"]) if "base64" in body else body.get("text", "").encode()
    return sha256(raw).hexdigest()


def events(text: str):
    """Read SSE data fields, including events with multiple data lines."""
    data = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") + [""]:
        if line == "" and data:
            joined = "\n".join(data)
            data = []
            if joined != "[DONE]":
                yield json.loads(joined)
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)


def stream_metrics(text: str) -> dict:
    """Compare streamed text and tool arguments with the final Responses output."""
    event_count = 0
    contiguous = True
    numbered = 0
    last_number = None
    fragments = defaultdict(list)
    final = {}
    terminal = None
    matches = None
    try:
        for event in events(text):
            kind = event["type"]
            event_count += 1
            number = event.get("sequence_number")
            if isinstance(number, int):
                numbered += 1
                if last_number is not None and number != last_number + 1:
                    contiguous = False
                last_number = number
            if kind in ("response.output_text.delta", "response.function_call_arguments.delta",
                        "response.custom_tool_call_input.delta"):
                fragments[(event["output_index"], event.get("content_index"), kind)].append(event["delta"])
            if kind in ("response.completed", "response.incomplete", "response.failed", "error"):
                terminal = kind
                final = event.get("response") or {}
        if terminal == "response.completed":
            expected = {}
            for index, item in enumerate(final["output"]):
                if item["type"] == "message":
                    for part, content in enumerate(item["content"]):
                        if content["type"] == "output_text":
                            expected[(index, part, "response.output_text.delta")] = content["text"]
                elif item["type"] == "custom_tool_call":
                    expected[(index, None, "response.custom_tool_call_input.delta")] = item["input"]
                elif item["type"] == "function_call":
                    expected[(index, None, "response.function_call_arguments.delta")] = item["arguments"]
            if expected or fragments:
                matches = (all("".join(fragments.get(k, [])) == value for k, value in expected.items())
                           and set(fragments).issubset(expected))
    except (ValueError, KeyError, IndexError, AttributeError, TypeError):
        matches = False
        if not isinstance(final, dict) or not isinstance(final.get("output", []), list):
            final = {}
    types = {"message", "reasoning", "function_call", "custom_tool_call"}
    return {"events": event_count, "terminal_event": terminal,
            "sequence_contiguous": (numbered == event_count and contiguous) if numbered else None,
            "deltas_match_terminal": matches,
            "output_types": [i["type"] if i.get("type") in types else "other"
                             for i in final.get("output", []) if isinstance(i, dict)]}


def call_metrics(call: dict) -> dict:
    """Export fixed fields, body digests and stream checks for one model call."""
    host = urlsplit(call.get("backend", "")).hostname
    backend = "configured"
    if host in {"127.0.0.1", "localhost", "::1"}:
        backend = "loopback"
    elif host in {"api.openai.com", "chatgpt.com"}:
        backend = "openai"
    return {"sequence": call["sequence"], "status": call["status"], "backend": backend, "wire_api": call.get("wire_api"),
            "complete": call["complete"], "elapsed_ms": call["elapsed_ms"],
            "request_sha256": body_hash(call["request"]), "response_sha256": body_hash(call["response"]),
            **stream_metrics(call["response"].get("text", ""))}


def grades(calls: list, before: dict, after: dict, outcome, source_changed, tests_unchanged) -> tuple[bool, bool]:
    recorded = bool(calls) and all(c["complete"] and c["sequence"] == i
                                   for i, c in enumerate(calls, start=1))
    solved = (outcome == 0 and before["ran"] == 7 and before["passed"] == 4 and before["exit_code"] == 1
              and after["ran"] == after["passed"] == 7 and after["exit_code"] == 0
              and source_changed is True and tests_unchanged)
    return recorded, solved


def exercise_evidence(model, calls, original, suite, prompt, before, after, outcome, changed, tests_unchanged):
    """Build a body-free summary of one Codex checkout session."""
    recorded, solved = grades(calls, before, after, outcome, changed, tests_unchanged)
    return {"experiment": "codex_checkout", "model": model, "recording_ok": recorded, "task_ok": solved,
            "tests_unchanged": tests_unchanged, "source_changed": changed,
            "http_error_count": sum(c["status"] is not None and c["status"] >= 400 for c in calls),
            "fixture_sha256": sha256(original).hexdigest(), "suite_sha256": sha256(suite).hexdigest(),
            "prompt_sha256": sha256(prompt.encode()).hexdigest(),
            "baseline": {k: before[k] for k in ("ran", "passed")},
            "after": {k: after[k] for k in ("ran", "passed")}, "calls": [call_metrics(c) for c in calls]}


def inspect_saved_run():
    """Recheck a saved Codex session and its final source with no model connection."""
    import argparse
    from pathlib import Path
    from verification import regular_bytes, verify

    parser = argparse.ArgumentParser(description=inspect_saved_run.__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    example = Path(__file__).resolve().parent
    result = json.loads((args.run / "result.json").read_text())
    name = result["log"]
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Expected a session JSONL filename")
    with (args.run / name).open() as stream:
        calls = [json.loads(line) for line in stream]
    if len(calls) != result["calls"] or not calls:
        raise ValueError("Saved model call count does not match the session result")
    prompt = (example / "prompt.txt").read_text()
    source = (example / "checkout/checkout.py").read_bytes()
    suite = (example / "checkout/test_checkout.py").read_bytes()
    candidate = regular_bytes(args.run / "checkout.after.py")
    for call in calls:
        request = json.loads(call["request"]["text"])
        if request.get("model") != result["model"]:
            raise ValueError("Requested model differs from the reported model")
    first = json.loads(calls[0]["request"]["text"])
    if not any(m.get("role") == "user" and any(part.get("text") == prompt for part in m.get("content", []))
               for m in first.get("input", [])):
        raise ValueError("Fixture prompt absent from the first request")
    before, after = verify(source, suite), verify(candidate, suite)
    public = exercise_evidence(result["model"], calls, source, suite, prompt, before, after, result["codex_exit"],
                               None if candidate is None else candidate != source, result["tests_unchanged"])
    public["verification"] = "archived_session_offline_regrade"
    args.output.write_text(json.dumps(public, indent=2) + "\n")
    print(f'{len(calls)} saved calls, {sum(c["events"] for c in public["calls"])} events, '
          f'tests={after["passed"]}/{after["ran"]}')


if __name__ == "__main__":
    inspect_saved_run()
