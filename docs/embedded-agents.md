# Embedded agents

Analog Factory's assistant follows Netcatty's separation of provider drivers,
normalized events, application context and persistent conversations. The reference
was inspected at commit `b2ed83ae5108b479d1b509aba208a233e5240a0f`; the implementation
here is independent Python/JavaScript code, with no copied GPL code or Electron
dependency.

The browser layout also follows the reference chat component: a 36 px tool header,
32 px agent header, continuous assistant messages, right-aligned user bubbles,
replacement history view, and an inset resizable composer. Expanding the sidebar
uses the same DOM, session, and pending request. Theme colors and language remain
part of Analog Factory's shared interface settings. Keyboard resizing and mobile
dialog focus handling are provided by `layout.js`; `agents.js` owns conversations
and drafts; `agent-content.js` renders escaped Markdown and tool disclosures.

| Capability | Model API | Codex CLI | Claude Code CLI |
|---|---|---|---|
| Protocol | Chat Completions or native Ollama tools | `exec --json` | `-p --output-format stream-json` |
| Continuation | Bounded conversation history | Native session ID | Native session ID |
| Output | Response/tool events, polled | JSONL events, polled | JSONL events, polled |
| File access | Shared operations and project confinement | Shared operations over scoped MCP | Shared operations over scoped MCP |
| Writes | Per-action confirmation; legacy write tool includes diff | Per-action application approval | Per-action application approval |
| Simulation | Approved shared run operation and evidence | Same run operation and evidence | Same run operation and evidence |
| Stop | Cancel token; pending HTTP may drain to timeout | Terminate process group | Terminate process group |
| Usage | Provider-reported; unknown if absent | CLI-reported; unknown if absent | CLI-reported; unknown if absent |
| Authentication | Endpoint-scoped existing provider settings | Existing CLI authentication | Existing CLI authentication |

Settings → tools accepts `codex` and `claude` executable paths. The adapters require
the corresponding structured output flags; Claude additionally requires
`--restricted`. Detection establishes executable availability, not authentication
or model availability. No CLI installation, login, paid inference or user-wide
configuration change is performed automatically.

Claude's restricted mode skips user settings. The adapter restores only
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
`ANTHROPIC_CUSTOM_HEADERS`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_MODEL`,
`ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL`, and `ANTHROPIC_SMALL_FAST_MODEL`
from the user `settings.json` into its child environment. The directory is
`CLAUDE_CONFIG_DIR`, defaulting to `~/.claude`. These values stay in memory and
are not command arguments or persisted in the workspace. User `env` takes
precedence over inherited values, as in the native CLI; host-managed provider
configuration is preserved. A user `model` fallback goes into explicit settings,
below managed policy and explicit model selection. Project/local settings,
arbitrary environment entries, hooks, and executable credential helpers are not
loaded; existing native login and inherited credentials still work without this file.

`agents.py` owns project sessions under `.af/agent-sessions/<id>.json`. `agent_api.py`
owns the bounded model/tool loop; `agent_cli.py` owns CLI framing, normalization and
process cleanup. `operation_mcp.py` is the stdio MCP adapter for native sessions.
Each native turn receives an environment credential bound to its project and
permission mode. The running HTTP server retains the approval callback; native
tools cannot mark themselves approved. The credential is revoked and outstanding
approval callbacks are released when the native turn ends. Codex `error` events can be intermediate reconnect notices: the
sidebar displays them and lets the CLI continue retrying. A terminal `turn.failed`,
nonzero exit, timeout, or missing completion still fails the turn. API sessions
remain bound to their original protocol and endpoint.
Settings changes to that binding require a new conversation. Sessions have fixed
backend, model, effort, Fast preference, and permission mode, preventing permission changes
during resume. New sessions store a boolean `fast_mode`; older records without
the field continue to inherit their native CLI configuration and are labeled as
inherited in the UI. Codex uses per-process `service_tier="fast"` or the explicit
standard sentinel `service_tier="default"`. Claude uses
`--settings '{"fastMode":true}'` or `false` with the existing restricted tool flags.
Claude's Fast selection requires an explicitly compatible Opus model, so enabling
it never silently switches from Sonnet/Haiku. Reported Claude Fast status and
disable reasons appear in the transcript only when emitted by the CLI.

`GET /api/agents/models?backend=codex|claude&project_id=…` uses that project's
configured executable. Codex discovery calls `initialize` and `model/list` on its
stdio app server, without starting a thread or inference. Claude discovery checks
its version/help and returns built-in model aliases, not an account entitlement list.
Metadata commands share an 8-second discovery deadline and output limits, cached for five
minutes (30 seconds on failure), and refreshed with `&refresh=1`. Discovery failure
keeps default/custom model selection available and disables unverifiable Fast
capabilities. Discovery does not read login files or change global settings. Claude
alias capability checks use the same controlled user/environment configuration
bridge described above; credentials are never returned in metadata. At turn
startup Claude receives the user connection settings described above; neither
adapter modifies global configuration or native login files.

Codex native terminal approvals remain disabled in the embedded, noninteractive
process. Its MCP configuration allows exactly the six Analog Factory bridge tools
and sets each tool's `approval_mode` to `approve`; unlisted tools retain `prompt`
and are excluded by `enabled_tools`. This permits reaching the scoped workspace
callback, which still checks permissions and requires application approval for
mutations. The bridge labels query tools read-only and leaves invocation and
cancellation marked as mutating. Native sandbox restrictions and managed policies
are unchanged. See [Codex MCP approval configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

If an older embedded session reports `MCP tool call requires approval, but approval
policy is never`, load the updated server and retry the turn. Changing the entire
CLI to interactive `on-request` does not provide a terminal approval interface in
the embedded JSONL driver. No global Codex configuration edit is required.

The composer's **Effort** control is separate from Fast. **Default** is stored as
`effort: null` and adds no CLI override; existing sessions without the field retain
that behavior. Codex choices come from the installed CLI's live model metadata,
including `max` and `ultra` only when advertised. Claude choices intersect the
installed CLI's advertised `--effort` values with verified model support. Unknown
models, unresolved defaults, and unsupported aliases remain Default-only. Custom
model capability checks use `GET /api/agents/models?...&model=MODEL_ID` and return
`selected_model.effort_levels`; model rows also include `default_effort` when known.

Choosing effort preserves the composer and selected context, and starts a new
conversation when changing an existing session's configuration. Choices known to
be compatible survive model changes. Incompatible or not-yet-discovered custom
models reset to Default; discovery enables a new explicit choice without silently
restoring an override later. Running conversations
lock these controls. The choice is persisted, restored from history, and reapplied
on every native turn/resume as Codex `model_reasoning_effort` or Claude `--effort`.
An explicit Claude choice also sets the matching child-process effort environment
value so an inherited preference cannot override the selection; parent environment
and managed policy remain unchanged.
Explicit unsupported choices fail validation before session creation. Model API
effort capabilities are not discovered by the current provider adapter, so API
sessions accept Default only; no unsupported override is silently ignored.

Agents use the same registration: `ui.agent.set_config` accepts `effort` for an
identified browser, and `agent.session.create` persists it directly. For example,
after checking `agent.models` for the selected model:

```json
{"operation_id":"agent.session.create","parameters":{"project_id":"PROJECT_ID","backend":"codex","model":"MODEL_ID","mode":"read-only","effort":"high"}}
```

The effort interaction follows Netcatty's capability-driven
[thinking selector](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/components/ai/ComposerThinkingChip.tsx)
and [model-change selection rules](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/infrastructure/ai/composerPicker.ts).
Claude model gates follow its [documented effort support](https://code.claude.com/docs/en/model-config#adjust-effort-level).

The HTTP API exposes `/api/agents` and project-scoped `/agent/sessions` routes for
create/list/get, bounded `?q=` history search, title updates via `PUT`, deletion via
`DELETE`, `messages`, `cancel`, and `approval`. Active or draining sessions cannot
be renamed or deleted. IDs are scoped to projects;
the existing same-origin/Host checks apply. Only one turn per project and at most
four turns per server execute concurrently. Events and context are size bounded.
Restarted servers mark abandoned active turns interrupted; they do not reconnect
to orphaned processes after a hard crash. Normal server shutdown cancels workers.
Conversations are local workspace data and are not included in project exports.

Selected files and optional run logs are explicit initial context. All agent
backends inspect visible project files through shared operations. Codex receives
only the workspace MCP server with a read-only native sandbox and shell, browser,
app, plugin and hook capabilities disabled. Claude receives an empty built-in tool
set, restricted mode and the workspace MCP allowlist. These restrictions are
reapplied on resume. Prompts and tool output are untrusted text in the
browser. A successful conversation is not a passed simulation or design signoff.
UTF-8 text attachments are persisted with each turn and passed as untrusted context,
with a 64 KiB per-file limit, 20 combined selected files/attachments, and the existing
256,000-byte combined prompt limit. Importing an attachment never writes a project
file. Editing a prior message restores its attachment and run selections; the next
send validates project files again, using their current saved contents.

The common agent tool surface is `list_operations`, `describe_operation`,
`invoke_operation`, `get_operation_status`, `cancel_operation`, and
`subscribe_events`. Discovery returns structured pages capped at 48 KiB, with
64 entries by default (`limit` supports 1–100). Follow `next_cursor` using the
same filters until `complete` is true, or narrow the module/keyword to keep a
large catalog within the existing bounded tool-round budget. Summary text may be
explicitly shortened; complete input/output schemas, permissions and examples
are available on demand through `describe_operation`. Oversized descriptors fail
explicitly with full HTTP/CLI inspection guidance instead of a clipped schema.
The legacy API aliases remain supported and delegate their work to the registry.

Every mutating agent invocation requires the application approval callback. The
project version is captured before approval, and browser operations additionally
capture the target client's state version. Changes while approval is pending
reject the operation. Writes from every backend retain the previous file through
the revision service. Agent approval operations and credential configuration are
unavailable to agents, and proposed secret values are redacted before event or
history storage. An interactive terminal and ACP are not required for this
structured tool transport. Native model execution still depends on an installed,
authenticated CLI; transport tests use local deterministic fixtures without paid
inference.

References:

- [Netcatty chat composition](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/components/AIChatPanelContent.tsx)
- [Netcatty side panel](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/components/AIChatSidePanel.tsx)
- [Netcatty driver registry](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/electron/bridges/aiBridge/sdk/index.cjs)
- [Netcatty event normalization](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/electron/bridges/aiBridge/sdk/emit.cjs)
- [Netcatty stream lifecycle](https://github.com/binaricat/Netcatty/blob/b2ed83ae5108b479d1b509aba208a233e5240a0f/electron/bridges/aiBridge/sdk/sdkStreamHandlers.cjs)
- [Codex noninteractive interface](https://developers.openai.com/codex/noninteractive/)
- [Claude programmatic interface](https://code.claude.com/docs/en/headless)
- [Codex speed and Fast mode](https://learn.chatgpt.com/docs/agent-configuration/speed)
- [Codex per-process configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Claude Fast mode](https://code.claude.com/docs/en/fast-mode)
- [Claude model aliases](https://code.claude.com/docs/en/model-config)
