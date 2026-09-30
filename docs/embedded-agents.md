# Embedded agents

Analog Factory's assistant follows Netcatty's separation of provider drivers,
normalized events, application context and persistent conversations. The reference
was inspected at commit `b2ed83ae5108b479d1b509aba208a233e5240a0f`; the implementation
here is independent Python/JavaScript code, with no copied GPL code or Electron
dependency.

| Capability | Model API | Codex CLI | Claude Code CLI |
|---|---|---|---|
| Protocol | Chat Completions or native Ollama tools | `exec --json` | `-p --output-format stream-json` |
| Continuation | Bounded conversation history | Native session ID | Native session ID |
| Output | Response/tool events, polled | JSONL events, polled | JSONL events, polled |
| File access | ProjectStore confinement and revisions | CLI read-only/workspace-write sandbox | `--restricted` confined file tools |
| Writes | Per-action confirmation and diff | Explicit session edit mode | Explicit session edit mode |
| Simulation | Approved Workspace.run with evidence | Native CLI activity, not an application run record | Use manual controls or API mode |
| Stop | Cancel token; pending HTTP may drain to timeout | Terminate process group | Terminate process group |
| Usage | Provider-reported; unknown if absent | CLI-reported; unknown if absent | CLI-reported; unknown if absent |
| Authentication | Endpoint-scoped existing provider settings | Existing CLI authentication | Existing CLI authentication |

Settings → tools accepts `codex` and `claude` executable paths. The adapters require
the corresponding structured output flags; Claude additionally requires
`--restricted`. Detection establishes executable availability, not authentication
or model availability. No CLI installation, login, paid inference or user-wide
configuration change is performed automatically.

`agents.py` owns project sessions under `.af/agent-sessions/<id>.json`. `agent_api.py`
owns the bounded model/tool loop; `agent_cli.py` owns CLI framing, normalization and
process cleanup. Codex `error` events can be intermediate reconnect notices: the
sidebar displays them and lets the CLI continue retrying. A terminal `turn.failed`,
nonzero exit, timeout, or missing completion still fails the turn. API sessions
remain bound to their original protocol and endpoint.
Settings changes to that binding require a new conversation. Sessions have fixed
backend, model and permission mode, preventing permission changes during resume.

The HTTP API exposes `/api/agents` and project-scoped `/agent/sessions` routes for
create/list/get, `messages`, `cancel`, and `approval`. IDs are scoped to projects;
the existing same-origin/Host checks apply. Only one turn per project and at most
four turns per server execute concurrently. Events and context are size bounded.
Restarted servers mark abandoned active turns interrupted; they do not reconnect
to orphaned processes after a hard crash. Normal server shutdown cancels workers.
Conversations are local workspace data and are not included in project exports.

Selected files and optional run logs are explicit initial context. API read tools
can read other non-hidden files in the selected project; CLIs can inspect project
files under their own policies. Prompts and tool output are untrusted text in the
browser. A successful conversation is not a passed simulation or design signoff.

API write proposals show a diff and retain the previous text through the existing
revision service. Changed files are checked again after approval. CLI writes go
through the CLI's own file tools and do not automatically create application
revisions. Use version control/CLI change history where required. This release
does not supply an interactive terminal or CLI per-tool approvals; those would need
a richer transport such as Codex app-server. ACP is not necessary for these
structured noninteractive adapters.

References:

- [Netcatty driver registry](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/electron/bridges/aiBridge/sdk/index.cjs)
- [Netcatty event normalization](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/electron/bridges/aiBridge/sdk/emit.cjs)
- [Netcatty stream lifecycle](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/electron/bridges/aiBridge/sdk/sdkStreamHandlers.cjs)
- [Codex noninteractive interface](https://developers.openai.com/codex/noninteractive/)
- [Claude programmatic interface](https://code.claude.com/docs/en/headless)
