# Phase 6 — Benchmark & Evaluation

## 1. Objective

Evaluate the correctness and performance overhead of CodexProbe
when forwarding and recording LLM communication.

## 2. Environment

- Platform: macOS
- Python: 3.14.6
- pytest: 9.2.0
- Agent: Codex CLI
- Model: Qwen3 8B via Ollama
- Context window: 16,384 tokens

## 3. Controlled HTTP Benchmark

A local Mock Backend returned identical SSE responses for Direct
and Proxy connections.

Each mode was measured 20 times after warm-up.

| Metric | Direct | Proxy | Difference |
|---|---:|---:|---:|
| Median TTFB | 1.039 ms | 2.436 ms | +1.396 ms |
| Median total latency | 25.450 ms | 26.148 ms | +0.697 ms |

CodexProbe recorded 22 HTTP calls, including two warm-up calls.

The additional latency was small under these local test conditions.

## 4. Real Agent Benchmark

Task: Execute a shell command that prints TOOL_OK, read the
result, and return TOOL_OK.

| Round | Mode | Wall Time | Result |
|---|---|---:|---|
| 1 | Direct | 52.938 s | PASS |
| 1 | Proxy | 39.608 s | PASS |
| 2 | Proxy | 49.876 s | PASS |
| 2 | Direct | 68.681 s | PASS |

Median Direct execution time: 60.810 s.
Median Proxy execution time: 44.742 s.

All four runs successfully executed the tool and completed
the agent turn.

Each Proxy run recorded two HTTP calls.

Evidence:
phase6_runs/real_agent_20261114_192650/

## 5. Correctness Evidence

- The earlier full-suite report recorded 25 passing tests.
- SSE fragmentation and concurrent streaming passed.
- Connection failure isolation passed.
- Graceful shutdown tests passed.
- Large SSE response recording passed.
- Real Codex tool calling succeeded through CodexProbe.

## 6. Limitations

The controlled HTTP benchmark measures local proxy overhead
under a fixed workload.

The real Agent benchmark includes model inference,
initialization, and tool execution.

Only two runs per mode were performed in the real Agent
benchmark. The observed faster Proxy times do not establish
that CodexProbe improves model performance.

The complex checkout repair task was not completed by the
tested local models.

## 7. Conclusion

CodexProbe successfully recorded complete HTTP communication
during real Codex tool execution.

The controlled benchmark showed approximately 0.7–1.4 ms
of additional latency, depending on the metric.

The results support correctness and low overhead in the
tested scenarios, without claiming universal performance
or compatibility guarantees.

## 8. Closeout follow-up

The 2026-11-16 closeout rechecked the archived OpenAI HTTP digests and
stream metrics against all three persisted calls. Its saved candidate was
independently regraded from 4/7 to 7/7 passing checkout tests.

The later Qwen3 dependent-command session completed two tool calls using
feedback from the first command, then returned `TOOL_OK`. Its three HTTP
calls contained 1,173 SSE events. The numbered sequences and reconstructed
outputs passed the offline stream checks.

The Flask-4045 proxy case captured 14 complete calls and 5,496 SSE events.
The model's six failed editing commands left the source unchanged. The
direct control reached its 600-second deadline with unchanged source.
Both cases failed the independent issue checks. These runs provide a
recorded example of model editing and error-recovery limitations.

Package construction, regular installation, editable installation and
public API import were checked locally. The current environment allowed
five pure-logic tests and collection of all 25 cases. It denied the
loopback mock server and nested macOS sandbox needed by the remaining
integration tests in that restricted environment. The user subsequently
ran the full suite in an ordinary terminal and confirmed that it passed
on 2026-11-16. See [the closeout receipt](evidence/closeout-verification.json).
