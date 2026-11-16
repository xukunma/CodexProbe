
"""Reproduce a real Codex tool-calling loop."""

import argparse
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_probe import ProxyRecorder
from quickstart import run_codex


PROMPT = (
    "Use exec_command exactly once to run "
    "`printf 'TOOL_OK\\n'`. "
    "Read the command output and then reply TOOL_OK. "
    "/no_think"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--mode", choices=["direct", "proxy"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=150)
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    config["log_dir"] = str(output)

    recorder = None
    calls = []

    try:
        if args.mode == "proxy":
            recorder = ProxyRecorder(config)
            endpoint = recorder.start()
        else:
            endpoint = config["base_url"]

        with TemporaryDirectory(prefix="codexprobe-demo-") as folder:
            start = time.perf_counter()

            exit_code = run_codex(
                Path(folder),
                endpoint,
                config,
                output,
                PROMPT,
                args.timeout,
            )

            elapsed = time.perf_counter() - start

    finally:
        if recorder is not None:
            calls = recorder.stop()

    events = [
        json.loads(line)
        for line in (output / "events.ndjson").read_text().splitlines()
        if line.strip()
    ]

    items = [
        event["item"]
        for event in events
        if isinstance(event.get("item"), dict)
    ]

    tool_ok = any(
        item.get("type") == "command_execution"
        and item.get("status") == "completed"
        and item.get("exit_code") == 0
        and "TOOL_OK" in item.get("aggregated_output", "")
        for item in items
    )

    final_answer_ok = any(
        item.get("type") == "agent_message"
        and item.get("text", "").strip() == "TOOL_OK"
        for item in items
    )

    turn_completed = any(
        event.get("type") == "turn.completed"
        for event in events
    )

    recording_ok = (
        len(calls) >= 2
        and all(
            call.get("status") == 200
            and call.get("complete") is True
            for call in calls
        )
        and any(
            "function_call" in call.get("response", {}).get("text", "")
            for call in calls
        )
        and any(
            "function_call_output" in call.get("request", {}).get("text", "")
            for call in calls
        )
    ) if args.mode == "proxy" else None

    success = (
        exit_code == 0
        and tool_ok
        and final_answer_ok
        and turn_completed
        and (args.mode == "direct" or recording_ok is True)
    )

    result = {
        "mode": args.mode,
        "model": config["model"],
        "elapsed_seconds": round(elapsed, 3),
        "codex_exit": exit_code,
        "tool_ok": tool_ok,
        "final_answer_ok": final_answer_ok,
        "turn_completed": turn_completed,
        "http_calls": len(calls),
        "recording_ok": recording_ok,
        "success": success,
    }

    (output / "result.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )

    print(json.dumps(result, indent=2))
    print("Evidence directory:", output)

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
