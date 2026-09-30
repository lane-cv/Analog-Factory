# Embedded Agent Implementation Plan

> Agentic workers: use the task boundaries below; test observable behavior before
> implementing. Preserve the shared workspace and unrelated changes.

**Goal:** Embed both model API tools and local CLI agents in the project sidebar.
**Architecture:** Project session coordinator with independent API and CLI drivers,
normalized events and plain JavaScript UI over existing HTTP routes.
**Tech Stack:** Python standard library, vanilla JavaScript/CSS.
**Spec:** ../specs/2026-09-30-embedded-agent-design.md

## Global constraints

- Python 3.12+, no mandatory dependency installation.
- Use existing credentials and EDA services; never invent verification results.
- Fail closed on permissions, malformed events and unknown backend identifiers.
- Retain English/Chinese and light/dark interface support.

## Tasks

- [x] CLI adapter: `agent_cli.py`, subprocess-fixture tests for Codex/Claude events,
  native resume IDs, failure, timeout and process group cancellation.
- [x] API adapter: `agent_api.py`, provider-fixture tests for history, tool results,
  read-only/confirmation, stale edit protection and real simulation records.
- [x] Session coordinator and HTTP routes: `agents.py`, `service.py`, `server.py`;
  tests for durable state, isolation, concurrency, approval, context and shutdown.
- [x] Sidebar integration: static files; browser regression for backend switching,
  sessions/reload, execution progress, approvals, cancellation and project switching.
- [x] Documentation, complete regression suite, JavaScript syntax checks and
  independent code review. Report external-inference validation limits explicitly.

## Review focus

Cancellation while waiting for approval or HTTP must prevent later mutations.
Late responses from another project/session must not replace the visible chat.
Invalid CLI output/exit must not be mistaken for success.
Permission policy must be applied on every resumed turn.
Pending writes must not overwrite a file edited after its approval was requested.

## Verification outcome

156 Python tests passed (41.348 s), including local HTTP and real ngspice tests.
Agent sidebar, provider settings, and original manual-workflow browser checks passed.
JavaScript syntax, Python compilation and git diff whitespace checks passed.
Independent review findings for project configuration and approval content were fixed
and rechecked. No hosted inference or authenticated CLI inference was performed.

Follow-up: fixed Codex intermediate reconnect events being treated as terminal
failures. All 159 tests passed (41.456 s); a minimal authenticated read-only
conversation through the restarted application returned `OK`. Broader CLI task
acceptance remains separate from this connectivity check.
