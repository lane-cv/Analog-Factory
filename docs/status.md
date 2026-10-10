# Delivery status

Updated 2026-10-10. The latest additions are actionable design-stage entry,
native prelaunch rejection recovery and snapshot-derived ordinary measurement
evidence, following first-user environment checks and characterization history.
Portable Ubuntu installation, owned desktop/service supervision and
publication checks remain available. Codex/Claude configuration
and official release tracking retain their separate network limitation below. Earlier source-patch, recovery, native and training gates retain
their historical scope. Documentation is reconciled after verification.
Continued project development and the scope limits below remain open.
The detailed evidence ledger is [completion-plan.md](completion-plan.md), and
remaining work is tracked in the [current plan](superpowers/plans/2026-10-09-project-improvements.md).
Provider fixtures, CPU training and local physical checks have distinct scopes.

| Area | Implemented and actually exercised | Remaining acceptance or scope limits |
|---|---|---|
| Unified operations | 274 registered UI/HTTP/CLI/MCP/assistant/graph/training/native operations, 42 compatibility routes, schemas, permissions, confirmations, revisions, cancellation, bounded paginated discovery and acknowledged target commands. Browser and owned native targets share registry policy; generated metadata and adapter-boundary checks pass | Static checks cannot prove arbitrary dynamic or third-party behavior; critical integration and final regression remain separate gates |
| Projects/artifacts | Create/open/edit/history, rename/move, trash/restore, bounded ZIP backup/import. Real browser and 68 MiB checkpoint round trips; chunked uploads/downloads; OTA design defaults preserved through all three archive formats; imported execution authority stays inert | Editor text remains bounded; archives/artifacts have explicit disk quotas and ownership/expiry limits |
| Manual EDA | Packaged five-MOS SKY130 template and full browser path: Xschem→publish→Magic DRC/PEX→publish→Netgen LVS→pre/post ngspice. Gain 43.3676/43.3681 dB; 28 extracted capacitors; DRC/LVS positive and negative controls. Actual Magic error-location acknowledgement under private Xvfb | DRC browser and distinct indexed error boxes passed; local capacitive extraction is not distributed-RC, density/antenna or foundry signoff |
| Simulation/measurements | Graphical parameter/temperature/supply/corner batches and actual gain/bandwidth/phase/power/swing. New OTA characterization has actual nominal/PVT evidence and corrected transitive validation of project and configured-model inputs. Explicit loop-gain phase margin tested against analytic two-pole result (~19.8266°). Actual SKY130 mismatch seed qualification and experimental cached-global-draw qualification | Original installed mc still fails global correlation. Experimental semantics have bounded correlation/reproducibility evidence, not calibrated distributions or yield |
| Waveforms | Complex/multiple plots, phase/dB/ratios, log axes, zoom/cursors/CSV. Streamed bounded display preserves narrow extrema; original raw downloads and exact full-sample statistics; >50k-sample ngspice graph mean verified independently | Display reduction may omit intermediate phase/ratio extrema; labelled sampled CSV is not full-resolution evidence |
| Assistant/configuration | Codex effort/model choices, streaming API tools/images, capability parameters, project overrides, finite private human scopes and real human-only PTY. Actual Codex five-MOS OTA design and independent AC/DC verification passed, gain 42.7802 dB. Project tool/PDK/provider settings preserve stale drafts | One bounded live design case does not qualify every backend/model or layout/PVT design. Compact status reduces response bytes; actual token savings have not yet been measured in another live model trial |
| Visual agents | Typed v2 graphs with branches/bounded loops/retries/deadlines/review/debug/model/tool nodes; v1 compatibility; JSON and safe optional YAML. New source_patch node shares exact diff preview, human approval and file.write; actual resistor replacement followed by ngspice passed with preserved history and source generations | Patch scope is one existing bounded text file per reviewed node. New-file/multifile transactions and a model-driven OTA graph acceptance remain open; the broader design graph remains a separate acceptance target |
| Editor reliability | Checked content/source versions, save read-back, three-way conflict review and bounded page-owned draft variants in transactional IndexedDB. Actual same-profile tabs, reload, typing/selection races, old-storage migration and quota abort tests passed | Drafts remain browser-local. Unknown save outcomes require inspection, never automatic retry. Native writes outside the registry do not acquire application locks |
| Managed native canvas | Seven actual native operations and eleven browser commands, fixed Tcl handlers, private session authority, real Xschem instance moves/pin wiring and flat-cell Magic box paint. Actual native and initial full-browser parity/save/disconnection evidence exists | Actual browser parity, target-switch/Escape races and confirmed stop/restart are verified. No unmanaged-window takeover, arbitrary Tcl, complete third-party menu coverage, embedded Xschem symbols or Magic hierarchical editing |
| Evaluation/training | Isolated amplifier/current-mirror tasksets, independent constraints/rewards/costs, actual CPU TRL GRPO LoRA updates, saved/reloaded checkpoints, cancellation, token traces, replay, UI and all 32 held-out tasks | Base and trained success both 1/32; random baseline 17/32. **No learned design improvement.** Additional GPU frameworks remain unintegrated/unavailable |
| Durable execution | Private SQLite receipts/events, bounded replay, scoped retries and fsynced crash test producing OUTCOME_UNKNOWN without duplicate effects | One owner process per workspace. Browser leases/private live capabilities expire on restart; ambiguous claims need inspection, not blind retry |
| Deployment | Pinned Ubuntu 24.04 base/apt closure/Magic/Xschem and offline replay passed for the corrected October 9 characterization/agent/statistics snapshot. Release validator builds sdist/wheel, checks assets/private paths and tests the installed CLI/HTTP service outside the checkout | Source-patch/editor/native additions passed the corrected full regression, browser, package and Ubuntu gates below. No remote CI run has been triggered; optional provider logins and ML environment have separate acceptance |


## First-user usability and design efficiency, 2026-10-10

New-project preflight now checks workspace tools and the exact prepared OTA
assets, offers configuration or an educational starter, and preserves newer
drafts when creation responds late. The dialog stays open until the new project
has loaded. English and Chinese controls share the same operations.

Overview milestones distinguish source presence, execution completion, verified
pass, stale evidence and published outputs. OTA publication checks actual run
artifacts, deterministically derived files, native sources and environment
identity. External edits refresh the overview without navigation. This is
workflow evidence, not electrical compliance or foundry signoff.

Measurement comparison requires matching recorded units, definitions, analysis,
PVT and model/simulator identities. Incomplete older records retain their values
without speculative deltas. Characterization reopens history, retains edited
draft versions, and reports target margins, worst measured conditions and
coverage. Missing measurements remain unknown. See the [manual workflow](manual-workflow.md)
and [characterization guide](ota5-characterization.md).

The follow-up makes each overview stage resolve to its configured source or
testbench, existing execution, explicit publication entry, or missing tool
configuration. `ui.workspace.continue_design` shares the manual path and enforces
read/write/execute permissions. In-flight actions, changed prerequisites and
newer drafts reject stale navigation. Publication selects its recorded source
and requires a separate explicit publish action.

Managed native canvas version/confirmation rejections proven to occur before
launch permit an explicit project reread and a separate new start. Unknown
outcomes retain the inspection requirement, including after closing the dialog.
Ordinary ngspice comparison now collects actual snapshot definitions, model and
simulator identities, sources, analyses and logged TEMP/TNOM. The educational
amplifier's six metrics compare across design-size changes under matching
conditions. Unsupported expressions, scoped/external models and control decks
remain unqualified. Derived measurement dependencies use bounded semantic
signatures; shared model-library sections retain existing batch behavior.

Follow-up validation passed 879 Python tests with 4 environment/opt-in skips
(`.validation/usability2-python-final.log`), 14 Node suites, and the generated
274-operation/42-route contract. Wheel/sdist and isolated installed CLI/HTTP
checks passed (`.validation/usability2-package/`). The 29-scenario full browser
attempt retained 27 passes and two failures in
`.validation/usability2-browser-final/`. Those checks assumed missing DRC tools
and relied on an async predicate that the installed browser test runtime treated
as immediately truthy. The corrected tests now follow actual workspace readiness
and explicitly poll terminal run records. The final five affected/related flows
passed across `.validation/usability2-browser-accepted/` and
`.validation/usability2-navigation-accepted/`; the latter also corrected the test
helper's backend query parameters. `.validation/usability2-validation.json`
records all 29 passing scenarios and proves all 121 product files are identical
across those runs and the installed package. This is aggregated coverage, not a
relabeling of the initial full attempt. Actual comparison screenshots are in
`.validation/usability2-ui-accepted/`. These checks do not repeat the separate
real native-window or full SKY130 physical-acceptance gates. The current project
service was not restarted.

The earlier first-user pass included 859 Python tests with 4 environment skips
(`.validation/product-ux-python.log`), plus all 23 final progress-specific tests,
12 Node contract suites (`.validation/product-ux-node.log`), and the generated
273-operation/42-route contract. The final wheel and sdist were built and
installed outside the checkout (`.validation/product-ux-package-final/`).
Browser checks use disposable workspaces, actual ngspice for first use and
characterization execution, and explicitly seeded records for history reports.
The 26-suite browser run retained 23 passes and three failures in
`.validation/product-ux-browser-final/`. The failures exposed the new-project
loading window, an existing test reading before initialization, and a test
expecting implicit characterization draft rebasing. After their corrections,
all seven affected/related suites passed in
`.validation/product-ux-browser-verified/`, including all three failed cases.
The final first-user test also verifies delayed creation, external source edits,
Chinese controls and actual simulation; screenshots are in
`.validation/product-ux-ui-verified/`.
No new SKY130 PVT or manufacturing qualification is implied by the UI tests.

## Portable Ubuntu deployment, 2026-10-09

The native [deployment guide](deploy-linux.md) covers core/EDA/desktop profiles,
project-local Node and agent CLIs, optional isolated training, a foreground runner,
and explicit systemd user-service commands. First-time settings use the shared
registry; existing unknown workspaces are refused, and repeat installs preserve
user settings. No global agent login state or live workspace was changed.

Evidence at `.validation/portable-20261009/` records actual Ubuntu 24.04/Python 3.12
installation and `acceptance-final/deployment-acceptance.json` **pass**: HTTP/noVNC,
real native editor startup/cancellation, and the full OTA flow with positive and
negative DRC/LVS controls. Gain is 43.36763/43.36810 dB with 28 parasitic capacitors.
`repeat-install.log` records two successful repeat installs with identical settings
and runtime-config hashes after deliberate user edits. Node 22.23.3, Codex 0.159.2
and Claude 2.1.280 were actually installed in the isolated copy; authentication and
model inference were not invoked.

Nine Node contracts, the 268-operation/40-route registry check and isolated wheel/
sdist installation passed. The initial host regression passed 745 tests (3 skips).
The final Ubuntu 24.04/Python 3.12 regression passed **755 tests, 4 skips**,
162.290 seconds (`ubuntu-final-contracts.log`), with the installed local CLI paths.
The first container attempt lacked Git for the new publication tests; the image
dependency was corrected. Earlier missing-HOME and desktop-URL failures remain in the evidence
alongside the passing replays. Version probes now isolate HOME/cwd, and the runner
uses Linux subreaper/pidfd ownership to reap detached workers without touching
unrelated processes.

Two existing project backups were restored through registered chunked imports in
a separate workspace (`migration-restore.json`). Their private ZIPs remain outside
Git. Bounded publication scanning covers candidates, index blobs and reachable local
history and reports its limits; it is not a proof that arbitrary encoded secrets
are absent. Real systemd user-daemon control, new-device native logins and fresh
training qualification remain environment-dependent checks. The official-release
HTTP 403 limitation is unchanged.

## Codex/Claude configuration and release tracking, 2026-10-09

Settings now includes **Agent CLIs**: project paths and inheritance, installed
versions, official release notes, cached failures and workspace-only opt-in checks.
Three backend queries and nine acknowledged browser commands share the registry.
Drafts survive status events; saved changes use source/workspace revision guards.
Native login, installation, upgrades and model inference are outside this panel.
See [configuration guide and examples](agent-cli-updates.md).

The frozen source at `.validation/agent-cli-final-zl4u4561/` has 365 files and
manifest `c4727ffeccf097311bb56d250dc70b1d4dab89c5ad30d00702318aaebc92b26f`:

- **646 Python tests, OK, 5 skips**, 117.511 seconds (`host-tests.log/json`).
- All **9 Node contracts** and registry generation passed (`contracts/`).
  Current metadata has **268 operations / 40 compatibility routes**.
- Installed wheel/sdist and isolated CLI/HTTP service passed
  (`release/release-ccna3l99/release-validation.json`).
- The initial 24-browser-suite batch recorded **23 passes and 1 failure**. The
  transfer test read an old selected project before import selection completed.
  Only its wait condition was fixed. The corrected **operations → transfers**
  two-suite replay passed, including the actual 5 MiB archive round trip.
  `browser-transfer-corrected/runtime-comparison.json` proves all 113 product
  files and 24 scripts match the original tested runtime. The original failed
  full-batch receipt is retained and is not relabeled a full-suite pass.
- The new actual-browser CLI suite passed manual/agent configuration parity,
  stale writes, tracking drafts, safe release rendering and event synchronization.
  Official HTTP data in browser tests is explicitly an offline fixture.

Actual local probes found Codex **0.159.2** and Claude Code **2.1.280**; authentication
is `not_checked`. The updater now honors the process's existing proxy configuration.
**Actual official API requests still returned HTTP 403 in this environment**:
`.validation/agent-cli-official-20261009/http-diagnostic.json`. Live retrieval of
latest releases is therefore not accepted as passed; results correctly remain
unknown. No CLI was installed/upgraded and no model turn was sent. Existing Ubuntu,
physical EDA and learned-training evidence was not rerun for this addition.

## Current work and remaining gaps

- Review found and corrected characterization analysis injection through transitive
  project includes and the effective configured model library. The corrected frozen
  snapshot passed the gate below. It must not be confused with newer additions.
- Existing native launch/publication/verification and DRC navigation are structured.
  Managed instance moves, pin wiring and flat-cell paint now have actual adapters;
  device placement, hierarchical edits and every embedded third-party menu do not.
- Native target-switch/Escape and confirmed stop/restart corrections passed
  independent regression and actual browser parity. Browser state deliberately
  excludes native token fields; geometry and session identities remain discoverable.
- Source-diff review and transactional browser recovery now exist. Broader recovery
  UX and native writes outside application locks retain their documented limits.
- Training has actual weight updates but no demonstrated design improvement;
  invalid/truncated actions, comparable generation budgets and multi-seed
  held-out evaluation remain improvement tasks. Additional GPU frameworks are optional.
- Extend graph acceptance beyond the actual approved resistor patch: model-driven
  OTA design graphs, new files and multifile transactions remain distinct work.
- Complete characterization over the intended PVT/mismatch space, calibrated
  statistical claims, distributed-RC extraction and foundry signoff are not established.
- Current [live design-agent](design-agent-acceptance.md),
  [OTA characterization](ota5-characterization.md),
  [global statistics](sky130-global-correlation.md) and
  [release validation](release-validation.md) have separate scopes and evidence.
  Both the characterization/agent/statistics snapshot and the later corrected
  source-patch/editor/native integrated snapshot passed their recorded gates.

## Training completion budget acceptance, 2026-10-09

Training/evaluation now expose a shared `max_completion_length` option (integer
96–2048, new-job default 96), including observations and framing. Manual controls
and acknowledged commands share it. Partial updates preserve an existing budget;
resume rejects changed semantics, mixed-budget or over-budget trajectories.

The later freeze `.validation/training-budget-verified-_ggz03y4/` passed **623 host
Python tests, OK, five skips, 107.954 seconds**, the training controller Node tests,
actual training-browser acknowledgment/parity and installed-package validation.
Its manifest is `00576c862eb61af1f203fcd7697db40c56b351fd377ce3c00d9747a78fa5708a`.
See `host-tests.json`, `browser/evidence.json` and
`release/release-gpoay9ud/release-validation.json`. The unchanged EDA/native modules
retain the full browser/Ubuntu gate below; it was not rerun for this training change.
Registry generation remains 256 operations and 40 compatibility routes.

Actual pinned TRL/tokenizer + ngspice acceptance is recorded separately at
`.validation/training-feedback-budget-20261009-corrected/evidence.json`:
96 tokens drop the first observation; 768 retain three observations and six real
simulator calls. The production config/worker/evaluator/driver hashes match the
training freeze (`feedback-runtime-equivalence.json`). Actions are scripted and
no model weights are loaded, so this proves feedback delivery, not better design.
Full new-budget base/checkpoint comparisons and general feedback-loss telemetry
remain open. Details: [training feedback budget](training-feedback-budget.md).

## D/E/F integrated acceptance, 2026-10-09

- Host: **614 tests, OK, 5 skips**, 108.775 seconds.
- Ubuntu 24.04: **613 tests, OK, 5 skips**, 135.323 seconds; registry and all Node
  contracts, actual OTA/negative controls, native lifecycle and actual managed
  geometry/save/nonce-isolation acceptance passed.
- **All 23 browser suites passed** on a frozen copy, including source-patch review,
  same-profile draft recovery and all five editor handler/envelope races.
- Actual Xschem/Magic browser acceptance passed: manual/agent/direct-registry grid
  parity, draft-only parameter changes, named pins, explicit history save, Magic paint
  and target disconnection. Both browser drivers used the same source manifest.
- Source distribution, wheel and isolated installed CLI/HTTP service passed.
  Registry metadata validates **256 operations and 40 compatibility routes**.
- Authored Chinese coverage is **639 characters with none missing**, including the
  53-glyph offline supplement. No cloud calls or training ran as part of this gate.

Evidence root: `.validation/integration-def-drafts-wzq5e7vj/`, source manifest
SHA-256 `bae18edc43dc1da7b22d9bce354032f58eaa05ecb981fdf187467be65b1b8f9a`.
Use `host-tests.json`, `ubuntu24/ubuntu24-acceptance.json`, `browser/evidence.json`
and `release/release-jlgo05kg/release-validation.json`. Native browser evidence is
`native-browser/evidence.json`; `live-source-equivalence.json` records byte-for-byte
agreement of all 353 source files before subsequent training-budget work. This
gate certifies D/E/F; newer training changes have separate checks below.

Five host skips explicitly cover opt-in actual DRC/OTA/statistical tests, absent
host Magic and the source-patch test's prepared local executable. The Ubuntu gate
runs actual OTA and managed native checks separately; source-patch and statistical
actual evidence remains separately recorded. Counts do not imply these opted-out
cases ran in the generic suite. The offline image lacks the native Codex CLI
fixture present on the host; no remote CI execution is claimed.

The earlier `.validation/integration-def-final-ykt1pzr4/` is a **failed** attempt:
Ubuntu exposed a versioned Tcl executable lookup issue and native browser exposed
stop/restart guarding. Its ordinary browser driver hit a preexisting test port;
`browser/invalid-run.json` rejects every result from that attempt as evidence of
its intended source. The replacement driver uses port zero, exclusive PID/source/
workspace readiness receipts and owned cleanup. Failed evidence is retained.

## Corrected fixed snapshot acceptance, 2026-10-09

- Host Python: **552 tests, OK, 4 skips**; Ubuntu Python: **551 tests, OK, 4 skips**.
- Pinned Ubuntu actual OTA/negative controls and native lifecycle passed.
- Source distribution, wheel and isolated installed service passed.
- Nineteen browser suites passed on the preceding freeze; the only later source
  correction affected characterization, whose browser acceptance was rerun and passed.
- These checks precede the current source-patch, editor-recovery and native-canvas
  additions. Their targeted tests are not a substitute for a new integrated freeze.

Evidence: `.validation/batch-corrected-u__4d5n4/` (corrected host/Ubuntu/release and
characterization browser) and `.validation/batch-ddxj_1f_/browser/results.json`
(unchanged browser modules). The first Ubuntu freeze's characterization failure
is preserved; only the explicit corrected source manifest supports the pass.
New module evidence: `.validation/reviewed-source-patch-20261009-stable-read/`,
`.validation/editor-recovery-20261009-final/`, `.validation/native-canvas-safety2/`
and initial browser `.validation/native-browser-xevoqz1h/`.

## Previous batch acceptance, 2026-10-08

- Host Python: **483 tests, OK, 3 opt-in skips**, 94.922 seconds.
- Frozen Ubuntu 24.04/Python 3.12: **482 tests, OK, 4 skips**, 111.078 seconds;
  waveform/provider/state Node checks and the 223-operation registry check passed.
- All 16 main Chromium suites passed, with two additional passing regressions for
  effective project configuration and acknowledged DRC index selection.
- Actual Ubuntu OTA DRC/LVS/PEX/pre/post simulation and intentional-error controls
  passed. Actual native Xschem/Magic windows started and acknowledged cancellation.
- The real DRC-location and installed SKY130 statistical tests remain opt-in in the
  generic suite; their separate actual-tool evidence is recorded in the ledger.
  The offline container also lacks the native Codex CLI fixture installed on the host.

Local evidence: `.validation/final-acceptance/`, `.validation/final-browser/` and
`.validation/ubuntu24/replay-e51e6l14/ubuntu24-acceptance.json`. The replay's source
manifest matches the then-validated implementation, tests, scripts and deployment
files, not subsequent changes.

## Operational boundaries

Native EDA programs execute trusted design languages as the local user; snapshots
are not an OS sandbox. External editors do not acquire application revision locks.
Static model dependencies are hashed but remain external inputs. The prepared PDK
and actual tool versions belong to every run's provenance.

Source/version checks reject stale application writes. Archives cannot grant native
PIDs, CLI sessions, automatic approval budgets or checkpoint execution authority.
Secrets remain in controlled server storage/environment and are excluded from state,
configuration exports, events and durable operation replies.

The journal retains at most 1,000 completed responses for 30 days, 2,000 events and
10,000 task traces/claims. Unresolved claims remain pinned. PTY and artifact bytes
are volatile, not journal history. Transfers expire after 30 minutes idle and use
bounded disk/size/count limits; editor text limits are unchanged.

Evidence and reproduction: [operation validation](operations/validation.md),
[OTA](ota5-reference.md), [simulation](simulation-batches.md), [graphs](workflow-graphs.md),
[training](training.md), [transfers](artifact-transfers.md),
[native terminal](native-terminal.md), [Ubuntu 24.04](deployment-ubuntu24.md).
Historical 361/425/475-test runs had failures and must not be cited as full-suite
passes. The final successful runs above supersede their open integration gates.
