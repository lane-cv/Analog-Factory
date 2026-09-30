# Embedded project agents

The requested outcome is an assistant embedded in Analog Factory, inspired by
Netcatty, supporting both configured model APIs and installed agent CLIs. Keep
Python 3.12+, zero mandatory dependencies, the existing browser UI and manual EDA
workflow. Existing source and desktop changes are preserved.

## Design

A project-scoped session service owns durable conversations, asynchronous turns,
normalized events, cancellation, and approval requests. The sidebar selects API,
Codex or Claude, an optional model, and a permission mode. Sessions survive page
reload, project switching and server restart; unfinished turns become interrupted.
Only one turn per project may run at a time. Context includes selected saved files
and an optional selected execution log, with bounded sizes. Backend output is text,
never executable browser markup. Polling delivers incremental events using the
existing HTTP server; API response text arrives when the provider completes.

API agents use OpenAI-compatible or native Ollama tool calling and retained message
history. Project tools list/read files, propose writes, start simulation, and read
run records/logs. Read-only is the default. Confirm mode waits for an explicit UI
decision before every write or simulation; edits use ProjectStore revision history
and reject changes to the file between proposal and approval. EDA actions use
Workspace.run so actual evidence is retained. Bounded tool rounds and cancellation
prevent unbounded work. Cancelling a blocked HTTP request marks the turn cancelled
immediately; the network operation can take its configured timeout to return, and
must never execute a later tool after cancellation.

CLI agents run their official JSONL noninteractive interfaces in the project cwd,
using existing CLI authentication without reading authentication files. Provider
session IDs are saved for resume. Child process groups are terminated on cancel,
timeout and shutdown. Read-only and explicitly selected workspace-write are
launch-level modes; CLI per-tool approval is not presented as supported. No bypass
of CLI sandbox/permission flags is allowed. CLI tools and changes appear as events;
they do not automatically become verified EDA results. Claude file tools only are
enabled; API simulation tools or manual controls provide recorded EDA runs.

## Interfaces

`GET /api/agents?project_id=:pid` reports effective project backend capability/discovery.
`GET|POST /api/projects/:pid/agent/sessions` lists/creates sessions.
`GET /api/projects/:pid/agent/sessions/:sid` gets history/state.
`POST .../:sid/messages` starts a turn with message/files/run_id.
`POST .../:sid/cancel` stops a turn.
`POST .../:sid/approval` resolves a pending approval by ID and boolean decision.

`run_api_turn(provider, history, prompt, workspace, project_id, mode, emit,
cancel, approve)` and `run_cli_turn(backend, executable, project_root, model,
mode, backend_session_id, prompt, emit, cancel, timeout=300)` return a dictionary
with reply, usage, history (API), backend_session_id (CLI). `emit(event)` accepts
JSON-safe type/text/tool/status fields. `approve(name, arguments)` returns bool or
raises InterruptedError on cancellation. Events include message, tool_call,
tool_result, status, usage, session, error. Drivers raise on failed completion.

## Evidence and references

Netcatty uses driver registration and normalized events; ACP is one adapter rather
than a required universal transport. This implementation independently adapts the
architecture without copying GPL code.

- https://github.com/binaricat/Netcatty/blob/main/electron/bridges/aiBridge/sdk/index.cjs
- https://github.com/binaricat/Netcatty/blob/main/electron/bridges/aiBridge/sdk/emit.cjs
- https://github.com/binaricat/Netcatty/blob/main/electron/bridges/aiBridge/sdk/sdkStreamHandlers.cjs
- https://developers.openai.com/codex/noninteractive/
- https://code.claude.com/docs/en/headless

Acceptance covers real local HTTP provider fixtures, subprocess fixtures, durable
session/resume, permission enforcement, cancellation, failed backend output,
context/path bounds, actual ngspice evidence when installed, project isolation and
the browser flow. Hosted model/CLI inference requires configured credentials and
is reported separately from deterministic integration tests.
