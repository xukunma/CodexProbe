# CodexProbe

CodexProbe records the full model requests and responses in a Codex CLI session. It forwards HTTP response bytes as they arrive and saves each model call in a session JSONL file. Choose a compatible OpenAI or local Ollama backend through configuration.

Start with the local Qwen tool-loop example below. It demonstrates a real tool execution, the return of its result to the model, and the recorded HTTP calls.

## Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quickstart: Local Qwen](#quickstart-local-qwen)
- [Output Files](#output-files)
- [Examples](#examples)
- [Configuration and Python API](#configuration-and-python-api)
- [Testing and Expected Results](#testing-and-expected-results)
- [Troubleshooting and Limitations](#troubleshooting-and-limitations)

## Overview

| Component | Responsibility |
| --- | --- |
| Codex CLI | Builds model requests, executes tools, and sends tool results back to the model. |
| CodexProbe | Forwards HTTP traffic and records complete request and response bodies. |
| Model backend | Runs inference and returns text or structured tool calls. Examples use OpenAI or Ollama with Qwen. |

Codex connects to the local endpoint returned by `ProxyRecorder.start()`. The recorder forwards requests to the backend address in its configuration. Backend selection requires no changes to the recorder implementation or Codex's source code.

The recorder preserves request bodies and response bytes, including SSE streams. It handles connection-specific headers and authentication separately. See the [protocol contract](protocol_contract.md) for details.

## Prerequisites

Install these tools before running the examples:

| Tool | Requirement | Verified version |
| --- | --- | --- |
| [Python](https://www.python.org/downloads/) | Python 3.14 or later, with `venv` and `pip` | 3.14.6 |
| Git | Available on `PATH` for cloning the repository | Used for the GitHub installation check |
| [Codex CLI](https://developers.openai.com/codex/cli) | Available as `codex`, with the permissions features used by the example runners | 0.169.0-alpha.11.1 |
| [Ollama](https://docs.ollama.com/quickstart) | Available as `ollama` and serving the local backend | 0.40.16 |

The measured local environment was macOS on an Apple M5 with 16 GB of memory. The local Qwen3 model occupied about 5.2 GB on disk. This is the tested setup, rather than a measured minimum hardware requirement.

Python 3.14 supplies `compression.zstd`, which the recorder uses to decode compressed request bodies for inspection. Use a Python 3.14+ executable when creating the virtual environment.

The commands below use a macOS/Linux-style shell. The examples and full suite were verified on macOS. The runners use version-specific Codex flags and named permissions profiles. Other Codex builds and operating systems may require changes to the runners.

Installing the Python package installs its Python dependencies. Codex CLI, Ollama, and model weights are separate prerequisites. The local example uses no OpenAI credentials. The cloud example uses Codex's own ChatGPT login.

## Installation

Clone into a new directory:

```sh
git clone https://github.com/xukunma/CodexProbe.git
cd CodexProbe
```

Check Python, create a virtual environment, and install the package with its test dependencies:

```sh
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

If `python3` is older than 3.14, use your installed Python 3.14+ executable to create the environment before continuing. The `-e` option installs the project in editable mode. The `[test]` extra installs pytest.

Confirm the package can be imported:

```sh
python -c "from codex_probe import ProxyRecorder; print('INSTALL_OK')"
```

Expected output:

```text
INSTALL_OK
```

Run the package tests:

```sh
python -m pytest -q
```

The verified suite contains 25 tests. A successful run ends with `25 passed`. Tests need permission to bind local loopback ports, and the isolated verifier uses Codex's operating-system sandbox.

Run all remaining commands from this repository's root with the virtual environment active. In a new terminal, enter the project directory and activate it again with `source .venv/bin/activate`.

## Quickstart: Local Qwen

### 1. Check the local tools and server

```sh
codex --version
ollama --version
ollama list
```

If Ollama is not running, open the Ollama app or run the following in a second terminal and leave it running:

```sh
ollama serve
```

Return to the project terminal for the remaining steps.

### 2. Prepare the model

For first-time setup, download Qwen3 and create the alias used by the configuration:

```sh
ollama pull qwen3:8b
ollama create qwen3-codex-16k:8b -f examples/Modelfile.qwen3_16k
ollama list
```

The supplied Modelfile selects the Qwen3 tool parser and a 16,384-token context window. Confirm that `qwen3-codex-16k:8b` appears in the model list. If the alias is already prepared, continue to the example.

### 3. Run the recorded tool loop

```sh
python examples/reproduce_tool_loop.py examples/qwen3_16k.json \
  --mode proxy \
  --output runs/first-proxy-demo
```

The model is asked to execute one shell command that prints `TOOL_OK`, read the returned tool output, and answer exactly `TOOL_OK`. The runner starts CodexProbe, connects Codex to it, and stops the recorder when the run finishes.

Every invocation requires a new output directory. For a second run, use a name such as `runs/second-proxy-demo`.

### 4. Check the result

A successful run from a fresh GitHub checkout on 2026-11-16 produced:

```json
{
  "mode": "proxy",
  "model": "qwen3-codex-16k:8b",
  "elapsed_seconds": 48.675,
  "codex_exit": 0,
  "tool_ok": true,
  "final_answer_ok": true,
  "turn_completed": true,
  "http_calls": 2,
  "recording_ok": true,
  "success": true
}
```

`tool_ok` checks successful command execution. `final_answer_ok` checks the final answer. `turn_completed` checks that Codex completed its turn. In proxy mode, `recording_ok` requires at least two recorded calls, complete HTTP 200 responses, and tool-call and tool-output markers in the recorded bodies.

The example succeeds when these checks pass and Codex exits with code 0. `http_calls` counts model HTTP requests, rather than executed shell commands. This run used two requests. Model behavior, request counts, and timings can vary between runs.

## Output Files

The proxy quickstart writes to `runs/first-proxy-demo/`:

| File | Contents |
| --- | --- |
| `result.json` | The result summary printed by the runner. |
| `<session-id>.jsonl` | Complete recorded model calls, one JSON object per line. The filename is generated for each session. |
| `events.ndjson` | Codex CLI events, including command execution and the final answer. |
| `stderr.txt` | Codex diagnostic output. |

The checkout example additionally writes `evidence.json` and, when available, `checkout.after.py`. Its `result.json` includes a `log` filename. The tool-loop summary has no `log` field, so find its recording by the `*.jsonl` extension.

### Inspect the tool feedback

This command reads the saved quickstart recording without calling the model again:

```sh
python - <<'PY'
import json
from pathlib import Path

folder = Path("runs/first-proxy-demo")
log = next(folder.glob("*.jsonl"))
print("Log file:", log)

for line in log.read_text().splitlines():
    call = json.loads(line)
    request = json.loads(call["request"]["text"])
    feedback = [
        item for item in request.get("input", [])
        if isinstance(item, dict)
        and item.get("type") == "function_call_output"
    ]
    print(
        f"\nCall {call['sequence']}: "
        f"HTTP {call['status']}, complete={call['complete']}"
    )
    print("Tool results in request:", len(feedback))
    for item in feedback:
        print("Tool output:", item.get("output"))
PY
```

In the verified run, call 1 had no tool results. Call 2 contained one `function_call_output` with command exit code 0 and output `TOOL_OK`. Both calls had HTTP status 200 and `complete=True`. This confirms that the returned tool result entered the next model request and was captured by the recorder.

### Read the record fields

Each recorded POST includes `sequence`, `timestamp`, `method`, `path`, `backend`, `status`, `elapsed_ms`, `complete`, `request`, and `response`. Error and downstream-disconnection fields are added when applicable. Discovery GETs are forwarded and excluded from the model-call count.

The body object's `text` field exposes readable UTF-8 content. Compressed or binary bodies also use `base64` to preserve their original bytes, with decoded `text` when supported. For SSE responses, `response.text` contains the event stream, including tool arguments, text deltas, and terminal events.

`complete: true` means the upstream HTTP response body was fully received. Task correctness and successful delivery to the downstream client are separate properties.

## Examples

### Direct tool-loop comparison

Run the same minimal task directly against Ollama:

```sh
python examples/reproduce_tool_loop.py examples/qwen3_16k.json \
  --mode direct \
  --output runs/tool-loop-direct
```

Direct mode captures Codex events and grades tool execution and the final answer. The recorder is absent, so `http_calls` is 0 and `recording_ok` is `null`. Both modes were observed to complete the minimal task. A single timing comparison does not establish proxy overhead.

### Checkout repair with OpenAI

This example starts from a small checkout module with a discount bug. Four of its seven fixture tests pass initially. A successful repair applies the discount to merchandise before adding shipping, preserves the original tests, and passes all seven tests.

Authenticate with `codex login` if needed, then check the login and run:

```sh
codex login status
python examples/quickstart.py examples/openai.json \
  --output runs/checkout-openai
```

The supplied configuration uses `https://chatgpt.com/backend-api/codex` and the model `gpt-6.1-sol` through Codex's ChatGPT login. Choose a model available to your account by changing the configuration's `model` field. This example uses the same recorder implementation as the local backend.

### Checkout repair with the original local Qwen configuration

The original local checkout comparison uses Qwen2.5-Coder:

```sh
ollama pull qwen2.5-coder:7b
python examples/quickstart.py examples/ollama.json \
  --output runs/checkout-ollama
```

In the recorded Qwen2.5 run, the model described a command in an ordinary message and Codex executed no tool. The recording succeeded while the coding task failed. The successful Qwen3 quickstart establishes the smaller tool interaction, and does not establish a successful checkout repair.

For checkout runs, inspect `result.json`:

| Field | Meaning |
| --- | --- |
| `recording_ok` | Calls were persisted with complete transfers in arrival order. |
| `task_ok` | Codex exited successfully, source changed, tests stayed unchanged, and an independent check improved the fixture from 4/7 to 7/7 passing tests. |
| `http_error_count` | Recorded HTTP responses with an error status, including retries. |

The checkout runner exits with 0 when recording and task checks pass, 1 when recording succeeds but the task fails, and 2 when the baseline or recording check fails.

The runner gives Codex write access to a fresh temporary exercise directory and disables tool networking. The independent verifier runs the candidate with the original tests in another fresh, read-only, offline sandbox.

### Inspect a saved checkout run

Regrade a checkout run and inspect its saved stream evidence without contacting a model:

```sh
python examples/evidence.py runs/checkout-openai \
  --output runs/checkout-openai/regraded-evidence.json
```

This command expects the checkout runner's files and summary schema. For a tool-loop recording, use the inspection command in [Output Files](#output-files).

### Advanced controls and historical experiments

`examples/qwen_direct.py` sends the source and tests directly to Ollama through the recorder, using Chat Completions. It can request plain Python or JSON output and provide one round of test feedback. This control bypasses Codex's file and shell tools.

Commands, outcomes, dependent-tool evidence, the Flask repair comparison, and performance measurements are in [Experiments and Recorded Results](docs/experiments.md).

## Configuration and Python API

### Choose a backend

| Configuration | Upstream service | Model |
| --- | --- | --- |
| `examples/qwen3_16k.json` | Ollama at `http://127.0.0.1:11434/v1` | `qwen3-codex-16k:8b` |
| `examples/ollama.json` | Ollama at `http://127.0.0.1:11434/v1` | `qwen2.5-coder:7b` |
| `examples/openai.json` | OpenAI through Codex's ChatGPT login | `gpt-6.1-sol` |

There are two addresses to distinguish. The recorder configuration's `base_url` is the real upstream service. The endpoint returned by `start()` is the local proxy address that Codex must use. A dynamic proxy port is selected when `listen_port` is 0. The runners connect these addresses automatically.

| Field | Purpose |
| --- | --- |
| `base_url` | Required upstream HTTP(S) API root. |
| `log_dir` | Required session-log directory. Runners set it to their `--output` directory. |
| `listen_host` / `listen_port` | Local IPv4 loopback listener. Defaults are `127.0.0.1` and an available port. |
| `model` | Model selected by the runner. The recorder forwards the client's model request unchanged. |
| `context_window` | Optional Codex context-window setting used by the runners. |
| `wire_api` | Protocol label, also supplied to Codex by the runners. Bundled Codex configurations use `responses`. |
| `forward_auth` | Forward incoming authentication when true. Defaults to false, removing authorization, cookies, and the account header before forwarding. |
| `env_key` | Optional name of an environment variable whose value becomes the upstream bearer token. |
| `timeout` | Upstream idle-read timeout in seconds. Default: 180. |
| `drain_timeout` | Optional shutdown deadline in seconds for active requests. |

The recorder preserves the client's sampling settings. The `seed` entry is used by the direct-file control and does not force a seed on Codex-generated requests.

### Use the recorder from Python

This opens port 8135 and keeps it available for a manually configured Codex session:

```python
import json
from pathlib import Path
from codex_probe import ProxyRecorder

config = json.loads(Path("examples/qwen3_16k.json").read_text())
config["listen_port"] = 8135
config["log_dir"] = "logs/manual"

recorder = ProxyRecorder(config)
endpoint = recorder.start()
try:
    print("Codex provider base URL:", endpoint, flush=True)
    input("Run Codex against this endpoint, then press Enter to stop. ")
finally:
    calls = recorder.stop()

print(len(calls), "model calls saved to", recorder.log_path)
```

`start()` returns the listening endpoint after startup. `stop()` drains active requests, closes the session, and returns full recorded calls in arrival order. The recording is saved at `recorder.log_path`.

With the Python process still running, use this provider fragment for a manual Codex session:

```toml
model_provider = "recorder"
model = "qwen3-codex-16k:8b"
model_context_window = 16384

[model_providers.recorder]
name = "CodexProbe"
base_url = "http://127.0.0.1:8135/v1"
wire_api = "responses"
supports_websockets = false
requires_openai_auth = false
```

The runners already supply provider settings per invocation. The fragment is for manual use. For a cloud session, select the OpenAI recorder configuration and a matching model, and enable `requires_openai_auth` in Codex. The recorder's `forward_auth` setting allows the session authorization to reach that upstream.

API-key backends can use `env_key` with a compatible API root. API-key mode was not part of the measured end-to-end runs.

### Match the API format

Responses uses input/output items and typed stream events. Chat Completions uses messages and choice deltas. The Codex examples use `/v1/responses`. The direct-file control uses `/v1/chat/completions`.

The recorder forwards the chosen route and bytes. It does not translate between formats or convert ordinary model text into structured tool calls. Compatibility includes request schemas, streaming behavior, and tool-call representations.

## Testing and Expected Results

Run the suite from the repository root:

```sh
python -m pytest -q
```

The 25 tests cover live HTTP/SSE forwarding, fragmented streams, compressed bodies, authentication, structured tool calls, concurrent requests, incomplete upstream responses, client disconnects, ordered persistence, shutdown, and independent fixture grading.

On 2026-11-16, installation from a fresh GitHub clone into a new virtual environment produced:

```text
25 passed in 9.97s
```

The same installed project completed the Qwen3 proxy quickstart in 48.675 seconds, with two complete HTTP 200 responses and a tool result in the second request. Codex CLI, Ollama, and the model alias were already installed on that machine. Timings describe that run and are not performance guarantees.

The checkout fixture has seven tests and intentionally starts with three failures. Its initial 4/7 result is expected. Those fixture failures are part of the model's repair task and are separate from the package's 25 passing tests.

Earlier results, their verification scope, and performance measurements are linked in [Experiments and Recorded Results](docs/experiments.md) and [the benchmark report](phase6_results.md).

## Troubleshooting and Limitations

| Symptom | What to check |
| --- | --- |
| Python version or `compression.zstd` import error | Create the virtual environment with Python 3.14 or later. |
| `codex` or `ollama` unavailable | Install the prerequisite and confirm its executable is on `PATH`. |
| Ollama connection failure | Start the app or `ollama serve`, then check `ollama list` and the configured URL. |
| Model not found | Pull Qwen3 and create the alias with the supplied Modelfile. Match the name in the JSON configuration. |
| `FileExistsError` for the output directory | Select a new `--output` directory for every run. |
| Unknown Codex flag or permissions option | Check the verified CLI version above. The runner and verifier depend on its permissions configuration. |
| Loopback binding or OS sandbox denied | Run in a local terminal that permits a loopback server and Codex's OS sandbox. |
| `TimeoutExpired` or incomplete turn | Inspect `stderr.txt` and `events.ndjson`. The runner defaults to 150 seconds. Retry with a new output directory and, if needed, `--timeout 300`. |
| `recording_ok: true` but `task_ok: false` | Inspect commands and tool feedback. Transport completion and coding correctness are graded independently. |

Additional limits:

- The Qwen3 quickstart verifies a minimal tool loop. More complex coding tasks can fail even when every HTTP call is fully recorded.
- Model output, tool choices, and request counts can vary. A matching API URL alone does not establish backend compatibility.
- Entire request and response bodies are accumulated for recording, and `stop()` loads the saved session. Large sessions can require substantial memory.
- Full logs contain prompts, conversation history, tool definitions, tool output, and any host metadata Codex includes in the body. HTTP headers are omitted. Keep raw runs private and use body-free summaries or clearly labeled teaching copies when sharing.
- Linux, Windows, other CLI builds, and API-key configurations have not been established by the measured macOS end-to-end runs.

Further reading: [protocol contract](protocol_contract.md), [experiment evidence](docs/experiments.md), [benchmark results](phase6_results.md), [teaching artifacts](walkthrough/manifest.json), and the [Task 7 specification](https://github.com/STAR-Computing/ProgrammingTask/blob/master/07_codex_llm_call_recorder.md).
