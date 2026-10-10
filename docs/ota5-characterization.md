# OTA characterization

`reference.characterize` runs saved five-transistor OTA pre/post testbenches through
actual registered `run.start` operations under a cancellable orchestration job.
The simulation dialog's **OTA characterization** opens native fields for views,
analyses, up to twelve explicit PVT cases, and optional electrical targets.
CLI, MCP, workflows and agents use the same version-checked operation.
Save an editor draft before starting. Post-layout characterization requires
`netlists/extracted.spice` containing parasitic capacitors; a missing PEX source
is rejected before execution. The original testbenches are preserved.

Example operation parameters (supply `expected_version` in the invocation envelope):

```json
{
  "project_id": "0123456789abcdef0123456789abcdef",
  "spec": {
    "views": ["pre", "post"],
    "cases": [
      {"corner": "tt", "supply_v": 1.8, "temperature_c": 27},
      {"corner": "ss", "supply_v": 1.62, "temperature_c": 125},
      {"corner": "ff", "supply_v": 1.95, "temperature_c": -40}
    ],
    "analyses": ["ac", "op", "dc", "tran", "noise"],
    "targets": {"gain_db_min": 30},
    "limits": {"runtime_seconds": 1800, "run_timeout": 180}
  }
}
```

Cases accept the installed deterministic `tt/ff/ss/fs/sf` sections, supply
1.2–1.95 V, and temperature −40–125 °C. These are bounded inputs, not a claim
that every combination is safe or meets specifications. The reference retains
its original 0.9 V input common mode, 0.65 V external tail bias and 1 pF load.
There are at most 128 runs, a maximum 1,800-second job budget and a maximum
300-second child budget. Only one orchestration job runs in a project.

Measurements are reconstructed independently from actual ASCII raw samples;
reduced display data and modified raw artifacts are rejected. Each run retains
conditions, definitions, source snapshots, tool/PDK identity, model dependency
hashes, simulator logs and waveform artifacts. External PDK files remain external;
static dependency accounting has the same limits as normal run provenance.
Executable provenance records and tracks both the ngspice binary and explicit
launcher targets. Supported launchers have one absolute `exec` target per layer
(up to four layers); tracked executables are bounded to 16 MiB each. Unsupported
launcher structures are explicit prerequisites, not qualified identities.
Tracked tool/model/source/configuration changes stop later scenarios, and stale runs
cannot contribute a successful verdict.

Preparation validates the complete circuit and model include ownership before
launch. Project `.include`/`.inc` paths must resolve to literal visible project
files (at most 64 files and 1 MiB). Included circuit cards cannot contain control
blocks, analysis/measurement/temperature/save overrides, additional libraries or
statistical-switch overrides. The configured PDK library remains outside project
sources; its static model closure is bounded to 1,024 files and 64 MiB. Model-native
cards and options remain allowed, but executable controls and analysis overrides
are rejected. Model includes must resolve uniquely within that validated model
closure. Missing, dynamic or ambiguous dependencies are prerequisites that fail
before a job starts; hashing an arbitrary simulator-written file is insufficient
to establish an independent measurement.

| Analysis | Observation |
|---|---|
| AC | Differential gain at 1 Hz and the first falling −3.0103 dB bandwidth, interpolated in log frequency over 1 Hz–100 MHz |
| OP | Actual output DC voltage and `−V(vdd) × I(VDD)` supply power |
| DC | Input offset defined as differential input producing output VDD/2, interpolated within ±10 mV; no unique crossing is a measurement failure |
| Transient | Open-loop 100 µV differential step at 1 µs; last crossing into 1% of final-window mean during a 50 µs observation; drifting tails fail |
| Noise | Output noise RMS from trapezoidal integration of squared ngspice output-noise density over 1 Hz–100 MHz |

Offset is this circuit-level midpoint definition, not measured silicon or a
statistical mismatch distribution. Settling is a finite-window open-loop
observation relative to its final ten-percent window, not a proof of asymptotic
settling or closed-loop stability. Noise is output-referred and includes only
models represented by this testbench. No mismatch/process Monte Carlo mode is
silently enabled; use separately qualified statistical batch operations.

With no targets, electrical status is `observed`, never `pass`. Optional targets
are `gain_db_min`, `bandwidth_hz_min`, `power_w_max`, `offset_v_abs_max`,
`settling_s_max`, and `noise_v_rms_max`. Missing measurements produce `unknown`
checks; out-of-spec observations produce `fail`. Execution status and electrical
status are separate: a fully executed job may fail its specified targets.
Measurement failures remain failed execution records and do not fabricate zeros.

Inspect the job with `job.get`; each iteration links `run.get`, `run.log`, and
`run.waveforms`. The browser can inspect conditions/provenance/logs and open
waveforms. Closing the dialog preserves the job; cancellation stops its active
simulator before later cases can run.

Opening **OTA characterization** restores an active job, otherwise the most recent
recorded job. **Characterization history** reopens older reports without starting
any computation. **New characterization** prepares a draft against the current
project version; **Run characterization** remains a separate explicit action.
Edited drafts retain their source-version guard when closed and reopened.
Remote callers use `ui.characterization.open` with `job_id` for history, or
`new_job: true` for a new draft.

Reports show measured values, units, recorded targets, signed margins and PVT
conditions. Positive margin meets the recorded bound; absent measurements remain
unknown. The worst-condition summary identifies each target's smallest measured
margin and shows measured coverage against every requested view and PVT case;
missing conditions prevent a complete passing summary.
Historical reports always use their recorded targets even while the next
job's draft is edited. Project-source freshness is labelled at the last refresh;
it is not a new qualification of external PDK files. Per-run logs, waveforms and
raw provenance remain available beneath the summary.

Reproduce actual qualification in an isolated workspace:

```sh
python3 scripts/validate_characterization.py \
  --settings .validation/settings.json \
  --workspace .validation/ota5-characterization
```

The driver first provisions a fresh OTA through full Xschem/Magic/Netgen/ngspice
nominal acceptance with the original >30 dB requirement and negative controls.
It then invokes the registered characterization operation and writes
`ota5-characterization.json`. Optional `--spec FILE` selects a bounded case list.
Failures retain actual jobs and logs and return a nonzero exit status. A small
linear differential RC fixture in the unit suite exercises real simulator
analyses independently; it is not SKY130 acceptance evidence.

## Actual nominal qualification, 2026-10-09

A fresh packaged OTA completed the original full-tool nominal acceptance, then
characterization job `f9c6d69bba5f4fb5b61ad70fe1bafcbd` completed all ten real
pre/post analyses at `tt`, 1.8 V, 27 °C. Both explicitly specified gain ≥30 dB
checks passed. The other values below are observations, with no additional
performance target implied.

| Observation | Pre-layout | Post-layout |
|---|---:|---:|
| Differential gain at 1 Hz | 43.367634 dB | 43.368096 dB |
| First −3 dB bandwidth | 25,591.683 Hz | 25,361.149 Hz |
| Supply power | 4.392387 µW | 4.396283 µW |
| DC output | 0.706702 V | 0.706686 V |
| Midpoint input offset | 1.258036 mV | 1.258064 mV |
| Observed step settling | 28.49677 µs | 28.73677 µs |
| Integrated output noise | 6.499246 mV RMS | 6.492010 mV RMS |

Evidence is retained in `.validation/ota5-characterization/ota5-characterization.json`
and the referenced immutable run directories. A separate replay with the final
measurement implementation verified every rawfile hash and exactly reproduced
all ten measurement objects; `current-measurement-replay.json` records its
implementation hash. Source files, original nominal targets and device sizes
were preserved. These results do not qualify unspecified PVT points, mismatch,
distributed-RC extraction, closed-loop stability or manufacturing yield.

A separate eight-run PVT job `149466c60ae842b0af265adc393d0a5f` completed real
AC and OP analyses at two additional conditions, in both layout views. Each
explicit gain ≥30 dB check passed. Its retained report is
`.validation/ota5-characterization/ota5-pvt-characterization.json`.

| Condition | View | Gain | Bandwidth | Supply power |
|---|---|---:|---:|---:|
| ss, 1.62 V, 125 °C | Pre | 41.471673 dB | 36,528.390 Hz | 6.176628 µW |
| ss, 1.62 V, 125 °C | Post | 41.471711 dB | 36,203.246 Hz | 6.182750 µW |
| ff, 1.95 V, −40 °C | Pre | 43.453241 dB | 20,047.262 Hz | 2.927965 µW |
| ff, 1.95 V, −40 °C | Post | 43.453805 dB | 19,861.201 Hz | 2.930093 µW |

These additional cases cover AC and DC operating point only; their noise,
offset and transient behavior remains unqualified until those analyses are run.

A final actual AC negative-target control used the unchanged nominal circuit with
an explicitly requested 45 dB target. Job `d89118c203514ccabae0c1b1d9744fa4`
completed simulation successfully, measured 43.367634 dB, and correctly reported
`electrical_status: fail`. Its report is
`.validation/ota5-characterization/ota5-explicit-target-control.json`; it records
actual launcher and ngspice target hashes. The original ≥30 dB qualification
remains separate. A successful process is therefore not treated as success on
an unmet electrical target.


## Included-control regression

An independent review reproduced a real ngspice defect before the closure guard:
a project netlist's `.control` block could overwrite the supply-current vector,
write an ASCII rawfile, and quit before the generated driver executed. A linear
fixture physically consuming 3.24 µW then falsely reported 1.8 µW and passed a
2 µW target. The regression now rejects that exact source before creating any job.
Nested project and model override cases are also covered; failed validation does
not fall back to parsed log metrics or user-written raw values.

After this fix, actual SKY130 pre/post OP job
`d4c7e100f47948df8af13b23d506edb7` completed both analyses and exactly reproduced
the earlier 4.392387/4.396283 µW observations and DC output values. Its report is
`.validation/ota5-characterization/ota5-closed-closure-validation.json`. This
regression concerns ownership of the measurement program; ordinary native EDA
execution still operates trusted design languages as the local user.
