# CodexProbe

CodexProbe records every HTTP model call in one Codex CLI session. It forwards response bytes as they arrive, then writes the complete request and reassembled response stream to a local JSONL file. The checkout example uses two fresh copies of the same Python project to compare OpenAI with a self-hosted Qwen coder model.

## 1. Install and test

Use Python 3.14 or later. Python 3.14 supplies the Zstandard decoder for compressed Codex requests. From this directory, run:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
```

The recorder tests use an HTTP server on loopback. The isolated verifier integration also uses a Codex CLI with named permissions profiles when installed. The seven tests in the checkout example intentionally start with three failures. A discount incorrectly applies to shipping.

## 2. Run the same exercise twice

The example was verified with Codex CLI 0.167.0-alpha.3.2 and Ollama 0.40.16 on an Apple M5 with 16 GB of memory. The Qwen weights occupy about 4.7 GB. The OpenAI example uses an existing ChatGPT login managed by Codex. The sample model names reproduce the measured setup. Choose an available OpenAI model in the config if your account differs.

```sh
codex login status
ollama pull qwen2.5-coder:7b
# Start ollama serve in another terminal if Ollama is not already running.
python examples/quickstart.py examples/openai.json --output runs/openai
python examples/quickstart.py examples/ollama.json --output runs/ollama
```

Choose a fresh output directory. For a shareable cloud demonstration, use a Codex installation without personal instructions or skills. The CLI can include host metadata in the request before executing any project command. The full JSONL, Codex events and stderr belong to the private run directory. The bundled evidence contains test counts, HTTP status, stream checks and SHA-256 digests.

The example grants Codex tools write access to the temporary exercise directory, reads for the base Python runtime and platform minimum, and disables tool networking. The CLI itself reaches the recorder on loopback. The verifier copies the candidate module and the original suite into another fresh directory. It runs them under a read-only, offline Codex permissions profile with a clean environment. A missing candidate produces a failed task result. The demo requires a CLI and operating system that can enforce the profile. The tested combination is listed above.

Inspect `result.json` for private test output and `evidence.json` for shareable metrics. `recording_ok` checks complete transfers in arrival order. `task_ok` requires a successful CLI exit, a changed source, unchanged tests and an independently verified improvement from 4/7 to 7/7. HTTP errors are counted separately so a recovered retry stays visible. Exit 0 means both checks passed, 1 means the recording succeeded but the task failed, and 2 means the baseline or recording failed.

## 3. Use the recorder from Python

```python
import json
from pathlib import Path
from codex_probe import ProxyRecorder

config = json.loads(Path("examples/ollama.json").read_text())
recorder = ProxyRecorder(config)
endpoint = recorder.start()
try:
    print("Codex provider base URL:", endpoint, flush=True)
    input("Run Codex against this endpoint, then press Enter. ")
finally:
    calls = recorder.stop()
print(len(calls), "model calls saved to", recorder.log_path)
```

For manual use, supply the returned port in the Codex provider settings. The quickstart supplies these overrides for one invocation. Example fragment for local Qwen:

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

Set `listen_port` to 8135 to use that fragment verbatim. With the OpenAI ChatGPT login, select the OpenAI example config and model, and set `requires_openai_auth = true` in Codex. The recorder forwards the session authorization to the configured upstream when `forward_auth` is true. The local config drops authorization, cookies and the account header. For an API-key backend, configure its HTTPS base URL and set `env_key` to the name of an environment variable containing the key. API-key mode was not used in the measured runs. Logs contain message and tool bodies and stay in the run directory. The recorder omits HTTP headers from the log.

Both measured Codex runs used HTTP streaming on `/v1/responses`. The local server also returned a real completion to a direct `/v1/chat/completions` request. Responses uses input/output items and typed stream events. Chat Completions uses messages and choice deltas. The recorder forwards the selected route and bytes without translating protocols. Configure the client and backend to speak the same format. Backend, port, log location and auth come from the config. The proxy preserves the client's sampling settings. The direct Qwen control explicitly sends the seed and temperature from its example.

## 4. Inspect a call

Each POST has an arrival sequence, UTC timestamp, method and path, upstream URL, upstream status, elapsed milliseconds, transfer completion flag, request and response. Discovery GETs are forwarded and excluded from the model-call count. The body’s `text` field contains the entire UTF-8 entity. For compressed or binary data, `base64` preserves the original entity bytes. The recorder also decodes supported compression for inspection. For SSE, `response.text` preserves the event stream, including tool arguments, text deltas and the final event. HTTP transfer framing is managed by the HTTP libraries.

The following snippet prints the requested model and the output item types in each completed Responses call. It reads the same JSONL returned by the quickstart.

```python
import json
from pathlib import Path

run = Path("runs/ollama")
summary = json.loads((run / "result.json").read_text())
for line in (run / summary["log"]).read_text().splitlines():
    call = json.loads(line)
    request = json.loads(call["request"]["text"])
    for event in call["response"]["text"].splitlines():
        if event.startswith("data: {"):
            data = json.loads(event[6:])
            if data.get("type") == "response.completed":
                print(call["sequence"], request["model"],
                      [item["type"] for item in data["response"]["output"]])
```

A record with `complete: true` means the upstream HTTP entity was fully received. The recorder keeps reading the upstream if the client disconnects before the headers or during streaming. Normal shutdown waits for active calls. Set `drain_timeout` in seconds to impose a shutdown deadline and mark unfinished calls incomplete. Upstream idle reads are bounded by `timeout` (180 seconds by default). Automatic HTTP cookie storage is disabled. Finished records are written in arrival order and released from memory. An earlier active call can delay writing later calls. The standard `stop()` API loads the full session from disk and checks the count before returning it.

To inspect an existing checkout run without contacting either model, run the following command. It checks the requested model and original prompt, reads the recorded bytes, compares streamed text and tool argument deltas with the terminal response, and regrades the saved source with the original seven tests.

```sh
python examples/evidence.py runs/openai --output openai-evidence.json
```

## 5. Matched Codex exercise

Both runs used the same user prompt, initial module and suite. Codex selected different tool representations for the two backends. The OpenAI row is an offline regrade of the earlier live session. The local Qwen row comes from a new live run of the updated recorder. Both have been checked against their private, persisted JSONL files.

| Backend and evidence | Model calls | Tests before → after | Recorded outcome |
| --- | ---: | ---: | --- |
| [OpenAI via the existing ChatGPT login](evidence/openai-archived.json) | 3 | 4/7 → 7/7 | Codex edited the module and ran the tests. The discount now applies to merchandise before shipping is added. |
| [Ollama with the original Qwen 7B](evidence/ollama-codex.json) | 1 | 4/7 → 4/7 | Qwen returned JSON describing a command inside an ordinary message. Codex executed no command, and the source stayed unchanged. |

All four model calls returned HTTP 200 and a terminal `response.completed`. The per-call digests and stream checks are in the linked files. The results describe these runs on the tested configuration.

## 6. Local Qwen control with direct file input

Can the same local weights produce a passing implementation when given the files directly? The control sends the original module and test suite through the same recorder to Ollama's Chat Completions endpoint. It uses temperature 0 and seed 42. Each feedback run allows one additional response containing a revised module after the model receives the actual test output. The runner extracts the returned source or its JSON field and strips an optional outer code fence. The model supplies all source changes. This control bypasses Codex's file and shell tools.

| Output contract | One response | Separate run with one feedback opportunity |
| --- | --- | --- |
| Plain Python module | [Import failed](evidence/ollama-direct.json). The reply appended the test module to the source. | [Import failed → 7/7 passed](evidence/ollama-feedback-plain.json). Qwen returned a standalone module on its second attempt. |
| JSON object with a source field | [4/7 passed](evidence/ollama-direct-json.json). The discount still included shipping. | [4/7 → 4/7 passed](evidence/ollama-feedback.json). The revised module retained the faulty calculation. |

All four control sessions used the same fixture and independent test suite. The JSON condition also explicitly instructs the model to return a single source field. Each cell is one measured session. Use a different output directory on every invocation.

```sh
python examples/qwen_direct.py --format plain --output runs/plain
python examples/qwen_direct.py --format plain --feedback --output runs/plain-feedback
python examples/qwen_direct.py --format json --output runs/json
python examples/qwen_direct.py --format json --feedback --output runs/json-feedback
```

[Task specification](https://github.com/STAR-Computing/ProgrammingTask/blob/master/07_codex_llm_call_recorder.md) · [Official OpenAI Codex configuration reference](https://developers.openai.com/codex/config-reference)

## 7. Verified Qwen3 Agent Tool Loop

A real Codex CLI tool-calling loop was successfully tested with
Qwen3 8B through Ollama's Responses API.

Test configuration:
- Model: qwen3-codex-16k:8b
- Ollama context window: 16,384 tokens
- Wire API: responses
- Task: execute a command printing TOOL_OK and return its output

The same task was run both directly and through CodexProbe.

| Metric | Direct | Through CodexProbe |
|---|---|---|
| Codex exit code | 0 | 0 |
| Agent turn completed | Yes | Yes |
| Shell command executed | Yes | Yes |
| Command exit code | 0 | 0 |
| Final answer | TOOL_OK | TOOL_OK |
| Recorded HTTP calls | N/A | 2 |
| Complete HTTP responses | N/A | 2/2 |

The proxy run recorded both the structured function_call and the
subsequent request containing the tool result. Both HTTP responses
returned status 200.

This demonstrates a successful minimal end-to-end Agent tool loop,
not successful completion of the more complex checkout repair task.

Earlier experiments with Qwen2.5-Coder 7B returned tool-like JSON
inside ordinary assistant messages instead of structured function
calls. That behavior was observed without CodexProbe as well.

## 8. Verification and Performance

The earlier full-suite report recorded 25 passing tests with Python
3.14.6 and pytest 9.2.0 on macOS. The 2026-11-16 closeout collected the
same 25 tests and reran five pure-logic cases successfully. The current
execution environment rejected nested macOS sandbox creation and binding
the loopback mock server during the restricted check. The user then
ran the full suite in an ordinary terminal and confirmed that it passed
on 2026-11-16. This final result is recorded as user-confirmed.
The build, editable installation, installed-package import and archived
OpenAI checkout regrade were also checked during closeout. See
[closeout verification](evidence/closeout-verification.json).

Tests cover transparent HTTP passthrough, SSE fragmentation,
concurrent streaming, authentication, response recording,
connection failures, graceful shutdown, and large SSE responses.

In a controlled local benchmark with 20 measured requests per mode:

| Metric | Direct | Proxy | Difference |
|---|---:|---:|---:|
| Median TTFB | 1.039 ms | 2.436 ms | +1.396 ms |
| Median full response | 25.450 ms | 26.148 ms | +0.697 ms |

A separate real-Agent benchmark completed four TOOL_OK runs
successfully: two Direct and two through CodexProbe.

The observed real-Agent wall-clock times are not sufficient to
establish a performance advantage for either mode, because model
inference time varies between runs.

Full evaluation details are in [phase6_results.md](phase6_results.md).

These results apply to the tested configurations and workloads;
they do not establish universal compatibility or performance.


## 9. Reproduce the Qwen3 Demo

The following commands reproduce a real Codex tool-calling
loop with Qwen3 8B and Ollama.

Prepare the model:

- Run `ollama pull qwen3:8b`.
- Create the local alias with the supplied example definition:

```sh
ollama create qwen3-codex-16k:8b -f examples/Modelfile.qwen3_16k
```

The definition selects the Qwen3 parser and a 16,384-token context window.
Model generations and tool choices can vary between runs.

Direct mode:

`python examples/reproduce_tool_loop.py examples/qwen3_16k.json --mode direct --output runs/qwen3_direct`

Proxy mode:

`python examples/reproduce_tool_loop.py examples/qwen3_16k.json --mode proxy --output runs/qwen3_proxy`

Use a new output directory for each run.

Expected result: the Agent executes a shell command,
receives TOOL_OK, and completes successfully.

The Proxy run records both HTTP model calls.

See [experiment evidence](evidence/qwen3-tool-loop-summary.json)
and [benchmark results](phase6_results.md).

Raw HTTP logs are private and should not be published.

## 10. Additional real-agent evidence

The closeout includes two additional experiments using Codex CLI
0.169.0-alpha.11.1 and `qwen3-codex-16k:8b`.

| Task and mode | Observed result | Evidence |
| --- | --- | --- |
| Dependent commands through CodexProbe | The first command generated a random value. The second command used the returned value. Both commands succeeded and the final answer was `TOOL_OK`. All three HTTP calls were complete, with 1,173 SSE events. | [Dependent tool loop](evidence/qwen3-dependent-tool-loop.json) |
| Flask-4045 through CodexProbe | All 14 HTTP calls were complete, with 5,496 SSE events. Six `sed` edits failed. The source remained identical to the initial checkout. | [Flask comparison](evidence/flask-4045-comparison.json) |
| Flask-4045 directly through Ollama | The run reached its 600-second deadline after six completed tool commands. The attempted edit matched an incorrect class declaration and produced no source change. | [Flask comparison](evidence/flask-4045-comparison.json) |

The Flask runs used the same model, user prompt and initial commit. The
model selected different commands, producing different tool histories.
Codex event logs were retained for both modes. The proxy case additionally
retained the full HTTP requests and SSE responses in the private archive.
All 13 model-generated commands in that case matched the commands Codex
executed. Recorded follow-up requests included the editing error messages.

The 49 original blueprint tests passed before and after both failed Flask
runs. The existing `test_dotted_names` expects dotted names to be accepted.
Independent checks required six dotted names to raise `ValueError` and
confirmed that ordinary names and nested blueprints still worked. Both
candidates failed the dotted-name checks. The measured result is therefore
a failed repair with complete proxy recording.

The task specification explicitly accepts an honest account of where an
open model's tool-calling behavior diverges or breaks. The included evidence
supports reviewing that behavior independently from transport correctness.
Full requests and model responses remain available for private inspection.
