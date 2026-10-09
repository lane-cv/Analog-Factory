# Versioned workflow graphs

Schema v1 remains the six-node RD bias-tuning chain. Schema v2 executes a typed
graph independently of the visual editor. The executor calls the same registered
file, project, model-session and EDA operations used by the other adapters. It
does not execute Python expressions, shell commands or model-generated tools.

The public Python interfaces are:

```python
from analog_factory.workflow_graph import validate_graph, execute_graph, GraphControl

flow = validate_graph(workflow_json)
control = GraphControl(step=flow.get('debug', {}).get('step', False))
result = execute_graph(workspace, project_id, flow, job, cancel_event,
                       update_callback, control=control)
```

`execute_graph` accepts the same job/cancellation/update arguments as the v1
executor. `validate_workflow` and the legacy execution entry point dispatch by
`schema_version`. Use the registered `workflow.save` and `workflow.run` operations
for application invocation. `workflow.parse` and `workflow.export` validate and
exchange JSON or YAML without saving or executing a graph. JSON needs no dependency.
YAML requires the optional extra (`pip install '.[yaml]'` from this checkout, or
the distribution's `python3-yaml`). Operation discovery reports unavailable YAML
with an installation explanation when it is missing.

The editor imports `.json`, `.yaml` and `.yml` files, and downloads either format.
An invalid document leaves the draft intact. Both formats preserve `schema_version`
and pass the same executable graph validation. Documents are bounded to 2 MiB,
64 nesting levels and 50,000 parser items. The data-only YAML subset rejects
anchors, aliases, explicit tags, duplicate/non-string mapping keys, multiple
documents and non-finite numbers. Only `true`/`false` become booleans, so the graph
edge key `on` remains a string. Parsing uses restricted
[PyYAML SafeLoader](https://pyyaml.org/wiki/PyYAMLDocumentation) constructors.

In the Workflows view, **New graph** opens a v2 simulation/measurement/report
draft. Select a node to edit its configuration, input mappings, output aliases,
retries and timeout. Connection controls select success/failure and true/false
branches. The limits card configures shared budgets and optional initial stepping.
**Save**, **Run graph**, and **Load saved** use the same registered operations as
agents. **New v1 tuning chain** restores the original tuning editor.

The running-job panel exposes node evidence, stepping, continue/pause and explicit
human review. Historical jobs show recorded evidence without live control buttons.
UI operations `ui.workflow.new_graph`, `update_node` and `connect` offer the same
typed fields and acknowledge their actual browser state; unsaved field edits
advance the draft revision used by approval conflict checks.

## Graph structure and mappings

```json
{
  "schema_version": 2,
  "name": "Check a parameter",
  "nodes": [
    {"id": "bias", "type": "parameter", "config": {"value": 1.5}},
    {"id": "check", "type": "condition", "config": {"operator": "gt"},
     "inputs": {"left": {"node": "bias", "port": "value"},
                "right": {"value": 1.0}}},
    {"id": "report", "type": "report",
     "inputs": {"bias": {"node": "bias", "port": "value"}}}
  ],
  "edges": [{"source": "check", "target": "report", "when": true}],
  "limits": {"runtime_seconds": 30, "max_node_executions": 100,
             "max_iterations": 20}
}
```

Inputs are literal `{"value": ...}` mappings or references with `node` and `port`.
References create dependencies even without a control edge. A reference may carry
an explicit `default` for an unavailable output. JSON types are checked when
validating known port connections and again when resolving runtime values.

A node may expose named aliases into its output object:

```json
{"outputs": {"target": {"path": "data.target", "type": "number"}}}
```

Paths select JSON fields or numeric array indices; they cannot execute code.
An alias with the wrong runtime type fails the node. Graph-level `outputs` use
the same literal/reference mappings as inputs. Top-level `inputs` supply values
to `input` nodes through `config.name`, with an optional `config.default`.
Built-in output names and `error` are reserved; aliases cannot replace a review
decision, branch result, measurement-satisfaction flag or tool outcome.

## Executable node types

| Type | Inputs and configuration | Result |
|---|---|---|
| `input` | `config.name`, `config.type`, optional default | Typed `value` from graph inputs |
| `specification` | Object input or `config.value` | Specification `value` |
| `parameter` | Numeric input or `config.value` | Numeric `value` |
| `project` | Path input/config, optional `parse_json`; alternatively `run_id` | File content/data and exact `sha256`, project detail, or an actual run and its logs |
| `netlist` | Text `content`, numeric `value`, `config.parameter` | Deck with exactly one matching `.param` changed |
| `simulate` | Optional generated `content` and `netlist`, `config.options` | Actual run `result`, `metrics`, `run_id`, `success` |
| `drc`, `lvs`, `extract` | Adapter settings in `config.options` | Actual run and verification evidence |
| `measure` | Run `result`/`metrics`, metric/target/tolerance inputs or config | Finite `value`, `satisfied` |
| `measure` (waveform) | `run_id`, `config.signal`, optional plot index/statistic | min, max, sample mean, RMS, or peak-to-peak from the saved waveform |
| `condition` | `left`, `right`, comparison operator | Boolean `result` |
| `loop` | Optional state/values, bounded nested `config.body` | Iteration outputs, last `result`, `satisfied` |
| `human_review` | Optional value, prompt | Explicit human `approved` decision and reviewed value |
| `source_patch` | Fixed `config.path`, mapped text `content` and `expected_sha256`; optional prompt | Mandatory exact diff review, registered write receipt and verified `applied` result |
| `model` | Prompt/context; backend/model/effort/files config | Read-only session reply, provider usage, session ID; monetary cost remains unknown |
| `report` | Any named mapped inputs; optional title | JSON report retained with job evidence |

Waveform nodes call `run.waveform_stats`, which streams every original raw sample;
bounded display sampling cannot change these measurements. Mean and RMS are sample
statistics, not time-weighted integrals. The parser supports ASCII artifacts up to
512 MiB with bounded memory; larger or binary inputs fail explicitly. Complex
signals use magnitude and the scale uses its real component. Empty or numerically
unrepresentable statistics fail the node instead of becoming zero.
For time-weighted circuit measurements use ngspice `.meas` and extract the actual
metric. DRC/LVS success depends on the adapter's authoritative reports; unknown
verification never becomes a synthetic pass. Netlist processing works on an
in-memory deck that becomes a run snapshot override, preserving saved sources.

## Branches, failures and loops

Control edges default to `on: "success"`. They may instead select `failure` or
`always`. A `when` boolean filters condition, measurement-satisfaction or human
review outputs. A node waits until all control and mapped-data dependencies have
finished. It runs when at least one incoming control edge is active. Skipped
branches stay skipped and never start child operations; a merge can use explicit
default mappings for data from a skipped branch.

Every node exposes an `error` output when it fails. A failure edge handles that
failure; `on_error: "continue"` also permits later independent work. Otherwise an
unhandled failure terminates the workflow. A successful diagnostic/report graph
means its reporting work completed: failed child verification remains failed in
the embedded evidence.

Loops require `config.max_iterations` from 1 to 100 and a schema-v2 `config.body`.
Each iteration receives graph inputs `index` (zero based), `state`, and `item` when
the loop has a candidate-values array. The body returns named graph outputs. The
default `state` output carries state forward; `config.carry` can select another
name. A boolean `done: true` stops the loop; `config.until` selects another output
name. Exhausting candidates or the node's iteration count returns
`satisfied: false`; it never claims that a design specification was met.

Dependencies outside explicit loop nodes must be acyclic. Up to three nested
loop levels, 200 nodes and 2000 edges per graph are accepted. All nested loops and
retries consume the same total execution and iteration budgets. A nested graph
may impose tighter per-invocation budgets, including runtime and cost limits.

## Limits, review and debugging

The default global budget is 120 seconds, 1000 node attempts and 100 loop
iterations. Nodes default to a 60-second timeout and zero retries. Set `retries`
from 0 to 5; all attempts share the node deadline, parent deadlines and global
budgets. A timed-out node does not receive a fresh deadline on retry.

Monetary model cost is not provided by the current provider interfaces. A model
node with an applicable `cost_usd` budget fails with `unknown_model_cost` before
inference. Runs without model calls report `cost_accounting: "no_model_calls"`;
model runs without a required cost budget preserve reported token usage and
`cost_usd: null`. This is not an estimate of local hardware or electricity cost.

`GraphControl(step=True)` or graph `debug.step` pauses before the first node.
`command("step")` permits one node; `command("continue")` runs until the next
review; `command("pause")` pauses at the next node boundary. Shared runtime and
parent loop deadlines continue while paused. A review waits for
`command("review", node_id=..., approved=True/False)` for the exact pending node.
Nested IDs look like `search[0]/review`. Application adapters must restrict review
decisions to the human owner; the engine never supplies a decision itself.

Updates contain node statuses, resolved inputs/outputs, attempts, error logs,
bounded live child logs, active run/session IDs, elapsed time, debug state and the
pending review. Reports and node evidence remain in the job record. Every new
execution has `cached: false`; inspecting a previous job does not rerun it.

Cancellation and deadlines cancel the registered child run. EDA process groups
are drained before the graph returns. Model cancellation uses the existing agent
session cancellation: native processes are stopped, while an in-flight API
transport may drain until its configured provider timeout. Its active slot stays
reserved during that drain.

Graph nodes execute serially. Ordinary nodes preserve design sources; the explicit
`source_patch` node below can apply an exactly reviewed replacement. The engine compares
accepted source hashes before each node, before EDA launch, and after execution. Concurrent
source edits terminate execution with `source_conflict`; the human edits remain
intact. A changed source during a child run is also reflected by run staleness.

## Reviewed source replacement

`source_patch` prepares one existing-file replacement and waits at the existing
human review checkpoint. Connect a `project` node's `sha256` to its
`expected_sha256` input and proposed text to `content`. Its destination is fixed
in configuration; an imported graph or model reply cannot approve the write.
Ordinary model sessions remain read-only. Source patch retries are forbidden.

`file.change.preview` is a reusable read-only operation with parameters
`{project_id, path, content, expected_sha256?}`. It returns `current_content`,
`candidate_content`, `current_sha256`, `candidate_sha256`, `diff`, `path`,
`project_version` and `proposal_sha256`. The complete unified diff marks missing
trailing newlines. Preparing a preview creates no revision and grants no write
authority. Existing agent write approvals use the same preview helper.
Preview consumers pin the returned `project_version` to the inspected text;
the outer response version can reflect a later native edit. Preview preparation
rechecks the file after version capture, and both agent write paths revalidate
the private file identity and exact frozen preview before invoking the writer.
The existing `file.read` operation also returns `source_version` tied to its
checked `content`, preserving its 2 MiB limit and ordinary readable-file policy.
It checks full project versions and regular-file identities, including change
time, before and after the original editor read. Concurrent content or identity
changes fail with `VERSION_CONFLICT`. Consumers of larger files pin this
`source_version`, rather than a response-envelope version computed later. These
optimistic checks coordinate registered writers and detect races during the
read; they do not claim filesystem atomicity against native writes after the
last check.

Both source texts must fit 64,000 UTF-8 bytes and the complete proposal must fit
256 KiB. Every graph, including all nested loop iterations, shares a maximum of
20 proposals and 2 MiB of proposal evidence. Oversized proposals fail instead of
silently truncating the review. Missing files, nontext files, hidden paths,
symbolic links, traversal/noncanonical paths, project configuration, known
credential file names, key files and JSON credential fields are rejected before
preview. This node creates no new files and performs no multi-file transaction.

The pending review includes a fresh `review_token` bound to the project, job,
full node ID, attempt, proposal hash and project version. Submit it to
`job.control` with `action:"review"`, `node_id`, and explicit `approved:true` or
`false`. Source patch decisions require the human owner's `approve`, `write` and
`execute` permissions. A token can be consumed once; stepping or continuing
cannot grant approval. Historical jobs never expose live review buttons.

After approval, the shared project lock guards cancellation/deadline checks,
the full accepted source map, original file identity and frozen project version.
The engine invokes the existing versioned `file.write` once, preserving the old
file revision. It verifies exactly that replacement before advancing its accepted
source generation. Outputs retain before/after hashes, the exact proposal and
the operation receipt, including trace/version and recoverable `revision_id`.
Job `source_generations` retain each accepted source map; nodes and subsequent
EDA run metadata identify their generation. Original EDA snapshots remain
immutable and earlier runs become stale when sources advance. Compare those
historical generations explicitly rather than treating earlier measurements as
measurements of the final source.

Rejection is a failed node with `approved:false, applied:false`; existing failure
edges can report it. Cancellation or timeout before the commit check writes
nothing. A cancellation after the commit point preserves the applied change and
its evidence. If the write or post-write evidence cannot be verified, execution
stops with `patch_outcome_unknown` or `patch_post_commit_failure`; inspect the
receipt, source and revision history before any new request. These outcomes
never replay the write or imply rollback.

## Runnable examples and evidence

The JSON files in `examples/workflows/` are importable workflow definitions:

1. `simulation-diagnosis.json` runs the selected saved testbench and routes failed
   execution to a report containing the actual simulator log. A successful
   simulation follows a separate branch.
2. `parameter-search.json` reads `specifications.json`, tries RD values from
   1000 to 10000 ohms in 250-ohm increments on the educational amplifier, and
   stops when the real measured metric meets the saved target/tolerance. It
   preserves every run and does not edit the saved deck.
3. `pre-post-comparison.json` runs `testbenches/pre.cir` and
   `testbenches/post.cir` in the provisioned `examples/ota5` reference project.
   It reports both measured metric objects and their distinct run IDs.
4. `verification-analysis.json` runs real DRC on `layout/ota5.mag` and LVS on
   `netlists/extracted-lvs.spice` versus `netlists/schematic.spice`, cell `ota5`.
   The final report preserves actual statuses and detailed tool logs.
5. `reviewed-source-patch.json` proposes RD=2200 in the educational amplifier,
   waits for explicit diff acceptance, saves one recoverable revision, and runs
   ngspice against the accepted source snapshot.

The last two examples require the OTA project's prepared SKY130 tools/models and
generated netlists. Provision that project with `scripts/validate_ota5.py`; a
missing PDK, tool or design input remains an explicit failure/unavailable record.
These examples use deterministic evidence analysis and do not silently call a
paid model. A user may add a configured `model` node to interpret their report.

Verification command (local HTTP fixture requires localhost socket access):

```sh
python3 -m unittest tests.test_workflow_graph tests.test_workflows tests.test_service -v
```

Tests exercise real ngspice parameter overrides, convergence, failed-deck
diagnosis, process cancellation/timeouts, typed graphs, branch skipping, shared
budgets, retries, source conflicts, review/stepping, and a local provider fixture.

`tests/browser-graph-controls.cjs` additionally tests keyboard stepping, explicit
human review, backend acknowledgement, and historical inspection against a
temporary real workspace/server. It requires the existing Playwright installation.
`tests/browser-workflow-editor.cjs` exercises the main editor: v2 construction,
mapping/retry/output/conditional-edge editing, saving, actual ngspice execution,
skipped-branch evidence, history and returning to the v1 tuning controls.
`tests/browser-source-patch.cjs` verifies the real token-bound review handshake
and source generation. `tests/test_file_changes.py` and
`tests/test_workflow_source_patch.py` cover complete previews, stale review,
rejection, cancellation, deadlines, loop limits, permission denial and uncertain
write outcomes. The registered real fixture is repeatable with:

```sh
python3 scripts/validate_source_patch.py --output /tmp/af-reviewed-patch-new \
  --ngspice /absolute/path/to/ngspice
```

On 2026-10-09 the isolated fixture changed RD from 3300 to 2200 ohms and measured
`vout_mean` from 2.43467 V to 2.71997 V. It preserved the original revision,
verified both actual run source hashes and confirmed the first run was stale
after the accepted replacement. Evidence is retained under
`.validation/reviewed-source-patch-20261009-stable-read/evidence.json`; this scripted test-owner
decision is an integration fixture, not live-model acceptance.

Actual reference acceptance on 2026-10-08 ran the verification and comparison
examples in the isolated `.validation/ota5-reference` workspace, project
`4ba0c48a913c42a3b5391f356617b74f`:

| Evidence | Actual run |
|---|---|
| DRC pass | `5090514092244f179ca4fab01e6ae4bd` |
| LVS pass | `b0826205e7d34008a8a25dcf96ad67be` |
| Pre-layout: gain 43.3676 dB, unity gain 3,731,160 Hz | `c77f93f7d88e452f9e84de6b15718661` |
| Post-layout: gain 43.3681 dB, unity gain 3,689,560 Hz | `fa4d9f631807405fadc52df95e8dfd5e` |

These records are local execution evidence, not bundled fabrication signoff.
Regressions also require failed or unknown DRC/LVS reports to produce
`success: false` even when the tool process exits zero.
