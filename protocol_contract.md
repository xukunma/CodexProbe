# CodexProbe — Protocol Contract

## 1. Purpose & Scope

CodexProbe is a transparent HTTP reverse proxy designed to improve the observability of model interactions in Codex CLI. It records complete model requests and responses and supports switching between compatible LLM backends without modifying Codex's source code.

CodexProbe handles communication and logging, while Codex CLI manages tool execution and the LLM backend performs inference. The proxy must not alter model behavior or reinterpret model outputs.

## 2. System Architecture

The system consists of three components:

**Codex CLI ↔ CodexProbe ↔ LLM Backend**

Codex CLI constructs model requests and manages the agent workflow. CodexProbe receives, records, and forwards HTTP traffic. The backend, such as Ollama or OpenAI, processes requests and generates responses.

CodexProbe acts as an HTTP server for Codex and an HTTP client for the backend. Codex connects to the endpoint returned by `recorder.start()`, while CodexProbe forwards requests to its configured backend `base_url`.

Backend switching requires configuration changes rather than modifications to the proxy implementation.

## 3. HTTP Request Contract

CodexProbe must preserve the original HTTP request method, correctly map API paths, and forward the complete request body without modification.

Request bodies may include developer instructions, conversation history, model parameters, and tool definitions. Changing these contents could affect the model's behavior.

The proxy preserves necessary end-to-end headers, handles hop-by-hop headers separately, and manages authentication according to its configuration.

## 4. HTTP Response Contract

CodexProbe must preserve the backend's HTTP status code and response body while maintaining correct response headers.

Headers such as `Content-Type`, `Content-Encoding`, and `Content-Length` must remain consistent with the transmitted data. Connection-specific headers are handled separately.

Incomplete responses must be recorded with available data and error information. A successful HTTP status does not necessarily indicate a complete response.

The `complete` field represents upstream response completeness, while downstream disconnections are tracked separately.

## 5. SSE Streaming Contract

CodexProbe must forward SSE data as it arrives, without waiting for the complete response.

The proxy must preserve stream content and ordering while recording the complete response for later analysis. Network chunks must not be confused with individual SSE events.

The implementation uses `iter_any()` to receive data, `outgoing.write()` to forward it, and `payload.extend()` to record it.

The archived Qwen2.5 session contains 52 SSE events, including 44 text deltas.
Additional Qwen3 sessions include a successful dependent tool loop with
1,173 events across three calls and a failed Flask repair with 5,496 events
across 14 calls. Their numbered event sequences were continuous, and the
reconstructed text and tool arguments matched the terminal responses.
The public summaries are in [evidence](evidence/).

## 6. Function Calling Contract

CodexProbe must preserve tool definitions, structured function calls, arguments, identifiers, and subsequent tool results without modification.

A valid `function_call` differs from an ordinary text message containing JSON-like tool instructions. CodexProbe must not convert text into tool calls or execute tools itself.

In the archived Qwen session, the model returned a regular `message` describing an `exec_command` rather than a structured `function_call`. Consequently, Codex did not execute the intended command.

The newer Qwen3 dependent-command session returned structured function
calls, received both tool results and completed successfully. The Flask
session continued across many tool calls but repeatedly generated invalid
editing commands. Those commands and subsequent error feedback were
captured by the recorder. The direct Flask control also failed to modify
the source. These observations describe the tested model configurations
and workloads.

## 7. Logging & Backend Configuration

CodexProbe records complete request and response bodies in session-specific JSONL files, preserving model-call arrival order.

Each record includes relevant metadata such as timestamps, backend information, HTTP status, latency, completion status, and errors.

Different compatible model backends can be selected through configuration without changing the proxy code.

Raw logs may contain sensitive information and should be protected. Sanitized teaching copies are used for sharing and explanation.

## 8. Verification & Known Gaps

The package contains 25 tests covering HTTP passthrough, live SSE relay,
compression and authentication, structured function calls, incomplete
responses, concurrent streaming, shutdown and ordered log persistence.
The earlier full-suite report recorded 25 passes. The current closeout
reran five pure-logic cases successfully. Loopback server creation and
nested macOS sandbox creation were denied by the execution environment.
The user subsequently ran the full suite in an ordinary terminal and
confirmed that it passed on 2026-11-16.

The archived OpenAI session contains three complete model calls and a
checkout repair independently regraded from 4/7 to 7/7 passing tests.
Local Qwen evidence includes both successful tool execution and failed
coding attempts. The configuration selects the upstream service, and the
same recorder implementation captures both backends. The controlled
overhead measurements and their scope are recorded in
[the benchmark report](phase6_results.md).

The closeout status is recorded in
[the verification receipt](evidence/closeout-verification.json). Backend
compatibility is evaluated for the API format, tool representation and
streaming behavior used by each client and server pair.
