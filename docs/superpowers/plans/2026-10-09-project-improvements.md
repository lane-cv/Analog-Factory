# Continued project development implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` for each owned task and independent review before integration. The user authorized continued implementation; do not repeat approval questions for routine reversible work.

**Goal:** Improve the existing analog design platform against the remaining gaps documented on 2026-10-09, preserving the original project and unified-operation requirements.

**Architecture:** Keep the Python service, existing registry and native JavaScript controllers. New business actions register one real execution handler and reuse run/job supervision; validation drivers call that registry. Synthetic protocol tests and live electrical evidence remain explicitly distinct. Existing files and uncommitted work are preserved.

**Tech Stack:** Python 3.12+, standard-library core, vanilla JavaScript, ngspice/Xschem/Magic/Netgen, prepared SKY130, optional existing native agent and training runtimes.

**Spec:** `analog-factory-project-prompt-en.md`, the original unified-operation user requirements, `docs/status.md`, and `docs/operations/validation.md`. Previous acceptance proves the prior phase only; the tasks below have separate gates.

## Global constraints

- Manual operation remains usable without AI, credentials or GPUs.
- UI and agents share registered handlers, permissions, revisions and evidence.
- No fabricated measurements, model outcomes, progress or success claims.
- Use isolated validation workspaces; do not mutate the user's `.workspace`, global agent configuration or authentication.
- No publishing, committing unrelated work, or credential copying as a side effect of validation. The current checkout contains the implementation baseline absent from HEAD, so work in place with isolated disposable runtime directories.
- Models may act only within a finite explicitly issued scope. Live paid calls must be bounded and use the user's already configured backend; unavailable authentication is an explicit failed prerequisite.
- Source changes, missing tools, failed analyses and cancellations must remain identifiable failures or unknown results.

## Review focus

1. A claimed electrical success must be independently reconstructed from the actual simulator result, never model prose.
2. Characterization must retain source/PDK/tool hashes and distinguish observations from specification pass/fail.
3. A stale or modified source during a job must not be silently combined with older results.
4. Release archives and installed packages must include runtime assets but exclude workspaces, secrets, evidence caches and symlinks.
5. Tests that use local provider fixtures must not be labelled as live-model acceptance.

## Task A — OTA characterization

**Owner/files:** Domain worker owns new `analog_factory/characterization.py`, `analog_factory/operation_characterization.py`, `tests/test_characterization.py`, `scripts/validate_characterization.py`, `docs/ota5-characterization.md`. Coordinate registration hook and frontend files with root before editing shared files.

**Interfaces:** Register bounded asynchronous `reference.characterize` through the existing `Jobs` supervisor and registry, plus state/result access through the ordinary job/run operations. Accept an explicit, schema-validated list of PVT cases and analysis selections. Reuse `file.read`, real EDA preparation and `run.start` execution. Measurements include actual differential gain, bandwidth, supply power, transient settling, noise and DC offset where the requested analysis genuinely supports them. Qualified mismatch is explicit; no process Monte Carlo claim from an unqualified library.

- [x] Read current simulation/jobs/reference contracts and define exact schemas and source ownership.
- [x] Add failing tests for missing source/PEX, invalid cases, source conflicts, cancellation and independent measurement calculations.
- [x] Implement bounded real execution with immutable inputs, raw evidence and per-metric units/conditions/definitions.
- [x] Connect a real user flow through shared registered operations and regenerate metadata at integration.
- [x] Run actual pre/post and PVT/analysis cases with the prepared PDK; report failures and performance limits without weakening specifications.

## Task B — live design-agent acceptance

**Owner/files:** Agent worker owns `scripts/validate_design_agent.py`, `tests/test_design_agent_validation.py`, `docs/design-agent-acceptance.md`, and a focused reusable validation module if needed. Avoid editing agent core unless a reproduced defect requires it; coordinate first.

**Interfaces:** Use `agent.session.create/send/get/cancel`, finite automatic grants, registry source revisions and actual `run.*` operations. The primary live case is a five-MOS OTA netlist design target, with an independent final simulator check. Educational amplifier cases can diagnose transport but do not substitute for OTA acceptance. Report backend/model/effort, session/run/trace IDs, bounded actions/runtime, usage when available, source changes, and electrical outcome.

- [x] Inspect available native backend through its supported public interfaces; never print secrets or modify global configuration.
- [x] Add failing contract tests for fabricated-success text, absent/failed simulator evidence, exhausted budget and cancelled sessions.
- [x] Implement the repeatable bounded driver, preserving failed sessions and evidence.
- [x] Run the actual configured backend on isolated public test assets; distinguish transport availability, tool execution, and electrical success.
- [x] Fix reproduced integration defects through the same registry and rerun the relevant acceptance.

## Task C — clean release/install verification

**Owner/files:** Root owns `scripts/validate_release.py`, `tests/test_release.py`, release documentation, and CI packaging additions.

**Interfaces:** Build a source distribution and wheel from the current source tree in disposable directories; inspect archive members, install into an isolated environment and execute CLI/registry/template/static-asset smoke checks outside the repository. Record source/package hashes and command exit statuses. Never publish as part of this driver.

- [x] Add failing tests for missing runtime assets and unsafe/secret archive inclusion.
- [x] Implement archive validation and isolated installation smoke checks without copying private state.
- [x] Build and verify actual packages; add the repeatable gate to CI.
- [x] Keep uncommitted state and remote CI status explicit until actual release/remote evidence exists.

## Subsequent tasks retained in the active objective

- [x] Human/agent concurrency: source-diff review and safe draft recovery, explicit uncertain-outcome inspection, and reconnect UX through registered commands.
- [x] Statistical compatibility: investigate installed SKY130 global correlation failure using actual controlled probes; qualify only verified model semantics.
- [ ] Training effectiveness: address invalid/truncated actions, declare comparable budgets, run multiple seeds and retain unsuccessful results; expand tasks only with independent electrical scoring.
- [x] Visual workflows: assess controlled source-patch nodes with revision checks/review and immutable execution history; preserve current no-write safety until implemented.
- [ ] Advanced EDA/native/GPU extensions: implement and verify where prerequisites support them; otherwise document concrete missing prerequisites and avoid placeholder operations.

## Integration and completion gate

- [x] Independent review of each task's implementation and actual evidence.
- [x] Updated registry metadata, inventories, documentation and critical browser parity tests.
- [x] Focused tests followed by final full regression on the integrated tree.
- [ ] Reconcile every task above with source/evidence. Unimplemented or weakly evidenced requirements remain open; do not close the overall goal because one batch passes.

## Task D — reviewed source patches in visual workflows

**Design decision:** Reuse the existing `file.write` operation, project lock,
revision history and human-only `job.control` review. Add one read-only
`file.change.preview` operation and a `source_patch` graph node; do not create a
second writer or approval service. This implements the original design-agent
diff/review requirement while preserving ordinary read-only model/tool nodes.

**Owner:** Graph worker owns the focused preview helper, graph engine/catalog,
existing write receipt extension if necessary, graph review controller, focused
tests and graph documentation/example. Root coordinates `app.js`, UI metadata,
translations, generated inventories and browser integration. Implement after the
current characterization/agent/statistical batch has been frozen for validation.

**Contract:** A fixed configured existing file path receives mapped replacement
text and its expected SHA-256. Preview returns exact current/proposed hashes,
project version, complete unified diff and immutable proposal identity. Limit
each source to 64,000 UTF-8 bytes, one proposal to 256 KiB, and one entire graph
including nested loops to 20 proposals/2 MiB of proposal evidence. Reject hidden,
credential/configuration, nontext, symlink and noncanonical paths before preview.
The reusable preview may be consumed by agent write approvals and later editor
conflict UI. A preview never authorizes execution.

**Authority and execution:** Review binds project/job/full node/attempt/proposal
hashes/version to a fresh token. Acceptance requires human approve and write
permissions; no ordinary graph output can approve it. Under the shared project
lock, recheck cancellation/deadline, complete source map, registry version, file
identity and token, invoke `file.write` once, verify exactly that replacement,
then advance the graph's accepted source generation. Preserve old run snapshots,
before/after hashes, operation trace and recoverable revision. Patch retries are
forbidden. A post-commit failure records applied or uncertain outcome and stops;
it must never silently repeat the write or claim rollback.

- [x] Shared preview contract and registered metadata, with permission/path/bounds tests.
- [x] Token-bound patch review and source-generation transition, preserving all ordinary conflict guards.
- [x] UI shows the exact proposal and active review only; historical jobs have no live approval buttons.
- [x] Actual approved resistor replacement followed by ngspice proves the new snapshot/result; rejection, stale review, cancellation, timeout, token replay and post-write failure tests prove no unsafe replay.
- [x] Independent review, regenerated discovery and browser/agent parity.

## Task E — editor conflict review and recoverable drafts

**Design decision:** Keep the current editor and source revision guards. Add a
focused controller for three-way inspection (opened base, latest saved source,
human draft), using the shared preview/read operations. A reviewed merged result
first becomes an editable draft pinned to the inspected latest version; normal
`file.write` remains the only save path. Concurrent edits after review must still
reject. Never automatically overwrite either contributor's text.

Persist bounded draft recovery in browser-owned storage keyed by project/file;
do not broadcast source text in normal UI snapshots. Expose named UI commands
for recovery listing, explicit reading, restoration, conflict inspection and
resolution. Surface storage failures and reconnect/unknown-outcome states.
Restoring a draft does not save it or grant a newer revision implicitly.

- [x] Inspect existing editor/draft/approval flows and implement focused storage/controller contracts.
- [x] Connect conflict review, recovery and resolution to registered UI commands and real controls.
- [x] Real two-client tests preserve both writers; browser reload recovers a draft; stale review and storage failure are explicit.
- [x] Update inventory, translations and independent review before final integration.

## Task F — managed native editor semantic operations

**Design decision:** Extend actual Xschem/Magic sessions with fixed, allowlisted
Tcl adapters loaded at startup. Reuse existing process supervision, project
revisions, permissions and operation registry. No arbitrary Tcl/shell endpoint,
screen clicking, reload-based editing or automatic takeover of an unmanaged editor.
The installed native primary sources expose the required instance/selection/move
and pin/wire or box/layer primitives; actual behavior still needs qualification.

**Owner:** Native worker owns a focused session manager/catalog, packaged fixed
adapters, narrow EDA launch integration, focused tests, actual Xvfb driver and
native coverage documentation. Coordinate service/UI registration and app hooks
with root. Graph and recovery workers own their unrelated modules.

**Contract:** A managed run becomes usable only after a matching native ready
acknowledgment. Commands target project/run/session and expected native revision.
Private bounded request/response files carry a session nonce and command ID;
fixed Tcl handlers parse data and dispatch only registered semantic verbs.
Observe the actual native current source, modified state, instance identities and
geometry. Account for unsaved human edits in revision checks inside the native
callback, and reject native busy/drawing state rather than interrupting it.
Missing/disconnected acknowledgments fail explicitly; after possible delivery,
timeouts remain unknown outcomes and cannot be silently retried.

Initial implemented verbs must form a usable flow: start/inspect, select/move an
instance in native grid units, connect named Xschem pins, paint an acknowledged
Magic box on a discovered layer, and explicit save. Saving requires unchanged
disk and native versions and uses the existing project's history/conflict
contract. Edits stay in the same native session, preserving human unsaved work.
If a native primitive cannot meet those semantics, leave it undiscoverable until
implemented and retain the gap in the coverage inventory.

- [x] Test private command transport, identity/revision/permission errors and lifecycle before registering real handlers.
- [x] Qualify actual native state inspection and semantic editing under private Xvfb, including simultaneous unsaved human edits and stale-command rejection.
- [x] Prove Xschem connectivity by real netlisting and Magic geometry by saved source plus fresh DRC; an edit acknowledgment alone is not design correctness.
- [x] Connect first-party controls through the same registry, with real target acknowledgment and no historical-session authority.
- [x] Independently review adapters, document native command/menu coverage and retain unsupported third-party UI gaps explicitly.

## Historical integration evidence before the D/E/F gate, October 9

Tasks A/B/C use the explicit corrected snapshot
`.validation/batch-corrected-u__4d5n4/`: 552 host tests, 551 Ubuntu tests,
four skips in each, installed-package verification and actual OTA/native
lifecycle passed. The initial incorrect characterization freeze is retained as
failed evidence. These results do not cover Tasks D/E/F.

D has 126 focused tests and actual resistor source-generation evidence in
`.validation/reviewed-source-patch-20261009-stable-read/`. E has actual browser
conflict/reload, same-profile independent variants, atomic quota rollback,
legacy migration and handler/envelope race evidence in
`.validation/editor-recovery-20261009-final/`. Browser persistence uses IndexedDB
transactions after a real Web-Lock/localStorage lost-update failure; saved text
is re-read with its checked source_version before a draft is cleared.

F has real Xschem connectivity, Magic geometry/DRC, already-dirty human-edit
revision conflict and slow-save receipt evidence in
`.validation/native-canvas-safety2/`. Initial full-browser actual native parity
passed in `.validation/native-browser-xevoqz1h/`. Independent review subsequently
requires final controller target-switch and invalid-draft corrections; the final
browser gate must run after those corrections. Public native browser state omits
box_token to preserve existing credential redaction; direct native commands keep
that parameter privately. Registry discovery now handles incomplete target
parameters explicitly and validates availability callback result types.

Current generated registry: 256 operations and 40 compatibility routes. New
Node/browser gates are in CI, but no remote CI execution is claimed. Final
integrated source freeze, full regression, release and Ubuntu replay remain open.

## Final D/E/F integrated gate

The final snapshot `.validation/integration-def-drafts-wzq5e7vj/` passed
614 host tests, 613 Ubuntu tests (five skips each), all 23 browser suites,
installed-package validation, actual OTA/native lifecycle and semantic geometry/
save checks. Actual native-browser final evidence is
the same root's `native-browser/`; both browser drivers record the same manifest.
Seven native controller regressions cover target changes, Escape, invalid drafts,
confirmed-stop/new-start and manual/remote draft-only parity. Details and the
retained failed/invalid attempts are in [status](../../status.md).

D/E/F are complete within their explicitly bounded contracts. The overall goal
remains active: full third-party native UI coverage, broader EDA/statistical scope
and training effectiveness are not established. The next concrete training issue
is documented in [training-feedback-budget](../../training-feedback-budget.md):
34 generated action tokens plus 129 measured-feedback tokens exceed the old
96-token aggregate cap. Do not claim better design outcomes without a new bounded,
independent held-out comparison.

## Task G — feedback-inclusive training completion budget

**Design:** Preserve the existing TRL adapter and legacy default of 96. Add one
registered `max_completion_length` integer option (96–2048), reused by UI/agent
training and evaluation. Its meaning is the aggregate completion, including real
tool observations and framing. Keep the task's action and simulator limits.
Normalize missing historical values to 96; evaluation resume cannot change this
budget or its semantics. Evaluate a checkpoint with a different budget in a new
job. Existing training evidence stays unchanged.

**Ownership:** Backend worker owns schema, manager, worker, resume and focused
Python contracts. UI worker owns controls, translations and parity/race tests.
Root owns actual pinned-tokenizer/TRL scripted tool-loop acceptance, documentation
and integration. No new optimization is needed to verify this adapter behavior.

- [x] Add failing contracts for option bounds, shared registry exposure and resume identity.
- [x] Use normalized options in runtime configuration, requests, trajectories and reports.
- [x] Connect the manual control and acknowledged UI commands; preserve edits during async job selection.
- [x] Exercise the real installed TRL tool loop with scripted actions and actual ngspice observations; compare 96 vs expanded budget and retain tool masks.
- [x] Review and validate the changed modules and generated metadata.

A scripted policy proves feedback delivery, not model learning. A new paired
held-out base/checkpoint experiment and multiple seeds remain necessary before
claiming improved circuit-design outcomes.

Task G bounded gate: `.validation/training-budget-verified-_ggz03y4/`, 623 host
tests/five skips, actual training-browser and Node contracts, installed packages.
Real scripted TRL/ngspice evidence is byte-matched for config/worker/evaluator and
validation driver. Independent review reproduced and fixed schema-default reset
and over-budget resume issues. A new model comparison and training-effectiveness
claim remain outside this completed adapter change.
