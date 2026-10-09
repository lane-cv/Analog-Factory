# Unified operations design and implementation plan

## Intent and architecture
Every existing application operation should be discoverable and invoke the same service implementation from UI and agents. Preserve the Python standard library server, vanilla JavaScript UI, filesystem project store, run manager, and job manager. No dependency or framework migration. Existing uncommitted UI changes must survive.

The registry owns stable IDs, JSON-compatible input/output schemas, permission declarations, execution metadata, validation, error envelopes, revision checks, idempotency, and change events. Catalog handlers delegate to the current services. Legacy HTTP routes become adapters and keep their response shapes. Structured HTTP discovery/invocation is the external protocol; no MCP server exists to reuse. Built-in provider tools and workflows delegate to registry handlers. Browser-only state stays in each identified client, with commands delivered by polling and success returned only after acknowledgement. Native EDA desktop interaction remains an explicit integration boundary.

Business state lives in ProjectStore/Workspace/AgentManager/RunManager/Jobs. Queries execute at the server. Frontend state (navigation, selection, drafts, graph positions, geometry) lives in a specific browser session. Browser commands execute there. Server events contain identifiers/revisions rather than payloads or secrets. Source revision checks and a serialized mutation boundary reject stale writes; native editor changes are detected from the source fingerprint.

## Implementation tasks
1. Inventory: enumerate backend routes and UI event handlers; classify executable, unavailable, and absent capabilities. Record state owners and coverage.
2. Core (`operations.py`, `operation_schema.py`, `operation_sessions.py`): registration validation, invocation context, permission checks, standard envelopes, optimistic versions, task tracking/cancellation, idempotency, and event cursor polling. Tests exercise invalid schema/permission, concurrency, deduplication, and disconnected clients.
3. Catalog (`operation_catalog.py`): schemas/metadata and real adapters for every existing HTTP business/query route. Integrate server routing; preserve legacy transport semantics. Test UI/agent parity and actual ngspice execution.
4. Frontend (`static/operations.js`, existing UI files, `operation_ui.py`): shared HTTP client, semantic command handlers, client lifecycle, acknowledgement, state publication, refresh on events, draft conflict protection. Browser tests verify actual navigation/selection/layout and backend changes.
5. Agents and workflows: generated discovery tools plus bounded invocation retaining approval and read-only restrictions. Existing tool aliases delegate to registry. Workflow run steps use registered run execution.
6. Enforcement/documentation: registry-generated reference, operation template, inventory, CI and static checks for direct transport/business bypass, integration tests, explicit limitations.

## Review focus and constraints
- Secret values never enter public configuration, generic invocation history, events, or diagnostics.
- A stale file/configuration write cannot silently overwrite newer human input.
- Browser timeout/disconnect must never produce a success acknowledgement.
- Permissions and approval apply before deduplication or execution; agent approvals cannot approve themselves.
- An accepted asynchronous task is not a successful EDA result; status/logs/artifacts remain authoritative.
- Preserve legacy routes for existing integrations and tests; frontend operations share their handlers.
- Static detection cannot prove all dynamic DOM/native desktop interaction coverage. List and test the boundary honestly.

Proceed in the existing shared workspace to preserve active user edits; no commits or destructive worktree changes. The user explicitly requested inspection followed by implementation, so this plan executes without additional approval gates.
