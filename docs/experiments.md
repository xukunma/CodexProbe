# Experiments and Recorded Results

This page preserves the measured cloud/local comparisons and controls. These are outcomes of specific runs, rather than promises for every model or CLI version. Start with the [README](../README.md) for installation and the current Qwen3 quickstart. Run commands on this page from the repository root.

## Checkout repair with cloud and local backends

The exercise starts from the same checkout module, prompt, and seven-test suite. The bug discounts shipping together with merchandise. The independent verifier uses the original tests and grades the model's saved source.

| Backend | Model calls | Fixture tests before / after | Observed result |
| --- | ---: | --- | --- |
| [OpenAI through Codex's ChatGPT login](../evidence/openai-archived.json) | 3 | 4/7 / 7/7 | Codex edited the source, ran tests, and repaired the calculation. |
| [Ollama with the original Qwen2.5-Coder 7B](../evidence/ollama-codex.json) | 1 | 4/7 / 4/7 | The model returned JSON describing a command inside an ordinary message. Codex executed no tool and the source stayed unchanged. |

The OpenAI row is an offline regrade of an earlier live session. The local row is a later live run of the updated recorder. Each was checked against its private persisted JSONL file. These are separate sessions, rather than a randomized performance comparison.

All four model calls returned HTTP 200 and terminal `response.completed` events. The summaries include body digests and stream checks. Recording correctness and coding-task success were graded separately.

## Qwen2.5 direct-file controls

This control gives the same local model the original source and test suite directly. Requests use the recorder and Ollama's Chat Completions endpoint. Codex's file and shell tools are bypassed.

The control sets temperature 0 and seed 42. It extracts returned Python source, or a JSON `source` field, and strips an optional outer code fence. The model supplies every code change. With `--feedback`, a failed first attempt receives the actual test output and one further opportunity to return a repaired module.

| Output contract | One response | Separate run allowing test feedback |
| --- | --- | --- |
| Plain Python module | [Import failed](../evidence/ollama-direct.json): the reply appended the test module to the source. | [Import failed, then 7/7 passed](../evidence/ollama-feedback-plain.json): the second reply contained a standalone source module. |
| JSON object containing source | [4/7 passed](../evidence/ollama-direct-json.json): shipping was still discounted. | [4/7, then 4/7 passed](../evidence/ollama-feedback.json): the revised source retained the faulty calculation. |

Each cell represents one measured session. All use the same fixture and independent suite. The JSON condition also explicitly requests a single source field.

```sh
python examples/qwen_direct.py --format plain --output runs/plain
python examples/qwen_direct.py --format plain --feedback --output runs/plain-feedback
python examples/qwen_direct.py --format json --output runs/json
python examples/qwen_direct.py --format json --feedback --output runs/json-feedback
```

Use a fresh output directory for every invocation.

## Qwen3 tool calling

The minimal real-agent task asks Qwen3 8B to execute a command printing `TOOL_OK`, read its result, and answer `TOOL_OK`. The alias uses a 16,384-token context window and Ollama's Responses API.

| Check | Direct | Through CodexProbe |
| --- | --- | --- |
| Codex exit code | 0 | 0 |
| Agent turn completed | Yes | Yes |
| Shell command succeeded | Yes | Yes |
| Final answer | `TOOL_OK` | `TOOL_OK` |
| Recorded HTTP calls | Not recorded | 2 |
| Complete HTTP responses | Not recorded | 2/2 |

The [earlier tool-loop summary](../evidence/qwen3-tool-loop-summary.json) records the direct/proxy result. The [current README quickstart](../README.md#quickstart-local-qwen) includes a further successful run from a fresh GitHub checkout on 2026-11-16. That run took 48.675 seconds, and its second request contained the successful tool output.

A separate [dependent-command experiment](../evidence/qwen3-dependent-tool-loop.json) used a random value produced by the first command as input to the second command. Both tools succeeded, the dependency check passed, and the final answer was `TOOL_OK`. It recorded three complete HTTP calls and 1,173 SSE events. Numbered event sequences were contiguous and streamed text/tool arguments matched terminal responses.

These experiments establish the tested tool interactions. They do not establish a successful checkout or Flask repair.

## Flask repair comparison

The [Flask comparison](../evidence/flask-4045-comparison.json) used Codex CLI 0.169.0-alpha.11.1 and `qwen3-codex-16k:8b`.

| Mode | Observed result |
| --- | --- |
| Through CodexProbe | 14 complete HTTP 200 calls and 5,496 SSE events. Six `sed` edits failed. The source remained unchanged. |
| Direct through Ollama | The run reached its 600-second deadline after six completed tool commands. An edit targeted an incorrect class declaration and produced no source change. |

Both runs used the same prompt, model, and initial commit. The model generated different commands and tool histories.

In the proxy run, all 13 model-generated commands matched those executed by Codex. Follow-up requests contained the editing error messages. The model's success claims did not agree with the unchanged source and independently checked result.

The 49 original blueprint tests passed before and after both runs. The existing `test_dotted_names` expects dotted names to be accepted. Independent issue checks instead required six dotted names to raise `ValueError`, while ordinary names and nested blueprints continued to work. Both candidates failed the dotted-name checks.

The outcome is a failed repair with complete proxy recording. The direct failure supports investigating the tested model/agent behavior independently of the proxy. It does not prove that all possible proxy behavior is equivalent.

Codex event logs were retained for both modes. Only the proxy run retained full HTTP bodies, so the runs cannot be compared byte-for-byte at that level.

The [task specification](https://github.com/STAR-Computing/ProgrammingTask/blob/master/07_codex_llm_call_recorder.md) permits an honest account of where an open model's tool-calling behavior diverges or breaks.

## Tests and performance

The package has 25 tests covering passthrough, streaming, compression, authentication, function calls, concurrent requests, failures, shutdown, ordered persistence, and independent grading.

The [closeout verification receipt](../evidence/closeout-verification.json) records earlier build/import checks and the user-confirmed full-suite pass. The later GitHub installation check in the [README](../README.md#testing-and-expected-results) reports `25 passed in 9.97s` in an ordinary macOS terminal. Restricted agent environments may deny loopback binding or nested OS sandbox creation.

The [benchmark report](../phase6_results.md) describes a controlled local HTTP benchmark with 20 measured requests per mode:

| Metric | Direct | Proxy | Difference |
| --- | ---: | ---: | ---: |
| Median time to first byte | 1.039 ms | 2.436 ms | +1.396 ms |
| Median full response time | 25.450 ms | 26.148 ms | +0.697 ms |

A separate real-agent benchmark completed four `TOOL_OK` runs: two direct and two through CodexProbe. Their wall-clock times do not establish a performance advantage, because inference time varies between runs.

## Inspect and share evidence

The [protocol contract](../protocol_contract.md) explains raw-byte handling, authentication headers, SSE streams, tool-call preservation, and completion semantics.

Public summaries retain status fields, test results, event checks, and SHA-256 digests without full bodies. Digests identify the archived bodies. Fidelity claims also rely on passthrough tests and comparison with available reference data.

Raw request/response archives and CLI events remain private. The [walkthrough manifest](../walkthrough/manifest.json) identifies readable teaching artifacts. Treat those artifacts as teaching copies, rather than substitutes for the original recordings.
