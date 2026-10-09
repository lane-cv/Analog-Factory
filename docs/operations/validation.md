# Operation validation and remaining boundaries

Updated 2026-10-09. [Delivery status](../status.md) and the
[completion ledger](../completion-plan.md) own acceptance. The October 9 D/E/F
fixed snapshot passed 614 host and 613 Ubuntu tests (five skips each), all 23 browser
suites, actual EDA/managed native checks and installed-package validation. Separate
actual native-browser evidence used the same frozen source manifest. Older test counts are
historical, not a claim that the current tree passed a full regression.
The subsequent training-budget freeze passed 623 host tests (five skips), focused
actual browser parity, Node controller tests and installed-package validation.
Its actual scripted TRL/ngspice feedback gate is separate from model-learning
acceptance; exact manifests and boundaries are in [status](../status.md).

## Delivered surface

The generated [reference](reference.md), [backend inventory](inventory-backend.md),
[frontend inventory](inventory-ui.md), and `boundary-inventory.json` enumerate the
current registry, schema/handler ownership and 40 legacy route adapters. Final
generation validates 256 operations; counts and references are checked in CI.

UI, HTTP, CLI, MCP, API assistants, native CLI bridges, visual workflows and training
tools share operation validation, policy and execution. Frontend session commands
have a specific target and require real browser acknowledgement. Queries expose
project/configuration/tool availability, drafts, selections, tasks and artifacts.
Missing tools/PDKs, disconnected sessions, invalid arguments and execution failures
remain explicit results, not successful placeholders.

Project lifecycle, typed visual graphs, simulation/PVT/statistical qualification,
streaming provider capabilities, bounded automatic execution, human-only native
PTY, actual CPU weight training, durable receipts and disk-backed artifact transfer
are implemented. Each module has focused tests and documented runtime limits.

## Actual evidence

- Reviewed graph source replacements preserve old/new source generations, revisions
  and actual ngspice results. Preview/read results bind content to a checked version;
  approval identity is rechecked under the existing writer lock.
- Browser recovery tests use actual IndexedDB transactions and same-profile tabs,
  including old-storage migration, quota abort, typing during save read-back and
  project/file selection races. Drafts are not cleared on uncertain save outcomes.
- Managed native Xschem/Magic commands have actual private-Xvfb evidence for instance
  geometry, named pin connectivity, Magic paint and fresh DRC, nonce/export isolation
  and slow-save receipts. Final actual browser parity passed after target-switch,
  Escape and confirmed stop/restart corrections; the integrated gate also passed.

- External-agent HTTP integration reads/edits a scoped project, launches actual
  ngspice, deduplicates retry requests and reads task status, metrics/logs/waveforms.
- Browser operation tests verify target effects, disconnected/error behavior,
  two-client conflicts, preserved human drafts, scope and secret redaction.
  The extension test registers one real source-count operation and invokes it
  through a UI button and agent HTTP without separate business code.
- Lifecycle browser tests cover rename/move/history/trash/restore and ZIP file-picker
  round trips. Transfer tests stream a 68 MiB checkpoint backup/import and reject
  traversal, quotas, stale artifacts, wrong callers and executable imported state.
- Actual [OTA reference](../ota5-reference.md) creates the five-MOS template, invokes
  Xschem/Magic/Netgen/ngspice, publishes actual netlists, and measures >43 dB pre/post
  gain. Clean DRC/LVS and intentional-error negative controls both pass acceptance.
- v2 graph tests execute real simulation/verification, branch/loop/retry/review/debug
  controls and cancellation. YAML tests enforce safe bounded parsing, round-trip
  real browser edits and reject aliases/duplicate keys/custom tags.
- Waveform browser/math tests cover complex phase/dB/ratios, log axes, plots,
  cursors, CSV and retained narrow peaks in bounded display data. Exact statistical
  queries consume all original raw samples instead of the display reduction.
- Provider protocol fixtures exercise actual local SSE/NDJSON/tool/image transport,
  retries, cancellation and capability restrictions. Browser tests cover explicit
  generation controls, project overrides, selected images and private human-issued
  finite automatic scopes. Native PTY tests exercise a real process, ANSI output,
  keyboard/approval wait, resize, detach/reattach and stop without persisting bytes.
- [Training evidence](../training.md) includes actual CPU LoRA optimizer updates,
  independent checkpoint reload/replay, configured tasksets, cancellation, curves
  and all 32 held-out tasks. Base and trained policies each passed 1/32; random
  search passed 17/32. No learned design improvement is claimed.
- Durable-journal crash tests fsync a real effect, interrupt the request and replay
  as `OUTCOME_UNKNOWN` without duplicate execution. Permissions/scopes are checked
  again on replay; unsafe response content is not persisted.
- The native Codex 0.159.2 regression uses the actual CLI and local Responses/SSE
  fixture to exercise MCP approval. It reproduced `approval policy is never`
  before the fix and then discovered and wrote through the bridge after workspace
  approval. It does not claim a live paid-provider design run.

Final acceptance passed on 2026-10-08: host Python 483 tests (3 opt-in skips), frozen
Ubuntu 24.04 Python 482 tests (4 skips), mathematical/provider/frontend-state Node
checks, 223-operation registry generation, all 16 main Chromium suites and the
additional project-configuration and DRC-selection browser regressions. The frozen
replay also passed actual OTA/negative controls and native GUI start/cancellation.
See [deployment acceptance](../deployment-ubuntu24.md) and [status](../status.md)
for reproducible evidence and skip boundaries. Earlier failing 361/425/475-test
runs are historical. CI definitions are present; a remote CI run has not been
invoked in this session.

The final corrections are covered explicitly: discovery remains structured under
the agent byte limit as the catalog grows; backup and both export formats preserve
validated OTA design defaults without importing execution authority; DRC index
selection is observable through the same acknowledged UI command before any native
window is started.

## Limits that tests do not remove

- One serving owner process per workspace is required. Legacy local-owner routes
  are trusted; external bearer scopes do not turn the app into a public multi-user
  service. Old trusted callers omitting `If-Match` retain compatibility behavior.
- External/native editors do not acquire application revision locks. A fingerprint
  cannot make their filesystem writes atomic with the last validation/write step.
- Browser leases are ephemeral. Missing acknowledgement means unknown outcome,
  not success or rollback. Durable journal limits are 1,000 completed responses
  for 30 days, 2,000 events, 10,000 task traces and 10,000 claims; unresolved claims
  remain pinned. Volatile PTY/artifact bodies are excluded from durable replies.
- EDA tools without genuine progress report `progress: null`. Cancellation stops
  managed processes; native windows and human PTY sessions retain their documented
  ownership boundary. Basic authenticated Codex/Claude connectivity is historical
  evidence, separate from full live provider/tool design acceptance.
- GPU frameworks, unqualified statistical interpretations, distributed-RC/foundry
  signoff and semantic access to every embedded third-party canvas/menu are not
  fabricated capabilities. Readiness reports their actual prerequisites.
- Static checks validate metadata, generated ownership and declared application
  boundaries using lexical/AST inspection. They cannot prove arbitrary dynamic
  DOM logic or third-party behavior. Unknown new controllers fail classification;
  integration tests and the operation inventory must demonstrate actual effects.
