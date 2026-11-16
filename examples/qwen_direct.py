"""Control experiment: give local Qwen the fixture directly and grade its returned module."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.request import build_opener, ProxyHandler, Request

from codex_probe import ProxyRecorder
from evidence import call_metrics
from verification import verify

EXAMPLES = Path(__file__).resolve().parent


def candidate_module(answer, output_format):
    """Extract one returned file without repairing or editing its contents."""
    try:
        text = (json.loads(answer)["source"] if output_format == "json" else answer).strip()
        fence = re.fullmatch(r"```(?:python|py)?\s*\n(.*?)\n```", text, re.DOTALL)
        return ((fence[1] if fence else text) + "\n").encode()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=("plain", "json"), default="plain")
    parser.add_argument("--feedback", action="store_true", help="Allow one repair using the actual failing test output")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    config = json.loads((EXAMPLES / "ollama.json").read_text())
    source = (EXAMPLES / "checkout/checkout.py").read_bytes()
    suite = (EXAMPLES / "checkout/test_checkout.py").read_bytes()
    prompt = ("Repair the bug in checkout.py so that the given tests pass. Preserve the public signature. "
              "Return only the complete revised Python module.\n\ncheckout.py\n" + source.decode()
              + "\n\ntest_checkout.py\n" + suite.decode())
    payload = {"model": config["model"], "temperature": 0, "seed": config["seed"], "stream": False,
               "messages": [{"role": "user", "content": prompt}]}
    if args.format == "json":
        prompt += ("\n\nReturn a JSON object with one field, source. Its value must contain only checkout.py, "
                   "ready to import. The test module is supplied as input and must stay in its own file.")
        payload["messages"][0]["content"] = prompt
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "repaired_module", "strict": True, "schema": {"type": "object",
                "properties": {"source": {"type": "string"}}, "required": ["source"],
                "additionalProperties": False}}}
    baseline = verify(source, suite)
    recorder = ProxyRecorder({**config, "wire_api": "chat_completions", "log_dir": out})
    limit = 2 if args.feedback else 1
    requests_sent = 0
    attempts = []
    candidate = None
    after = {"exit_code": None, "ran": 0, "passed": 0, "output": "No candidate"}
    error = None
    try:
        endpoint = recorder.start()
        for _ in range(limit):
            req = Request(endpoint + "/chat/completions", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
            requests_sent += 1
            with build_opener(ProxyHandler({})).open(req, timeout=90) as response:
                answer = json.load(response)["choices"][0]["message"]["content"]
            candidate = candidate_module(answer, args.format)
            after = verify(candidate, suite)
            attempts.append({k: after[k] for k in ("ran", "passed", "exit_code")})
            if candidate is not None:
                (out / f"checkout.attempt-{len(attempts)}.py").write_bytes(candidate)
            (out / f"tests.attempt-{len(attempts)}.txt").write_text(after["output"])
            if after["exit_code"] == 0 or len(attempts) == limit:
                break
            payload["messages"].extend([
                {"role": "assistant", "content": answer},
                {"role": "user", "content": "The independent verifier returned the following output. "
                 "Use it to repair checkout.py and return only that file in the same response format.\n"
                 + after["output"]}])
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        error = type(exc).__name__
    finally:
        try:
            calls = recorder.stop()
        except (OSError, ValueError) as exc:
            calls = []
            error = type(exc).__name__
    if candidate is not None:
        (out / "checkout.after.py").write_bytes(candidate)
    recorded = bool(calls) and len(calls) == requests_sent and all(c["complete"] for c in calls)
    solved = (baseline["exit_code"] == 1 and baseline["ran"] == 7 and baseline["passed"] == 4
              and after["exit_code"] == 0 and after["ran"] == after["passed"] == 7 and source != candidate)
    metrics = {"experiment": "direct_chat_with_source_and_tests", "format": args.format, "model": config["model"],
               "feedback_enabled": args.feedback, "attempts": attempts,
               "temperature": 0, "seed": config["seed"], "recording_ok": recorded, "task_ok": solved,
               "prompt_sha256": sha256(prompt.encode()).hexdigest(),
               "fixture_sha256": sha256(source).hexdigest(), "suite_sha256": sha256(suite).hexdigest(),
               "candidate_sha256": sha256(candidate).hexdigest() if candidate is not None else None,
               "baseline": {k: baseline[k] for k in ("ran", "passed")},
               "after": {k: after[k] for k in ("ran", "passed")},
               "calls": [call_metrics(c) for c in calls]}
    (out / "evidence.json").write_text(json.dumps(metrics, indent=2) + "\n")
    (out / "tests.txt").write_text(after["output"] + (f"\nRun error: {error}\n" if error else ""))
    print(f'Direct Qwen: calls={len(calls)}, tests={after["passed"]}/{after["ran"]}, '
          f'recording_ok={recorded}, task_ok={solved}')
    return 2 if not recorded else int(not solved)


if __name__ == "__main__":
    raise SystemExit(main())
