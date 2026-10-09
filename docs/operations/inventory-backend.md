# Backend operation inventory

This inventory covers the standard-library Python core and optional serialization
and training adapters. `operation_catalog.py` and the domain registration modules
declare business/query operations and map compatibility API routes to those
handlers. `Workspace`, `ProjectStore`, `RunManager`, `Jobs`, `AgentManager`, the
terminal manager and transfer services remain the owners of business state.
The descriptors, rather than this document, are authoritative for current schemas,
defaults, examples, permissions, and availability. Read them with operation
discovery or `python3 -m analog_factory operations describe OPERATION_ID`.

## Existing routes and registered handlers

`P` means `/api/projects/{project_id}`. `S`, `R`, and `J` mean a session, run,
and job ID belonging to that project. Legacy responses preserve their original
record/list shapes and status codes. New operation invocations return the shared
result envelope.

| Existing route | Operation ID | State owner / effect |
| --- | --- | --- |
| `GET /api/status?project_id=…` | `workspace.status` | Optional project toolchain, desktop and shared launch availability |
| `GET /api/settings` | `settings.get` | Public workspace settings; secret presence only |
| `PUT /api/settings` | `settings.update` | Workspace configuration and separate credentials |
| `POST /api/provider/test` | `provider.test` | Provider model catalog network request |
| `GET /api/agents` | `agent.backends` | Effective API/native executable discovery |
| `GET /api/agents/models` | `agent.models` | Native model and effort catalog / optional selected model inspection and refresh |
| `GET /api/projects` | `project.list` | ProjectStore metadata |
| `POST /api/projects` | `project.create` | New template project; HTTP 201 |
| `GET P` | `project.detail` | Project, file list, run list and job list |
| `PUT P/config` | `project.configure` | Validated replacement project overrides |
| `GET P/files?path=…` | `file.read` | Confined visible text read |
| `PUT P/files?path=…` | `file.write` | Atomic replacement with prior revision |
| `GET P/history?path=…` | `file.history` | Revision metadata for one file |
| `POST P/restore` | `file.restore` | Revision restoration through normal write |
| `POST P/simulation` | `simulation.configure` | Generated SPICE deck saved with revision history |
| `GET P/export` | `project.export` | Sources and run/job/episode evidence ZIP |
| `POST P/chat` | `chat.send` | Legacy read-only model suggestion; saved conversation |
| `GET P/workflows` | `workflow.list` | Saved workflow JSON |
| `POST P/workflows` | `workflow.save` | Validate and save schema-v1/v2 workflow |
| `POST P/workflows/run` | `workflow.run` | Bounded tuning or typed graph job; HTTP 202 |
| `POST P/evaluate` | `evaluation.run` | Isolated random-search episodes; HTTP 202 |
| `GET P/jobs` | `job.list` | Durable orchestration records |
| `GET P/jobs/J` | `job.get` | Progress or final job outcome |
| `POST P/jobs/J/cancel` | `job.cancel` | Cooperative cancellation |
| `GET P/runs` | `run.list` | Project and evaluation episode evidence |
| `POST P/runs` | `run.start` | Real EDA execution; HTTP 202 |
| `GET P/runs/R` | `run.get` | Manifest, metrics, verdict and staleness |
| `POST P/runs/R/cancel` | `run.cancel` | Process-group cancellation |
| `POST P/runs/R/retry` | `run.retry` | Fresh snapshot and ID; HTTP 202 |
| `GET P/runs/R/log` | `run.log` | Last 256 KiB of each recognized log |
| `GET P/runs/R/waveforms` | `run.waveforms` | Parsed actual raw waveform artifact |
| `GET P/runs/R/artifact?path=…` | `run.artifact` | Confined output artifact download |
| `GET P/agent/sessions?q=…` | `agent.session.list` | Public conversation search |
| `POST P/agent/sessions` | `agent.session.create` | Durable session; HTTP 201 |
| `GET P/agent/sessions/S` | `agent.session.get` | Public turns/events/pending approval |
| `PUT P/agent/sessions/S` | `agent.session.update` | Rename after worker drains |
| `DELETE P/agent/sessions/S` | `agent.session.delete` | Delete idle conversation record |
| `POST P/agent/sessions/S/messages` | `agent.session.send` | Bounded asynchronous turn; HTTP 202 |
| `POST P/agent/sessions/S/cancel` | `agent.session.cancel` | Cancel turn and pending approval |
| `POST P/agent/sessions/S/approval` | `agent.session.approve` | Exact pending user decision |
| No separate legacy route; included in project detail | `file.list` | Visible regular project files |

`LEGACY_ROUTES` entries have `(method, fullmatch_pattern, operation_id, mapper)`
plus an optional fifth dictionary containing `status` and/or `response`.
`mapper(groups, query_lists, body)` produces validated operation parameters.
Path ownership overrides any body-supplied IDs. `response: export` or `artifact`
decodes the JSON-safe `{filename, content_type, content_base64}` result into the
legacy attachment response.

New services use generic invocation without requiring a parallel route or
agent-specific implementation:

| Operation family | Owner and contract |
|---|---|
| `project.rename/trash/restore`, `file.move/trash/restore_deleted` | Recoverable lifecycle with source revisions and active-work guards |
| `workflow.catalog/parse/export`, `job.control` | Typed graph discovery, safe JSON/YAML interchange, debug stepping and explicit human review |
| `simulation.batch.*`, `simulation.statistics.qualify` | Bounded Cartesian scenarios, measured evidence and live model qualification |
| `training.*` | Optional isolated local trainer, trusted taskset configuration, checkpoints, held-out evaluation and replay |
| `agent.cli.status/releases/check_updates` | Workspace-owned public release cache and optional project executable detection; anonymous fixed official sources, no installation/inference/authentication, workspace-only opt-in scheduler |
| `native.terminal.*` | Human-owned native PTY; bounded private memory output and owned process-group cleanup |
| `artifact.*`, `transfer.*`, `project.import.*` | Confined artifact metadata/chunks and bounded recoverable archive transfer |

The generated [handler inventory](boundary-inventory.json) lists the actual
registered IDs and implementation locations, including additions to these families.

## State, permissions, and asynchronous work

| Owner | Persistent data | Lifecycle |
| --- | --- | --- |
| Workspace | `settings.json`, separate `.credentials/provider.json` | Configuration lock and endpoint-bound in-memory secret |
| ProjectStore | `projects/P/project.json`, sources, `.af/history` | Atomic file replacement and revisions; 2 MiB text editor limit |
| RunManager | `.af/runs/R/{manifest.json,input,output}` | Source snapshot before return; daemon worker and process group |
| Jobs | `.af/jobs/J.json`, optional cancel marker | Cooperative daemon worker above EDA runs |
| AgentManager | `.af/agent-sessions/S.json` | Durable turns/events; one active turn per project, four globally |
| Evaluation | `.af/episodes/J/index/…` | Isolated design copies and frozen evaluator evidence |
| Operation journal | `.operations/journal.sqlite3` | Private bounded event/replay/trace storage; interrupted claims never reexecute |
| Automatic scope grants | `.agent-scopes` | Private session/project-bound action budgets, separate from imported project evidence |
| Native terminal manager | `.af/native-terminals` metadata | Live process authority and output remain in memory; imported metadata never restores control |
| Training | `.af/training`, `.af/training-tasksets` | Immutable tasksets, worker logs, trajectories and validated local checkpoints |

Mutations declare `write`, `execute`, `configure`, or `approve` permission.
Ordinary queries declare `read`. API-agent mutations require explicit confirmation,
while cancellation can interrupt existing work without another approval.
Agents cannot invoke the approval decision operation themselves. File writes,
restores, simulation configuration, workflow saves, workspace/project configuration,
and session rename/delete require an optimistic version on the generic interface.
Legacy routes retain their previous versionless transport contract.

Project provider overrides accept endpoint/model/environment-variable settings;
credential values and storage/clear controls belong to `settings.update` and are
rejected by `project.configure`. They are never silently accepted and discarded.

Run/job/session records remain authoritative. An accepted asynchronous task is not
a successful simulation or verification. Unavailable tools produce real unavailable
run evidence. DRC/LVS require recognized completed reports; exit code zero alone is
insufficient. Runs record source/effective-input hashes, metrics and provenance,
artifact hashes, and current staleness.

`Workspace.run_availability` supplies the same environment predicates to
`workspace.status.operation_availability`, `run.start` discovery, and execution.
Status accepts an optional project ID and evaluates its effective tool/PDK
overrides; without a project all launch controls are unavailable. SKY130 simulation
and Magic/verification require prepared PDK assets; GUI editors require a desktop,
while xschem netlisting remains headless. Launch additionally validates actual
source files and arguments before any editor source creation or process start.
Unavailable launches retain real terminal run records and their diagnostic reason.

Run cancellation terminates the whole process group. Job callbacks cancel their
active child run. Provider response reads can be interrupted by shutting down the
owned response socket; connection establishment remains timeout-bounded. No
subsequent tools execute after cancellation. Native CLI cancellation terminates its
process group. An agent record can say cancelled while its worker still drains;
the active slot remains reserved until cleanup.

## Actual execution boundaries

- `run.start` supports ngspice simulation, xschem GUI/netlisting, Magic GUI/DRC/
  extraction, and Netgen LVS. GUI editors intentionally edit original project
  files; batch runs use snapshots. EDA programs and executable design inputs are
  trusted host programs, not sandboxed by the registry.
- Schema-v1 `workflow.run` preserves the six-node specification → parameter →
  simulate → measure → condition → report bisection chain. Schema-v2 adds typed
  registry-backed nodes, conditional branches, bounded loops/retries/timeouts,
  source-conflict checks and human review. Arbitrary code is not an executable node.
- `evaluation.run` samples the educational circuit's `RD` parameter uniformly
  from 1000 to 10000 for 1–20 isolated episodes. It performs no model training or
  PVT/signoff validation. The internal `run.start.episode` selector is accepted
  only from the evaluation executor and resolves under the owning project's
  confined episode directory.
- The registry reuses API-agent reviewed writes, session approvals, and real
  execution evidence. Embedded native agents receive a scoped MCP bridge and
  restricted native tooling so application actions can use the same operations.
  Installed CLI programs remain trusted processes. Native EDA GUI edits and
  independently launched editors do not use application file revision writes.
- Static-file serving, HTTP origin checks, and desktop/WebSocket proxying are
  transport infrastructure, not registered business operations. Browser-only
  selection/navigation/drafts/layout are covered by the frontend operation
  catalog. Arbitrary native desktop clicks are not implemented operations.
- Project/file lifecycle uses recoverable trash and restore, with active-work and
  confinement checks. Permanent deletion is not exposed as an operation. Optional
  model training and statistical modes expose their actual runtime/qualification
  availability; a missing dependency or rejected qualification never becomes success.

## Verification

`tests/test_operation_catalog.py` exercises the real adapters: file revision and
restore parity, input rejection before process creation, secret-safe settings and
ZIP export, configured SPICE/workflow files, durable session route mapping, public
rejection of private episode selection, executable discovery examples/defaults,
unavailable-run evidence, and actual ngspice metrics/logs/artifacts when installed.
`tests/test_operation_availability.py` checks status/discovery/launch parity for
missing tools, missing or partially prepared PDKs, project overrides, and headless
versus GUI execution, plus project credential-control rejection.
Existing server, agent, EDA, workflow, job, and storage tests retain their domain
coverage. Native model catalog tests patch the catalog adapter's discovery seam.
