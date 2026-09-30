# Analog-factory implementation plan

**Goal:** Deliver a runnable local workspace and evidence-backed incremental EDA integration.
**Architecture:** Python standard-library service and CLI, local browser UI, native EDA subprocesses.
**Spec:** `analog-factory-project-prompt-en.md` and `docs/architecture.md`.

## Constraints and review focus

AI must be optional. No fabricated results. No implicit credential reads. Loopback-only
service with origin checks. File paths remain inside projects; symlink traversal is
rejected. Run snapshots and artifacts are immutable to workspace editing. Missing tools,
invalid simulator outputs, cancellation races, stale results, and unknown verification
outcomes must be explicit. Native GUI tests require a display. Training is a separate gate.

## Tasks

- [x] 1. Execution and EDA adapters (`execution.py`, `toolchain.py`, `eda.py`, `waveforms.py`).
  Write and run critical failing unittest cases; implement snapshots, hashes, subprocess
  groups, timeouts/cancellation/retry, genuine report parsing, ASCII raw waveform parsing.
  Verify with `python3 -m unittest discover -s tests -v` and an installed ngspice run.
- [x] 2. Projects and service (`projects.py`, `service.py`, `server.py`, `__main__.py`).
  Tests first for traversal, revisions, API origin rejection and end-to-end simulation.
  Implement project templates, shared service APIs, CLI and loopback HTTP transport.
- [x] 3. Workspace UI (`static/index.html`, `app.js`, `style.css`).
  Connect project tree/editor, simulation builder, waveforms, native-tool actions, run
  logs/cancellation, diagnostics, context sidebar, workflow editor and settings.
  Check JavaScript syntax and HTTP integration; perform browser checks if available.
- [x] 4. Workflow and evaluation (`workflows.py`, `evaluation.py`, tests).
  Pin bounds, invalid graphs, target stopping and independent scoring with tests first.
  Run the amplifier tuning example with real simulator outputs and isolated episodes.
- [x] 5. Optional provider adapters (`providers.py`, tests).
  Validate protocol-specific configuration, selected-context limits and secret redaction.
  Provide real HTTP requests and documented CLI integration without implicit auth access.
- [x] 6. Delivery (`README.md`, `docs/`, `scripts/`, packaging).
  Record researched integration matrix, reproducible installation, execution evidence and
  phase acceptance status. Run complete tests, CLI demo, HTTP smoke tests and fresh review.

## Execution record

Initial inspection: no application files or usable Git repository. Ubuntu 26.04 LTS,
Python 3.14.4, Node 22.22.1, ngspice 46; xschem/magic/netgen/PDK and GUI display absent.
Implementation proceeds in place under the user's instruction to build incrementally
and choose reasonable defaults. Research, EDA adapters and UI can proceed independently
against explicit service contracts; integration and final verification remain sequential.

Delivered this incremental plan with 62 passing Python tests, actual ngspice evidence,
and Chromium UI checks. Independent review led to fixes for generated-script merging,
credential endpoint scope, canonical protected paths, evaluation evidence/export,
verification report filenames, external model staleness, and cross-process job state.
All fixed behavior has regression coverage. Broader product-phase acceptance remains
explicitly partial in `docs/status.md`; completing this plan does not complete all phases.

Decisions: standard-library core avoids mandatory dependency installation; external
models are hashed rather than copied, with incomplete provenance explicit; no Git
commits/worktrees were created because this directory has no usable Git metadata.
