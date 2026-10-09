# Full product completion ledger

Started 2026-10-08. Authority: `analog-factory-project-prompt-en.md`, the user's
unified-operation requirements, and the 2026-10-08 gap audit. The user authorized
continuing implementation until all requirements are met. This ledger tracks the
whole objective; completing an individual batch does not complete the goal.

## Architecture and execution decisions

Keep the Python standard-library modular service, vanilla browser controllers,
existing EDA adapters, and registry as the policy/execution boundary. Add focused
modules and catalogs rather than replacing existing managers. UI, API, CLI, native
MCP, workflow and training calls must share registered operations. Preserve existing
v1 projects/workflows and saved sessions; use new versioned schemas where semantics
change. Native design files remain source files, results remain actual run evidence.

Alternatives considered: replacing the server/UI would increase migration risk;
adding parallel agent-only services would violate the operation contract. Selected:
incremental service extensions with adapters and compatibility tests. Optional
training dependencies run in an isolated environment and never become requirements
for manual design. No external paid inference, publishing, or user project deletion
is necessary to implement and test features; live integrations need separately
identified actual evidence before acceptance.

## Requirements and authoritative acceptance

| ID | Deliverable | Current baseline / remaining work | Required evidence |
|---|---|---|---|
| C01 | Complete project/file lifecycle and backup restoration | Create/edit/export/history exist; rename, move, trash/restore, bounded archive import and full backup missing | Real round trip, conflicts, active-job rejection, traversal/symlink/bomb rejection, UI and agent parity |
| C02 | Complete manual reference design | Tool-level SKY130 checks exist; complete reference schematic/layout/pre/post circuit acceptance missing | Saved design inputs, actual DRC/LVS reports including negative controls, extraction provenance, pre/post metrics and reproducible command |
| C03 | Graphical advanced simulation and measurements | Five basic analyses exist; sweeps/PVT/qualified Monte Carlo and metric configuration missing | Real ngspice batch results with conditions/measurement definitions, invalid/unsupported model rejection, UI invocation |
| C04 | Full waveform analysis | Raw complex/multiple plots exist; plot selection, phase/components/dB/log axes missing | Deterministic complex response tests, browser transformations/cursors/CSV and shared UI commands |
| C05 | General visual execution | v1 six-node tuning works; general typed graph, branch/bounded loop/retry/debug/review/model/tool nodes missing | v1 compatibility; v2 real simulations, branch/loop/cancel/timeout/error/review tests; four runnable example agents and browser execution |
| C06 | Provider/agent experience and configuration | Native effort/MCP approvals implemented; API streaming/images/capability controls, richer provider parameters, scoped automatic mode and terminal fallback incomplete | Protocol fixtures and actual supported-backend acceptance; no false capabilities; approval/secret/concurrency tests; UI context and rollback |
| C07 | General design evaluation environment | Single RD random-search baseline exists | Isolated configurable task sets, fixed independent scoring, constraints/PVT/cost separation, replay and held-out comparisons |
| C08 | Actual trainable framework integration | No model-weight training implemented | Researched current framework choice; actual parameter updates, trajectories, saved/reloaded checkpoints, training curves and held-out before/after vs baseline; explicit resource availability |
| C09 | Durable operation/task behavior | Records durable; events/idempotency/browser leases are process local | Restart-safe dedup/events with explicit limits; interrupted-task/recovery semantics; source/version conflicts including legacy paths |
| C10 | Deployment and reproducibility | Ubuntu 26.04 host installation exists; primary 24.04 acceptance pending | Reproducible isolated install, version pins, dependency closure, clean-environment tests, bounded artifact streaming and diagnostics |
| C11 | Full operation contract and coverage | 117 operations / 40 adapters at baseline | Every new action registered with schema/permissions/errors/real handler; regenerated reference/inventory; UI/MCP/CLI parity, acknowledgement, negative tests and CI |
| C12 | Documentation and final acceptance | Several phase/status documents stale | Reconciled status, examples and architecture; requirement-by-requirement evidence audit; no placeholders treated as completion |

## First implementation batch

1. C01: extend `projects.py` behind a focused lifecycle service/catalog. Reversible
   trash instead of permanent UI deletion; refuse active work, require revisions,
   validate imports before publication. New UI controls call the registry. Tests
   own temporary workspaces; never delete the user's existing projects.
2. C05: add `workflow_graph.py` with schema-version 2 validation and independent
   execution through the registry; preserve v1 tuning. Explicit typed ports,
   bounded control flow, node records, cancel/deadline/retry semantics. Integration
   with catalog/service/UI follows real engine tests, not placeholder nodes.
3. C02: create an isolated SKY130 OTA reference and validation scripts under
   `examples/ota5/` and validation output outside the user's project. Establish
   measured >30 dB gain, then schematic/layout/DRC/LVS/PEX/post-layout evidence.
4. C04: add pure waveform transformations with tests, expose explicit plot/view
   commands, then connect existing graph rendering/cursor/CSV handlers. Preserve
   untouched raw data and existing default behavior.

Subsequent batches implement C03/C06, C07/C08, C09/C10 and final C11/C12 audits.
The order follows manual design > reliable data > assistance > orchestration > RL.

## Review focus

- Import collisions, ZIP traversal/symlinks/decompression bombs, hidden credentials,
  and active runs must fail without partial publication or source loss (C01).
- UI changes made while approval or asynchronous reads are pending must reject
  stale agent writes and preserve human drafts (C01/C04/C05/C11).
- Graph branches must not execute skipped nodes; bounded loops/retries must consume
  the same deadline/budget, and cancellation must stop actual child tools (C05).
- Complex phase unwrap, zero magnitude, log-axis nonpositive values, multi-plot
  selection, cursor/export units must stay mathematically consistent (C04).
- Missing PDK/model/training resources, failed tools, unavailable costs and unknown
  verification reports must remain failures/unknown, never synthetic passes (all).

## Verification record

- Baseline registry check: 117 operations, 40 routes, generated schemas and adapter
  boundaries passed on 2026-10-08. Previous full test evidence is 271 tests on
  2026-10-01, not a new full-suite run for this batch.
- 2026-10-08 current implementation evidence (this supplements the baseline table;
  the whole product is still in progress):
  - C01: project rename/trash/restore, file move/trash/restore, bounded ZIP import
    and backup are registered and connected to the UI. Focused Python regressions
    passed; `tests/browser-lifecycle.cjs` passed the real file-picker round trip,
    history retention, stale-draft rejection and acknowledged remote UI commands.
    Imported execution evidence remains inert and cannot gain process/retry rights.
  - C02: `scripts/validate_ota5.py` passed actual SKY130 Xschem/netlisting, Magic DRC,
    extraction, Netgen LVS and pre/post simulation, including negative DRC/LVS
    controls. Measured differential gain: 43.3676 dB before, 43.3681 dB after
    capacitance extraction. Still needs a user-facing reference template and
    clean Ubuntu 24.04 replay; no distributed-RC or foundry signoff claim.
  - C03: batch engine and graphical configuration are implemented and registered;
    browser acceptance is in progress. Installed SKY130 `tt_mm` passed actual
    matched-device qualification and two seeded mismatch samples. Its `mc`
    section failed global-device-correlation qualification and remains disabled
    for that statistical interpretation; engineering perturbations stay labelled.
  - C04: `node tests/waveform-view.test.cjs` passed eight transformation contracts;
    `tests/browser-waveform-analysis.cjs` passed multiple plots, phase/components,
    trace ratio, dB/log axes, CSV and remote acknowledged display changes.
  - C05: v2 typed graph engine, editor and four JSON examples execute; v1 remains
    supported. Actual simulation, pre/post OTA comparison, DRC/LVS and diagnostic
    graphs passed. Main-browser editor and standalone debug/review tests passed.
    YAML interchange remains to be implemented and checked.
  - C06: provider streaming/images/capability declarations, scope-limited execution
    and native terminal fallback are being built; this row is not accepted.
  - C07/C08: isolated CPU TRL/Qwen pilot performed two optimizer steps, saved LoRA
    weights and reloaded them in a separate process. Adapter update L2 was
    0.0164437335 over 540672 trainable parameters; peak RSS about 3.44 GB, 264 s.
    Two-task held-out before/after both scored 0/2, so no improvement is claimed.
    Full held-out comparison, UI/replay acceptance and independent review pending.
  - C09: private SQLite journal and durable deduplication/events implemented.
    Fifteen new regressions plus operation/HTTP integration checks passed,
    including a crash after a real fsynced side effect: replay is OUTCOME_UNKNOWN,
    never duplicate execution. Independent review remains open. Browser leases
    are deliberately ephemeral; one serving process per workspace is required.
  - C10: isolated Ubuntu 24.04 deployment/replay is being prepared. Current host
    evidence alone does not establish Ubuntu 24.04 acceptance.
  - C11: generation check now validates 159 operations / 40 compatibility routes.
    Counts will change as remaining modules land. CI expansion and final full
    regression/coverage audit remain open.
  - C12: this ledger is current; phase/status documents still require reconciliation.
- Interim full-suite run: 361 tests, three failures, one skip. Two failures were
  training contracts while that module was being implemented; the third was font
  coverage, subsequently regenerated. This is not a full-suite pass. Run the full
  suite again after shared module integration stabilizes.

- 2026-10-08 final integration batch (supersedes pending implementation items above):
  - C01/C10: bounded disk-backed transfers, 68 MiB training checkpoint import/backup,
    explicit inert imported model/optimizer state, 5 MiB browser File.slice upload
    and streamed download passed. 101 focused contracts and seven actual HTTP tests
    passed. Original text-editor limits are retained.
  - C02: packaged OTA template, shared design defaults and registered publication
    of actual Xschem/Magic output passed full CLI and browser flow. Final extraction
    flattens hierarchy and retains top-level ports, fixing Magic 8.3.684's omission
    of legacy-tech substrate capacitance. Both old/new Magic generic fixtures pass.
    Actual final reference: 28 capacitors, 43.3676/43.3681 dB pre/post gain,
    3.73116/3.69110 MHz unity crossing, clean and negative DRC/LVS controls. Evidence
    in `examples/ota5/evidence/template`, with fresh records under `.validation/ota5-template-cli`.
  - C03/C04: explicit loop-gain phase-margin control passed actual two-pole analytic
    comparison (~19.8266°), wrong-polarity/low-gain/no-crossing failures and browser
    use. Full-sample statistic queries use bounded streamed accumulation; a >50k
    actual ngspice graph mean matches independent full-raw math. Rendering no longer
    applies a second stride that discarded retained narrow extrema.
  - C05: strict optional YAML import/export and real browser graph execution passed;
    61 related tests passed. v1 compatibility and four v2 EDA examples remain.
  - C06: actual local SSE/NDJSON, tools/images, bounded retries/concurrency, capability
    and generation controls, project overrides, finite private human scopes, and
    human-only native PTY are implemented and browser/protocol tested. Project EDA
    overrides have UI/agent parity and refresh availability. Independent review
    found stale form revisions on preference rerender and capability inheritance
    across model/endpoint overrides; both fixed and reproduced as safe regressions.
  - C07/C08: full held-out comparison completed all32 tasks: base1/32, trained1/32,
    random17/32; mean rewards -0.82917/-0.81442/0.20229. No success improvement.
    A second configurable current-mirror pilot performed actual weight updates,
    checkpoint reload, replay and cancellation. Exact original amplifier pilot
    requalification reproduced the same adapter hash. Training UI and remote
    acknowledgement passed after canonical state redaction was aligned.
  - C09: independent journal audit and exception/crash/unknown-outcome cases passed.
    Replay remains bounded and private; approval/scope and volatile-body exclusions
    remain enforced on restart/retry.
  - C10: pinned Ubuntu24 image built; first frozen suite425 had9errors+2failures+3skips,
    while real OTA and native Xschem/Magic lifecycle passed. Nine errors were old
    protocol fixture signatures; failures were font coverage and legacy Magic
    capacitance. Fixes landed; final frozen replay remains necessary. PyYAML is
    included in the pinned apt closure. The image receives no user credentials.
  - C11/C12: new-controller classification, literal operation IDs/action namespaces,
    Python engine boundary checks and generated handler ownership now enforced.
    Independent full-spec audit identified phase margin, project EDA settings and
    DRC location navigation as concrete gaps; all three now pass browser acceptance.
    Native DRC focus passed real Magic/SKY130/Xvfb with distinct acknowledged boxes
    for two error indices. Final gates are recorded below.

## Final acceptance — 2026-10-08

The integrated implementation gate is complete. These results supersede the open
items in the historical progress entries; the documented operational/research
limits are retained.

- Generated inventory, interface schemas and boundary checks: **223 operations,
  40 compatibility routes**, clean. Final discovery uses byte-bounded structured
  pages with complete traversal, stale-cursor, context and Unicode regressions.
- Host full suite: **483 tests in 94.922 seconds, OK (3 opt-in skips)**. Record:
  `.validation/final-acceptance/host-python.log`.
- Frozen Ubuntu 24.04 full suite: **482 tests in 111.078 seconds, OK (4 skips)**.
  All Node/registry checks, actual OTA DRC/LVS/PEX/pre/post and negative controls,
  and actual Xschem/Magic start/cancellation passed. Record:
  `.validation/ubuntu24/replay-e51e6l14/ubuntu24-acceptance.json`. Source manifest
  matches final implementation/tests/scripts/deployment bytes.
- All 16 main Chromium suites passed, plus the effective-project-configuration and
  DRC-navigation regressions. Records: `.validation/final-browser/results.json`
  and `.validation/final-acceptance/drc-browser.log`. Extension parity exercises
  one newly registered real handler from both UI and agent interfaces.
- Final independent review found and verified fixes for OTA design metadata lost
  on import and DRC index draft visibility. Backup, legacy export and disk export
  now preserve validated source roles/specifications; missing derived files remain
  prerequisites and imported run evidence cannot authorize local publication.
  Manual, semantic and remote acknowledged DRC selection agree without launching
  a process until focus is requested.
- Generic skips are the explicit real OTA/DRC/statistical qualification checks,
  whose actual evidence is recorded separately; the offline image also lacks the
  native Codex CLI fixture. Font coverage includes all 613 authored CJK characters.
  `git diff --check` passes. Remote CI was configured but not triggered.

At that October 8 gate, no live cloud design acceptance, GPU-framework integration, distributed-RC/foundry
signoff, arbitrary third-party GUI semantics or learned success improvement is
claimed. The full held-out base and trained policies each passed 1/32 tasks; these
are measured limits of the completed initial workflow, not synthetic success.

## Continued October 9 development

The corrected fixed snapshot in `.validation/batch-corrected-u__4d5n4/` passed
552 host and 551 Ubuntu tests (four skips each), actual OTA/native lifecycle,
release archives and isolated installed-service verification. The first freeze
had a characterization effective-model validation regression; its failed record
is retained and the two-file correction is identified by a separate source manifest.
Nineteen browser modules passed on the first snapshot, and characterization was
rerun after the source correction. See [status](status.md) for paths and scope.

Actual bounded live Codex five-MOS OTA design now has independent AC/DC electrical
evidence (42.7802 dB). Characterization nominal/PVT and experimental global-model
correlation have their own real-tool reports. None proves all backend/model cases,
statistical yield or learned design improvement.

The later source_patch graph node, checked file reads, transactional browser draft
variants and managed native semantic controls are new work beyond that snapshot.
Current metadata validates 256 operations. Their focused/actual evidence and
remaining final integration gates are tracked in the
[current plan](superpowers/plans/2026-10-09-project-improvements.md).

The D/E/F gate subsequently passed on
`.validation/integration-def-corrected-hr43dbmu/`: 613 host tests, 612 Ubuntu tests
(five skips each), all 23 browser suites, installed packages, actual OTA and native
geometry/save checks. Actual native-browser lifecycle evidence is
`.validation/native-browser-final-lifecycle-20261009/`, with all product files
matched against the integrated snapshot. Preserve the earlier failed/invalid
`.validation/integration-def-final-ykt1pzr4/` records; their browser attempt used
a preexisting test port and is explicitly disqualified. The new browser driver
checks owned PID/source/workspace identity on an OS-assigned port.

Remaining project goals include full third-party native editing coverage and
training effectiveness. A concrete next training diagnosis is recorded in
[training-feedback-budget.md](training-feedback-budget.md); no new electrical
success improvement is claimed by this delivery gate.

The final native draft-only correction also passed a complete gate at
`.validation/integration-def-drafts-wzq5e7vj/`: 614 host / 613 Ubuntu tests
(five skips each), all 23 browser suites, actual native-browser parity, package
installation and actual OTA/native tools. Both browser drivers report the same
353-file source manifest; parameters can be prepared manually or remotely without
changing native geometry until the explicit action. The subsequent training budget
change is separate work and does not inherit this full-suite claim.

Training completion-budget work then passed 623 host tests (five skips), its actual
acknowledged browser/Node contracts and installed packages on
`.validation/training-budget-verified-_ggz03y4/`. The option, request/trace/report
identity and strict resume guards share the existing registry. Actual scripted
TRL plus ngspice confirms96 drops feedback while768 retains three rounds within
six simulator calls; no model weights or learning claims are involved. A new-budget
paired held-out model experiment remains open.
