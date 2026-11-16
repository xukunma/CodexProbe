"""Run a recorded checkout exercise and independently verify its outcome."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from codex_probe import ProxyRecorder
from evidence import exercise_evidence
from verification import overrides, profile, regular_bytes, verify

EXAMPLES = Path(__file__).resolve().parent


def run_codex(project, endpoint, config, output, prompt, timeout):
    env = os.environ.copy()
    for key in ("CODEX_APP_TOOLS_PIPE_PATH", "CODEX_APP_RUNTIME_CLI_PATH", "CODEX_THREAD_ID",
                "CODEX_SESSION_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
        env.pop(key, None)
    settings = {
        "model_provider": "recorder", "model_providers.recorder.name": "CodexProbe",
        "model_providers.recorder.base_url": endpoint,
        "model_providers.recorder.wire_api": config["wire_api"],
        "model_providers.recorder.requires_openai_auth": config.get("forward_auth", False),
        "model_providers.recorder.supports_websockets": False,
        "features.apps": False, "features.plugins": False, "features.skip_host_skill_discovery": True,
        "web_search": "disabled", "project_doc_max_bytes": 0,
        "default_permissions": "checkout", **profile(project, "write"),
    }
    if "context_window" in config:
        settings["model_context_window"] = config["context_window"]
    cmd = ["codex", "--no-daemon", "-a", "never", "exec", "--ignore-user-config",
           "--skip-git-repo-check", "--ephemeral", "-C", str(project), "-m", config["model"],
           "--json", *overrides(settings), prompt]
    with (output / "events.ndjson").open("w") as events, (output / "stderr.txt").open("w") as errors:
        try:
            return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=events, stderr=errors,
                                  cwd=project, env=env, timeout=timeout).returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            return type(exc).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New directory for this run")
    parser.add_argument("--timeout", type=int, default=150)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    config["log_dir"] = str(output)
    recorder = ProxyRecorder(config)
    prompt = (EXAMPLES / "prompt.txt").read_text()
    original = (EXAMPLES / "checkout" / "checkout.py").read_bytes()
    suite = (EXAMPLES / "checkout" / "test_checkout.py").read_bytes()
    before = verify(original, suite)
    if (before["ran"], before["passed"], before["exit_code"]) != (7, 4, 1):
        (output / "result.json").write_text(json.dumps({"recording_ok": False, "task_ok": False,
                                                        "baseline": before, "error": "baseline"}, indent=2))
        print("Baseline unavailable; inspect result.json.")
        return 2
    with TemporaryDirectory(prefix="codexprobe-") as name:
        project = Path(name).resolve()
        (project / "checkout.py").write_bytes(original)
        (project / "test_checkout.py").write_bytes(suite)
        try:
            endpoint = recorder.start()
            outcome = run_codex(project, endpoint, config, output, prompt, args.timeout)
        except (OSError, ValueError) as exc:
            outcome = type(exc).__name__
        finally:
            recording_error = None
            try:
                calls = recorder.stop()
            except (OSError, ValueError) as exc:
                calls = []
                recording_error = str(exc)
        source = regular_bytes(project / "checkout.py")
        tests_unchanged = regular_bytes(project / "test_checkout.py") == suite
    after = verify(source, suite)
    changed = None if source is None else source != original
    public = exercise_evidence(config["model"], calls, original, suite, prompt, before,
                               after, outcome, changed, tests_unchanged)
    recorded, solved = public["recording_ok"], public["task_ok"]
    result = {"model": config["model"], "backend": config["base_url"], "codex_exit": outcome,
              "calls": len(calls), "log": recorder.log_path.name if recorder.log_path else None,
              "baseline": before, "after": after, "tests_unchanged": tests_unchanged, "source_changed": changed,
              "recording_ok": recorded, "task_ok": solved, "http_error_count": public["http_error_count"],
              "recording_error": recording_error}
    if source is not None:
        (output / "checkout.after.py").write_bytes(source)
    (output / "result.json").write_text(json.dumps(result, indent=2))
    (output / "evidence.json").write_text(json.dumps(public, indent=2) + "\n")
    print(f'{config["model"]}: {len(calls)} calls, recording_ok={recorded}, task_ok={solved}, '
          f'tests={after["passed"]}/{after["ran"]}')
    if not recorded:
        return 2
    return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
