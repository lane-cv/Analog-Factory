# Operating Analog Factory through the shared registry

The Python registry is the shared execution boundary for the browser, API assistant,
visual tuning/evaluation workflows, native assistant MCP bridge, CLI, and authorized
external HTTP callers. Existing service classes still own project files, credentials,
conversations, jobs and run evidence. The implementation keeps the current Python
standard-library server and vanilla JavaScript stack.

- [Architecture and implementation plan](design.md)
- [Backend inventory and coverage](inventory-backend.md)
- [Frontend inventory and coverage](inventory-ui.md)
- [Generated operation reference](reference.md), [schemas](registry.json), [agent tool descriptions](agent-tools.json)
- [CLI examples](cli.md)
- [Verification and boundaries](validation.md)
- [Codex/Claude project configuration and official release tracking](../agent-cli-updates.md)

## HTTP discovery and invocation

The existing Host/Origin/Fetch-Site guard applies to all interfaces. The application
remains a **single-user trusted local service**, including its explicitly enabled
LAN mode. Bearer credentials add scoped agent callers; they do not turn the legacy
local-owner surface into a multi-user authentication system.

| Capability | Endpoint |
|---|---|
| `list_operations` | `GET /api/operations?module=file&keyword=write&project_id=ID` |
| `describe_operation` | `GET /api/operations/file.write?project_id=ID` |
| `invoke_operation` | `POST /api/operations/invoke` |
| `get_operation_status` | `GET /api/operations/status?task_id=TASK_ID` |
| `cancel_operation` | `POST /api/operations/cancel` with `task_id` |
| `subscribe_events` | `GET /api/operations/events?after=0&wait=20` |
| Browser state | Invoke `ui.sessions`; then `ui.workspace.read` or a named state query |

Discovery accepts module, keyword, project, client and EDA kind context, and
`available=true`. Descriptions include actual availability reasons, prerequisites,
schemas, defaults, units, effects, permissions, confirmation and task behavior.
Missing tools do not become successful runs: an attempted EDA launch can return an
`UNAVAILABLE` envelope with its real unavailable run record. Unsupported planned
capabilities are documented in the inventory and have no executable stub.

API-model and native MCP `list_operations` tools use byte-bounded registry pages
instead of the full HTTP catalog: at most 48 KiB of UTF-8 JSON, 64 entries by
default, and a requested `limit` of 1–100. Follow `next_cursor` with the same
`module`/`keyword` filters until `complete` is true; `total`, `offset`, and
`returned` describe the matching catalog. Cursors reject changes to the catalog,
filters, project or caller permissions. Availability is refreshed on each page.
Long summary text is explicitly marked `summary_truncated`; full schemas remain
available through `describe_operation`. An individually oversized descriptor
returns `DISCOVERY_ITEM_TOO_LARGE` with HTTP/CLI guidance, never a partial schema.
HTTP and CLI full-catalog discovery remain compatible with existing clients.

```json
{
  "operation_id": "project.create",
  "parameters": {"name": "Agent amplifier", "template": "amplifier"},
  "idempotency_key": "create-amplifier-1"
}
```

Read `file.read` with the returned project ID, then reuse its `version` for writes:

```json
{
  "operation_id": "file.write",
  "parameters": {
    "project_id": "REPLACE_WITH_PROJECT_ID",
    "path": "reports/design-notes.txt",
    "content": "Review the bias point before verification.\n"
  },
  "expected_version": "REPLACE_WITH_VERSION_FROM_READ",
  "idempotency_key": "review-notes-1"
}
```

Start an actual EDA run and inspect the returned task rather than interpreting
acceptance as a simulation verdict:

```json
{
  "operation_id": "run.start",
  "parameters": {
    "project_id": "REPLACE_WITH_PROJECT_ID",
    "kind": "simulate",
    "options": {"netlist": "testbenches/pre.cir"},
    "timeout": 120
  },
  "idempotency_key": "baseline-simulation-1"
}
```

Results contain `ok`, `operation_id`, `trace_id`, `data`, structured `error`, `status`,
`task_id`, `progress`, `artifacts`, and `version`. Query success means that inspection
succeeded; inspect the record's status and verification verdict to judge an EDA task.
Progress is `null` when the underlying tool does not report a meaningful quantity.
Task IDs resolve durable run/job records, including isolated evaluation runs. Agent
tasks also identify the particular conversation turn. Bounded trace correlation,
event history and idempotency receipts persist in the private SQLite operation
journal; detailed tool evidence remains in the existing run/job files.

Run and job cancellation uses the existing process-group/cooperative mechanisms.
Provider streams observe cancellation between bounded reads; a blocked network
connection can still wait for its configured timeout. Cancellation prevents subsequent
mutations. Browser commands cannot undo changes that already happened while an
acknowledgement was lost. Their timeout is an explicit unknown outcome.

## Permissions and confirmations

`InvocationContext` is server-owned and contains principal, permissions, source,
project scope, and trusted approval outcome. Callers cannot submit this context in
an operation request. Permissions are `read`, `write`, `execute`, `configure`, `ui`,
and `approve`; every operation declares them. Project-scoped callers cannot create
another project, inspect another project's records, or change global settings.
Agents cannot decide their own pending approval.

For an external integration, configure the server's `AF_AGENT_TOKEN`, optionally
`AF_AGENT_PROJECT`, and comma-separated `AF_AGENT_PERMISSIONS` (default `read`). Send
`Authorization: Bearer …`. Explicitly granted external mutation calls also send
`confirmed: true` to acknowledge the operation's declared effects. That flag is an
intent acknowledgement under the token's prior authorization, **not evidence of an
independent human approval**. An external token never grants `approve`. Use the
built-in scoped turn bridge when each mutation must pause for a human decision.

Built-in API and native agents retain read-only modes. Mutation-capable native
sessions now use the same per-action approval UI as API sessions. Their short-lived
MCP credential is scoped to the project, kept in child environment variables, and
revoked when the turn ends. Direct native write/shell/application tools are disabled
for bridge-backed turns. Installed CLI support is validated without making a paid
model call; provider login/model availability remains environment-dependent.

Credential updates use `settings.update` with `configure` permission. Public settings
return only credential-presence/storage indicators. Provider secrets are stored by
the existing credential service, never in registry metadata, event payloads or
invocation histories. Generic browser form/state interfaces exclude secret fields.

## Browser sessions and concurrent changes

Each actual browser registers an ephemeral client with an unguessable acknowledgement
credential. Polling publishes an explicit redacted snapshot. UI operations require a
live `client_id`, execute the existing semantic controller in that browser, and wait
for acknowledgement. Missing/disconnected clients, invalid targets, changed state,
and missing acknowledgement are errors. Coordinate clicks and arbitrary JavaScript
are not the primary interface.

```json
{
  "operation_id": "ui.workspace.navigate",
  "parameters": {"client_id": "REPLACE_WITH_LIVE_CLIENT_ID", "tab": "simulation", "ui_version": 12}
}
```

Use the `version` in `ui.sessions` as optional `ui_version`. Project-bound agent
commands automatically bind a browser version; the server and browser both reject
state changes before execution. Manual edits increment draft-state counters without
publishing draft contents or credentials. Browser source/settings/graph forms pin
the revision from which the draft was created. Background reads cannot advance that
write precondition. Conflicts preserve the draft for user review.

Source revisions cover project files and stable session metadata. They are deliberately
coarse: an unrelated source/session rename can require a refresh. Native editor source
writes are noticed by source fingerprint polling. Native file writes outside the
application cannot participate in its in-process lock; fingerprints catch changes
before a write, but no filesystem transaction can lock an uncooperative external
editor across that final check and write. Use one serving workspace process.

Legacy HTTP routes remain adapters over the registry. Updated browsers send
`If-Match` and `X-AF-Client`; old trusted-local integrations without a revision retain
legacy unconditional-write behavior. Structured versioned invocations and token-bound
agents require a revision. This compatibility exception is explicit, not a guarantee
of conflict protection for old clients.

Events are bounded cursor-based long polling (up to 25 seconds). They contain IDs,
status and versions, not operation inputs or secrets. `reset: true` means the retained
history no longer covers the cursor; reread current state. Browser leases must be
registered again after a server restart. The
browser refreshes clean data and preserves unsaved drafts. Idempotency keys serialize
concurrent retries and replay a stored envelope, including uncertain frontend outcomes.
The private journal retains at most 1,000 completed responses for 30 days, 2,000
events, and 10,000 task traces/claims. Claims are durable before side effects; an
interrupted ambiguous request returns OUTCOME_UNKNOWN instead of executing again.
Unresolved claims stay pinned. Scope and permission checks apply again on replay.
PTY/artifact bytes and unsafe response content are excluded from durable replies;
volatile operations reject idempotency keys. One owner process per workspace is
required. Transfer/artifact reads use their immutable handle or artifact revision
token and return version:null; all ordinary source mutations retain revision guards.

## Adding an operation

1. Define a stable ID and strict input/output schemas. State defaults, enums, units,
   effects, prerequisites, permissions, confirmation, version and lifecycle support.
2. Register one handler in the relevant catalog. Reuse the service that owns the state.
3. Connect the UI with `AFOperations.invoke`; an operation does not need a legacy route.
4. Discovery, generic API/MCP invocation and generated tool descriptions use that same
   registration. Do not write a second agent implementation.
5. Add an integration test for the actual effect and invalid/unauthorized inputs. Run
   `python3 scripts/check_operations.py --write`, review the inventory diff, then run
   `python3 scripts/check_operations.py` and the affected tests.

```python
from analog_factory.operations import Operation

def register_project_summary(registry):
    registry.register(Operation(
        operation_id='project.source_summary',
        name='Project source summary',
        description='Count visible source files in this project.',
        module='project', kind='query', permissions=('read',), idempotent=True,
        input_schema={
            'type': 'object', 'properties': {'project_id': {'type': 'string'}},
            'required': ['project_id'], 'additionalProperties': False,
        },
        output_schema={
            'type': 'object', 'properties': {'file_count': {'type': 'integer', 'minimum': 0}},
            'required': ['file_count'], 'additionalProperties': False,
        },
        handler=lambda context, parameters: {
            'file_count': len(registry.workspace.projects.files(parameters['project_id']))
        },
    ))
```

```javascript
const result = await AFOperations.invoke('project.source_summary', {project_id});
renderFileCount(result.data.file_count);
```

`tests/operation_test_server.py` and `browser-operation-extension.cjs` exercise this
extension pattern with a real browser button and agent HTTP call. For a frontend
browser operation, add its schema to `operation_ui.COMMANDS` and register the controller
with `AFOperations.register`; reuse that controller from manual UI handlers. Include
editable form drafts as well as action buttons, so an agent can prepare the same
state without prematurely executing its action.

For an owned nonbrowser frontend, declare `kind='frontend'`, an explicit
`frontend_adapter` and a `frontend_target(context, parameters)` resolver. The resolver
returns a verified live target's `state`; the registry checks project scope before
execution and on the actual result. The handler still requires the adapter's real
acknowledgment and target revision. A resolver is not an execution receipt, and
discovery must not perform a native action. `operation_native_canvas.py` is the
implemented example. The default browser adapter continues to require `client_id`
and optional `ui_version`; native commands name their actual project/run/session.
Availability callbacks return a list of explicit reasons (empty means available),
not an availability envelope. Incomplete targets and malformed callbacks are
reported as unavailable rather than making the entire catalog fail.

Run the repeatable browser gate with
`python3 scripts/validate_browser.py --output <new-evidence-directory>` in the
configured Playwright environment. It freezes source, allocates a free port and
verifies the owned server's PID/source/workspace receipt. Actual native windows
require the separate [native canvas acceptance](../native-canvas.md) driver.

CI validates schema/ID/permission/handler declarations, generated reference/tool
schemas, route registration, and selected adapter boundaries. It flags new direct
`fetch`/XHR/WebSocket in application JavaScript and direct business calls in known
entry points. It records declared UI actions and checks browser handler/catalog
parity. These checks do not prove arbitrary dynamic DOM or third-party desktop
semantics; maintain the coverage inventory and behavior tests alongside them.
