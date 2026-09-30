# Delivery status

Statuses describe this implementation, not the upstream tool's capabilities.

| Phase | Implemented or experimental | Acceptance still open |
|---|---|---|
| 0: technical validation | Architecture, researched integration/capability matrix, tool/PDK/display diagnostics, real ngspice 46 validation, executable hashes and evidence scripts | Ubuntu 24.04 desktop validation; prepared SKY130 combination; xschem/Magic/Netgen execution and GUI coordination |
| 1: manual MVP | Project/source management and revisions, simulation forms/directives, actual measurements/waveforms, run provenance, stale checks, cancellation/timeouts/retries, comparisons and archives. EDA adapters exist for other tools | Complete matching SKY130 schematic/layout, real DRC and LVS reports, actual extraction/post-layout run; GUI integration; graphical PVT and Monte Carlo configuration. Therefore Phase 1 is **partial**, not accepted end-to-end |
| 2: assistant | API tool loop (OpenAI-compatible/Ollama), confirmed file edits with revisions and simulations, Codex/Claude structured CLI adapters, persistent project conversations, native CLI resume, activity polling, cancellation, selected file/run context, endpoint-scoped credentials; minimal live Codex and Claude conversations verified | Live API inference and broader CLI task validation; interactive terminal; CLI per-tool approvals and application-managed rollback; token streaming for API; OS keychain persistence |
| 3: visual agents | Editable versioned JSON graph and draggable nodes; real bounded scalar tuning chain with six typed stages, cancellation, iteration/runtime budget and run history | General graph execution, branch/loop nodes outside the tuning template, variable-port mapping, node-level retry/debugger, model nodes, cost budgets and all four example agents |
| 4: evaluation/RL | Experimental isolated episodes, deterministic random-search parameter baseline, independent fixed evaluator, reward breakdown, model-weight-training distinction, raw evidence archival | Agent Lightning training integration, trainable model, checkpoint lifecycle, token-level training trajectories, held-out pre/post model comparison, PVT robustness, additional framework adapters. **No model training is claimed** |

## Known operational limits

- This is a trusted, single-user local tool. EDA tools execute design languages and can
  access the user's filesystem; input snapshots are not an operating-system sandbox.
- Use one server per data directory. A crashed service marks abandoned records interrupted;
  it does not claim to safely reattach editor/process sessions after a crash.
- Run snapshots include project files. External model dependencies are separately identified
  and hashed where statically resolvable; they are not copied into immutable snapshots.
  Their provenance remains explicitly incomplete. Dynamic includes need manual review.
- The educational device model has no foundry accuracy. No PDK, schematic/layout pair,
  DRC pass, LVS pass, parasitic result or tapeout/signoff evidence is bundled.
- Magic extraction requests capacitance-inclusive SPICE. Distributed-resistance extraction
  and complete RC signoff are unvalidated.
- AC complex raw data preserves real/imaginary values; the browser plots magnitudes.
  Multi-plot rawfiles are parsed, but the initial UI displays the first plot.
- Scalar tuning assumes the target is bracketed and the metric is monotonic over the
  selected interval. It stops explicitly when the target is not bracketed. It is not a
  general circuit optimizer or an RL algorithm.
- Evaluation currently samples one nominal educational circuit. It does not measure
  robustness over process/voltage/temperature or certify improvement on held-out tasks.
- Provider replies remain unverified suggestions. API tools can create actual execution
  records after approval; CLI output alone is not application verification evidence.
  Usage/cost stay unknown when the service does not supply them. Model discovery can
  be unsupported; manual entry remains. See [agent limits](embedded-agents.md).
- Editor files are limited to 2 MiB. Run waveforms and archives are read into memory;
  very large circuit datasets need a future streaming/downsampling layer.

## Next acceptance work

1. Provision and freeze a prepared SKY130 installation on Ubuntu 24.04 with a graphical
   session; validate native editors and batch adapters using clean and defective designs.
2. Complete one matching schematic/layout through DRC, LVS, extraction and post-layout
   simulation; publish reports, coverage and reproducible input closure.
3. Validate live model endpoints and authenticated CLI versions against the deterministic
   adapter contracts, then add richer streaming, CLI approvals and terminal integration.
4. Generalize the execution graph behind typed ports and resource locks; add the four
   requested agent examples with real-run acceptance tests.
5. Freeze independent training/held-out tasks, then integrate one current Agent Lightning
   backend with actual trainable weights, checkpoint and budget-matched evaluations.
