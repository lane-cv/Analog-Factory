# Browser operation inventory

The application keeps its existing vanilla-JavaScript controllers and HTTP APIs. `AFOperations.invoke(operation_id, parameters, {expected_version, idempotency_key})` invokes any registered server operation, including new operations with no legacy route; it returns the success envelope or throws an error carrying the failed envelope. `AFOperations.list` and `describe` expose discovery. `static/operations.js` supplies a named dispatch boundary; `operation_ui.py` describes the matching operations to the server registry. Domain operations still execute through the existing controller's HTTP calls. No selector, script, coordinate-click, or arbitrary `data-action` endpoint is exposed.

`ui.sessions` lists live browser clients and redacted state. All other `ui.*` operations require a `client_id`; optional `ui_version` rejects a stale acknowledged browser state. The browser executes the handler, then acknowledges its real result and current state. A disconnected browser fails. A timeout means the outcome is unknown, not success. Different browser tabs have independent IDs, drafts and geometry.

## Semantic operation coverage

| Existing UI area and operations | Registry command(s) | Existing owner / domain operation |
| --- | --- | --- |
| Eight workspace tabs, project selection/clear, refresh | `ui.workspace.navigate`, `select_project`, `refresh` | `setTab`, `selectProject`, `refreshProject`; project reads |
| New-project/new-file dialog visibility; form drafts | `ui.workspace.dialog`, `ui.form.read`, `ui.form.set` | Existing dialogs and allowlisted form controls; actual creation uses project/file domain operations |
| Explorer folders and AI context selections | `ui.workspace.set_folder`, `set_context` | Existing tree `<details>`, shared `state.context` |
| Language and appearance | `ui.workspace.set_preferences` | Existing optimistic preference controller, rollback and draft preservation; settings write |
| Desktop reconnect | `ui.workspace.reconnect_desktop` | Existing same-origin noVNC iframe and tool status |
| Safe settings draft editing | `ui.settings.set_draft` | Settings form; requires configuration permission; excludes password and file controls |
| Editor selection, content draft, inspection, save, history | `ui.editor.open`, `set_content`, `read`, `save`, `history` | `openFile`, `setEditorDraft`, `saveFile`; file read/write/revisions |
| Conflict inspection, merged drafts, retained draft variants and recovery | `ui.editor_recovery.list`, `read`, `restore`, `discard`, `inspect`, `resolve` | `editor-recovery.js`; checked source reads, browser IndexedDB transactions, explicit draft IDs; ordinary editor save is the only writer |
| Managed native window controls, named instances/pins and Magic paint | `ui.native_canvas.*`, `native.canvas.*` | `native-canvas.js`; owned native session adapter, actual revision/acknowledgment, registered file.write for explicit history saves |
| Run selection, result comparison, metric evidence, copy log | `ui.runs.select`, `compare`, `inspect_metric`, `copy_log` | Run/log/waveform reads, current run, clipboard |
| Simulation/verification/evaluation draft configuration | `ui.form.read`, `ui.form.set` | Existing analysis fields, verification overrides, episodes/seed; domain simulation/evaluation operations perform execution |
| Waveform trace visibility, horizontal range, cursor, zoom, pan, reset, CSV export | `ui.waveform.set_traces`, `set_view`, `set_cursor`, `zoom`, `pan`, `reset`, `export` | Existing trace/zoom/cursor state and drawing functions |
| Versioned workflow draft, typed nodes/mappings/retries, conditional edges, JSON/YAML import | `ui.workflow.read`, `new_graph`, `set_graph`, `select_node`, `add_node`, `update_node`, `remove_node`, `connect`, `disconnect`, `apply_json`, `apply_document` | Schema-v1 tuning and schema-v2 graph editor; `workflow.parse` validates imported executable documents |
| Workflow bounds/name/limits drafts | `ui.form.read`, `ui.form.set` with `workflow` | `captureWorkflow` and existing change handlers |
| Workflow save, saved-list display, JSON/YAML download | `ui.workflow.save`, `list_saved`, `export` | Workflow registry operations; YAML dependency availability is explicit |
| Graph stepping, pause, continue and human review | `job.control` | `graph-controls.js`; review requires human approval authority |
| Exact source replacement preview and human-approved graph patches | `file.change.preview`, `source_patch` node, `job.control` | Shared preview and source identity checks, registered file.write, preserved revision and accepted source generations; no automatic patch retries |
| Project rename, file move, trash, restore, import and backup | `ui.lifecycle.*` and project/file operations | `project-controls.js`; selected-project revisions and active-work guards |
| Sweep/PVT/engineering variation and qualified model statistics | `ui.simulation_batch.*`, `simulation.batch.*`, `simulation.statistics.qualify` | `simulation-batches.js`; previewed bounded scenarios, actual run evidence and cancellation |
| OTA pre/post characterization, selected PVT cases, targets and raw evidence | `ui.characterization.*`, `reference.characterize`, ordinary `job.*`/`run.*` queries | `characterization.js`; pinned draft revisions, real simulator analyses, explicit electrical verdicts, acknowledged inspection and cancellation |
| Tasksets, local training, held-out evaluation, resume and replay | `ui.training.*`, `training.*` | `training-controls.js`; immutable published tasksets, optional runtime and real simulator rewards |
| Codex/Claude paths and official updates | `ui.agent_cli.*`, `agent.cli.*`, `project.configure`, `settings.update` | `agent-cli-controls.js`; semantic path/tracking drafts, pinned project and workspace revisions, detected executables, cached official release notes and opt-in checking |
| Native terminal panel, options, input, size and stop | `ui.native_terminal.*`, `native.terminal.*` | `native-terminal.js`; human approval permission required, raw output excluded from snapshots and durable receipts |
| Project provider overrides | `ui.settings.project_provider_*`, `project.configure` | `provider-fields.js` form helper and `app.js`; explicit generation/capability settings and pinned revision |
| Agent read/new/select conversation | `ui.agent.read`, `new`, `select_session` | Existing `AFAgents` closure, session loading and draft restoration |
| Backend/model/effort/permission/fast settings | `ui.agent.set_config` | Existing `changeConfig`, mode and model capability checks; requires configuration permission; nullable effort inherits native defaults |
| Composer read/write, selected files, run, attachments | `ui.agent.read_draft`, `set_draft`, `set_context`, `attach_run`, `add_attachments`, `remove_attachment` | Drafts, context and bounded text/image ingestion; images require declared model capability |
| Send/stop | `ui.agent.send`, `stop` | Existing session create/message/cancel APIs; explicit execution/write permissions and agent confirmation |
| History open/close/search; model filtering/catalog refresh/favorites | `ui.agent.history`, `search_models`, `refresh_models`, `favorite_model` | Existing history search and browser model preferences |
| Context/model/command/export menus; preset prompts; message edit/copy; export | `ui.agent.popover`, `prompt`, `edit_turn`, `copy_turn`, `export` | Existing named popovers, composer draft, clipboard and conversation exporter |
| Jump to latest; tool/reasoning disclosure state | `ui.agent.scroll_latest`, `disclosure` | Existing message panel and stable disclosure IDs |
| Reusable prompts and management dialog | `ui.agent.quick_messages`, `save_quick`, `remove_quick`, `manage_quick` | Existing browser quick-message cache |
| Explorer/assistant/log visibility; dimensions; composer size; agent expansion; reset; compact overlays | `ui.layout.set_panel`, `resize`, `expand_agent`, `reset`, `close_overlay` | Existing private layout controller, viewport bounds, focus management and browser layout cache |
| Known disclosure panels and named panel scrolling | `ui.workspace.disclosures`, `set_disclosure`, `scroll` | Explicit stable IDs on known details; named panels with start/end positions |
| Redacted browser inspection | `ui.workspace.read` | Explicit controller snapshots, never a generic form/DOM dump |

## Existing business actions

Project creation/export; file creation/import/read/write/revision restore; simulation deck generation; simulation/DRC/LVS/extraction/editor launch; run retry/cancel/log/artifact download; workflow persistence/execution; evaluation/job records/cancellation; toolchain detection; settings/provider test/key lifecycle; and conversation creation/message/history/rename/delete/cancel remain domain operations. UI commands select or edit the associated browser state rather than implementing second copies of these services. The HTTP compatibility adapter resolves the existing endpoints through the shared server registry.

Manual tab/file/run/project events use the same semantic dispatch entry points. Other adapters call the same existing controller functions used by clicks and keyboards. Ordinary typing still updates the owning draft controller. Pointer drag, wheel, keyboard shortcuts, file picker, paste/drop, native details, and local downloads retain their existing accessible user interactions.

## State ownership and concurrency

- `app.js`: project/file/run selection; source drafts; graph drafts; comparison, waveform traces/view/cursor; form drafts. File content is returned only by explicit `ui.editor.read`, not broadcast in routine session snapshots.
- `agents.js`: conversation selection/configuration, local prompt draft, streaming text, attachments, automatic-scope drafts, history/model filters and menus. Routine snapshots include attachment names/counts and whether a draft exists; explicit draft reads reveal the requested prompt. Text limits remain 20 files, 64 KiB each and 256 KB total. Images are limited to four, 256 KiB each and 512 KiB total, with MIME/magic checks. Automatic grants require the human owner and retain exact project/path/action limits.
- Project lifecycle, simulation batch, training and native terminal controllers own their separate dialogs and publish explicit redacted snapshots. Provider field helpers do not own transport. The terminal renderer retains its own display state and never publishes terminal bytes through generic UI snapshots.
- `layout.js`: geometry/visibility/focus and `af-workspace-layout`; `preferences.js`: language/theme cache backed by server UI settings.
- Existing project/session caches and quick-message storage remain browser-local. No new remote secret store is introduced.
- HTTP requests carry `X-AF-Client` and relevant `If-Match`. Editable source content pins the ETag returned by its original read until successful save or clean reload. Settings, simulation, new-file and workflow drafts pin their version when opened; background polling cannot silently advance their write preconditions.
- EDA buttons consume `status.operation_availability` for the selected `project_id`, as provided by the shared operation catalog. The frontend does not duplicate tool, PDK, template or desktop eligibility rules; project-specific status also honors project configuration overrides.
- Identifier-only server events refresh selected project metadata and clean source content. Dirty source and unsaved form controls are preserved. Draft revision counters detect edits without publishing their contents; expected browser state is rechecked immediately before executing a versioned command. Subsequent stale writes fail instead of overwriting newer data.
- Asynchronous selection commands verify that their requested project, file, run, conversation or view actually remains selected before acknowledging success. A newer manual selection produces `VERSION_CONFLICT` when it supersedes a pending command.
- Message sending confirms a newly accepted turn in the selected project and conversation. If the user switches projects before its response arrives, the command reports a conflict and asks the caller to inspect history before retrying; the server may already have accepted the message.

## Deliberate boundaries

- Password/API-key input values are never included in browser discovery, snapshots or generic form commands. Credential management remains the dedicated server operation; entering a credential through the human settings form continues to work.
- No remote UI command can click Allow/Deny or accept a native confirmation prompt. Human approval remains a separate server permission boundary. UI operations that may autosave declare write effects; settings/model-permission configuration declares configuration permission; send declares execution/write effects.
- The embedded noVNC application owns its native desktop login and EDA canvas interactions. `reconnect_desktop` does not claim to manipulate an Xschem/Magic canvas. Native EDA execution is exposed through the corresponding domain operations.
- Native file-picker selection cannot be automated by passing a client filesystem path. Use explicit named attachments, source contents or workflow JSON/YAML. Downloads and clipboard commands use the target browser and may be blocked by browser policy.
- Native file-picker dialogs and arbitrary focus/scroll coordinates remain ordinary browser interactions. Substantive import actions accept explicit source/text/JSON content, and named panels support semantic start/end scrolling.
- Graph execution supports the registered typed nodes and bounded loops. It cannot run arbitrary Python or shell expressions. Statistical model modes require live compatibility qualification; educational parameter variation is explicitly separate. Training needs the optional pinned local runtime and never treats an inference API as a weight-training interface.

## Enforced declarations and their limits

[`ui-actions.json`](ui-actions.json) records all recognized action-attribute
namespaces, other data attributes (including replay/file/selection controls),
literal listener event types, switch labels, literal operation calls, controller modules
and nonliteral call counts. [`boundary-inventory.json`](boundary-inventory.json)
classifies each first-party script and identifies every registered Python handler.
Adding an unclassified script, a literal unknown registry operation, or direct
browser transport outside `operations.js` fails `scripts/check_operations.py`.
The pinned xterm renderer is excluded from business-action scanning and its source
hash is verified instead.

These are lexical declarations. They do not resolve arbitrary JavaScript aliases,
computed IDs, conditional expressions or dynamically generated DOM; a recorded
action is not proof that its effect works. Browser handler/catalog parity and
the relevant real browser tests remain required. The Python AST check similarly
covers named workspace call chains in explicitly listed adapters/engines, with
documented source-read/job-supervision exceptions; it is not whole-program flow
analysis or a sandbox.

## Verification

`tests/browser-operations.cjs` runs against a disposable real workspace and real Chromium. It compares browser handlers against registry discovery; executes acknowledged remote commands; checks shared manual/remote editor state, pinned-version conflict rejection after a background refresh, source preservation, graph and layout effects, attachment/prompt controls, credential redaction, invalid commands, and independent browser targets. Existing preference, layout, agent and workspace browser suites remain applicable.

`tests/browser-operation-extension.cjs`, against `tests/operation_test_server.py`, discovers a newly registered example, invokes it through a real button using the generic browser client, renders its result, and compares it with the direct HTTP agent result. The example needs no legacy route or browser-specific implementation.

`tests/browser-agents.cjs` also checks that semantic sending returns its accepted turn ID, a project switch during the message response returns a conflict, and navigating away from a stale source draft cannot overwrite an agent's saved changes.

The added browser suites exercise lifecycle changes, complex waveform controls,
graph execution/stepping, YAML file interchange, simulation batches, training,
provider forms, streamed image input and automatic file-write scopes. Their local
protocol fixtures establish integration behavior without claiming paid-provider
performance. `tests/test_operation_coverage.py` introduces deliberately missing
declarations, unknown IDs and direct source writes to verify that enforcement fails.
