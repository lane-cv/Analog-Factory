# Advanced simulation and statistical qualification

The **Advanced simulation** control in the Simulation panel prepares a bounded
set of actual ngspice runs. Parameter axes, supply values, temperatures, model
sections, and statistical samples form a Cartesian product. Every scenario uses
an immutable run snapshot; the original testbench and included source files stay
unchanged. Save an open editor draft before previewing.

1. Select an existing testbench and AC, transient, or operating-point analysis.
2. Add parameter axes and, optionally, a supply axis. Each parameter must have
   exactly one `.param` declaration in the selected testbench.
3. Enter temperatures and, optionally, a model library and its actual `.lib`
   sections. Corner selection requires exactly one top-level `.lib` declaration.
   Relative libraries must be visible project files; absolute libraries must
   belong to the configured PDK root.
4. Add measurements, then validate the scenario count and inspect the generated
   input and control directives.
5. Start the validated revision. Results show each condition, measured values,
   terminal status, logs, provenance, and a waveform link. Cancelling stops the
   active child run and prevents later scenarios.

A changed source/configuration revision invalidates a preview. Source or tracked
model changes during a job stop subsequent scenarios. Missing measurements remain
missing and fail the scenario; they are never replaced by zero. A simulator
success exit alone does not satisfy a requested measurement.

## Measurement definitions

| Measurement | Required analysis | Definition |
|---|---|---|
| Gain | AC | `db(v(output)/v(input))` at the requested frequency; optional negative input/output nodes define differential voltages |
| Bandwidth | AC | First falling crossing of reference-frequency gain minus the selected drop in dB |
| Phase | AC | Continuous output/input phase from `cph`, converted to degrees at the requested frequency; this is not phase margin |
| Phase margin | AC with explicitly declared loop gain | `180 + phase(L)` at the first falling unity-gain crossing, with `L = output/input` defined in `1+L(s)=0`; records crossing frequency and continuous phase in the raw run metrics |
| Supply power | OP or transient | `-v(supply_node)*i(voltage_source)`; instantaneous OP value or transient average over the selected interval |
| Output swing | Transient | Observed peak-to-peak output voltage over the selected interval; it does not establish maximum linear swing |

The generated ngspice control driver derives ratio/power vectors and executes real
`meas`/`print` commands. `metadata.conditions.measurement_definitions` records each
expression, units, analysis, and directives. Inputs with their hashes, conditions,
logs, metrics, tool versions, and raw waveforms remain in the normal run record.
Model files outside the project are dependency-hashed, not bundled into that
snapshot; the normal provenance limitations still apply. Basic DC and noise
configuration remains available in the existing simulation panel; advanced
measurement batches currently provide the combinations listed above.

Phase margin requires `loop_gain_convention: negative_feedback_L`. Prepare a loop
injection/return testbench that preserves the circuit's loading; a normal closed-loop
voltage transfer is not automatically a loop transfer. The measurement rejects
sweeps that start at/below unity, start with phase outside (-90°, 90°), or lack a
falling unity crossing. It uses ngspice's continuous phase and interpolated `meas`
crossing, retains negative margins, and does not establish complete stability for
multiple-loop, multiple-crossing or open-loop-unstable systems. Its definition
links the actual unity-frequency and phase metric names to the execution record.
The implementation follows the native
[ngspice measurement/control language](https://ngspice.sourceforge.io/ngspice-control-language-tutorial.html).
`tests/test_phase_margin.py` compares a real two-pole loop against its analytic
margin and verifies missing-crossing, low-gain and wrong-polarity failures.
Copy `examples/simulation/phase-margin.cir` into a project's
`testbenches/phase-margin.cir` and use `examples/simulation/phase-margin.json` as
the batch specification. This analytical two-pole reference measured 19.8266° in
the actual browser/ngspice acceptance test; it illustrates the declared transfer
convention rather than claiming a transistor-level loop-loading model.

## Three distinct kinds of variation

**Engineering parameter perturbation** draws bounded uniform or bounded-normal
samples for explicit `.param` values using a repeatable seed. Normal samples use
rejection sampling within explicit minimum/maximum bounds, with a finite attempt
limit. These values describe an engineering experiment, not a foundry distribution.

**Global process variation** requires a model section that declares process
variation enabled and mismatch disabled, and actual evidence that matched devices
share the same within-sample draw while distinct seeds change the result.

**Device mismatch** requires a section that declares mismatch enabled and global
process variation disabled, and actual evidence of different matched-device
currents with repeatability for the same seed. Combined process-plus-mismatch is
not advertised by this implementation.

Select a mode and one model section, then run **five qualification probes**:
nominal `tt` with seeds A and B; selected statistical section with A, A, and B.
Identically biased pairs of SKY130 `nfet_01v8` and `pfet_01v8` devices measure four
drain currents. Qualification checks finite positive currents, nominal seed
invariance, nominal matching, same-seed repeatability, seed sensitivity in both
families, and the requested pair correlation. This checks simulator/model
compatibility at 27 °C and 1.8 V. It does not calibrate a statistical distribution,
validate geometry dependence, establish yield, or provide foundry signoff.

Successful qualification is bound to the library/section, external dependency
hashes, simulator version, launcher and target-binary hashes, and the two tested
model families. Included circuit sources must remain project-relative and cannot
select additional model libraries or override statistical switches. A batch
rechecks qualification before preparation and its tracked dependencies before
each snapshot. Imported evidence and records from an earlier server lifetime
cannot authorize statistical runs; run qualification again in the current server.
The UI can inspect historical evidence without treating it as current authority.

## Actual installed SKY130 evidence

On 2026-10-08, real ngspice 46 with the local SKY130 compatibility launcher and
open_pdks revision `1689ac3f2dc763876eaf967227c7dfe831b031ae` was exercised in
separate temporary workspaces. The tested library was
`sky130A/libs.tech/combined/sky130.lib.spice`, SHA-256
`48de7c677e2c6e7d09b2559279de9f818be71010a4aa933d728eb4db3b133c84`.

- `tt_mm` passed all six checks across five real OP runs. Seed 41 repeated the
  same n-channel pair currents, `6.89859395393462e-6` and
  `8.81285018543114e-6 A`; seed 42 changed them to
  `9.859144658865698e-6` and `1.344573292911405e-5 A`.
  Two separately seeded mismatch batch runs then measured drain-supply powers
  `8.253198e-6` and `9.165873e-6 W`. Original project sources were unchanged.
- `mc` completed five real OP runs and passed finite-current, nominal,
  repeatability, and seed-sensitivity checks. It failed the requested global
  correlation: for seed 41, its n-channel pair measured
  `1.187526261600159e-5` and `1.200714507466547e-5 A`; its p-channel pair also
  differed. The application therefore rejects this installed section as a
  qualified **global-process** sampler even though the library declares
  `MC_PR_SWITCH=1` and `MC_MM_SWITCH=0`.

Model loading takes tens of seconds per process on this installation. The global
correlation failure was reproduced in a second complete five-probe run using the
180-second per-probe limit (308 seconds overall). An earlier concurrent-load
attempt exceeded a smaller 90-second per-probe limit and remained unqualified.
Budget failures never replace completed evidence or enable a mode.

Controlled probes on 2026-10-09 identified ngspice 46's rewriting of random
`.param` assignments into functions as the cause of the installed `mc` pair
correlation failure. Caching the top-level FET random draws in an isolated model
copy restored matching in the two tested families, including a full-library
seed-41 comparison. The installed section remains unqualified. See the
[diagnosis, reproducible probe, hashes, measurements, and qualified limits](sky130-global-correlation.md).

An explicit experimental `model_semantics: cache_global_fet_draws` option on
`simulation.statistics.qualify` now prepares fully bundled model snapshots and
runs fresh five-probe qualification for `global_process` / `mc`. Both the
qualification request and the later `statistics` batch specification must select
that semantics; the default remains `installed`. A matching live certificate
binds original model hashes, generated copy hashes, adapter implementation, and
simulator identity. The adapter does not modify the installed PDK or rewrite
local device mismatch draws. Its copied-model closure and the aggregate generated
input byte limit also bound the number of sample scenarios.

## Shared operations and bounds

All browser, CLI, workflow, and agent callers use these registered handlers:

- `simulation.batch.capabilities`: actual executable, discovered library sections,
  and live qualification records.
- `simulation.batch.preview`: validate a specification, return the scenario count,
  source revision, conditions, and first generated inputs.
- `simulation.batch.start`: revision-checked, cancellable `simulation-batch` job.
- `simulation.statistics.qualify`: revision-checked, cancellable
  `statistical-qualification` job.
- Existing `job.get`, `job.list`, `job.cancel`, `run.get`, `run.log`, and
  `run.waveforms`: the same evidence and lifecycle used elsewhere in the product.

Execution operations require execute permission and agent confirmation. They
respect project scope. Only one orchestration job can run in a project. The
browser's `ui.simulation_batch.*` commands invoke the same controller and return
acknowledged state; they do not introduce a separate execution engine.

Batches permit at most 128 scenarios, three parameter axes of at most 32 values,
16 temperatures, 16 model sections, 16 measurements, and 32 MiB of generated text.
Each analysis requests at most 200,000 samples. A batch has a total runtime limit
of at most 1,800 seconds and a per-run limit of at most 300 seconds. Qualification
has five probes, a total limit of 900 seconds, and a per-probe limit of 180 seconds.

Example preview specification for an RC testbench declaring `.param R=1000`:

```json
{
  "source": "testbenches/pre.cir",
  "analysis": {"type": "ac", "parameters": {"start": 1, "stop": 100000, "points": 60}},
  "axes": [{"parameter": "R", "values": [1000, 2000]}],
  "temperatures_c": [27],
  "measurements": [
    {"kind": "gain", "name": "gain_db", "input": "in", "output": "out", "frequency_hz": 100},
    {"kind": "bandwidth", "name": "bandwidth_hz", "input": "in", "output": "out", "reference_hz": 1, "drop_db": 3.01029995664}
  ]
}
```

Pass it as `{ "project_id": "…", "spec": { … } }` to preview, then use the
returned `source_version` as `expected_version` for start. With a 1 µF capacitor,
actual tests measure bandwidths near 159.15 and 79.58 Hz for these resistance values.

## Reproduce verification

```bash
python3 -m unittest tests.test_simulation_batches -q
AF_TEST_REAL_SKY130_STATISTICS=1 AF_SKY130_VARIATION_KIND=device_mismatch \
  python3 -m unittest tests.test_simulation_batches.BatchTests.test_real_sky130_statistics_measures_correlation_before_enabling_samples -q
```

The opt-in installed-PDK test uses only temporary projects, performs real model
qualification and fresh sample runs, and can take several minutes. Omitting
`AF_SKY130_VARIATION_KIND` exercises both known installed-model behaviors. The
standard suite exercises actual RC AC/transient/OP measurements, differential
measurements, library-corner effects, cancellation, deadlines, stale inputs,
bounds, registry revisions, forged qualification records, and executable identity.
`tests/browser-simulation-batches.cjs` exercises native graphical fields, real
results/logs, stale preview rejection, and acknowledged remote UI invocation
against a disposable server.

The implementation follows the official [ngspice control-language tutorial](https://ngspice.sourceforge.io/ngspice-control-language-tutorial.html),
[ngspice manual](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf), and
[SKY130 simulation documentation](https://skywater-pdk.readthedocs.io/en/main/sim.html).
Actual local qualification results, rather than documentation or section names,
determine which statistical mode is enabled.
