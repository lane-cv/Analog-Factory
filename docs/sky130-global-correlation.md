# Installed SKY130 global correlation diagnosis

The installed `mc` section remains rejected for global-process batches. Controlled
ngspice 46 probes on 2026-10-09 identified a simulator/model interaction, and a
temporary parameter rewrite restored correlation in a small model subset. This
diagnostic is not a live qualification certificate and does not enable a mode.

## Cause and discriminating experiment

The [ngspice 46 parser source](https://raw.githubusercontent.com/imr/ngspice/ngspice-46/src/frontend/inpcom.c)
calls `inp_fix_agauss_in_param` for the statistical functions. That pass changes
random `.param` assignments into `.func` definitions before macro expansion.
References to such parameters can therefore contain a fresh random call instead
of a cached value. Changing launcher compatibility flags does not bypass this
unconditional pass. This behavior was also discussed by the ngspice maintainers
in their [parameter-evaluation investigation](https://sourceforge.net/p/ngspice/discussion/133842/thread/c13bbd10c6/).

Two independent voltage sources using the same top-level
`.param global_draw = agauss(1,.1,1)` measured **0.9679620739119652** and
**0.8659874845287817 V** at seed 41. Repeating seed 41 repeated both values;
seed 42 changed them. Putting the random call in a named wrapper function and
assigning its result to the global parameter gave **0.9679620739119652 V** at
both sources. The wrapper expands after the parser's random-parameter pass,
allowing the global `.param` to retain one evaluated value.

In the installed SKY130 continuous model, `sw_polycd` and `sw_activecd` have
top-level random expressions. The n-channel and p-channel device subcircuits
define their own model cards using these parameters for `lint` and `wint`.
The expanded seed-41 n-channel models in a six-file subset showed:

| Variant | Device | `lint` (m) | `wint` (m) |
|---|---|---:|---:|
| Original expressions | XN1 | 1.099636794604437e-8 | 2.315005452784535e-8 |
| Original expressions | XN2 | 9.991695462257269e-9 | 1.294597434945742e-8 |
| Cached global expressions | XN1 | 1.241256889132052e-8 | 1.515837422643909e-8 |
| Cached global expressions | XN2 | 1.241256889132052e-8 | 1.515837422643909e-8 |

The experimental rewrite changes only the ten random expressions in the flat
`continuous/parameters_fet_tt.spice` file. It keeps their formulas, distributions,
and process switches, places the complete right-hand expression in braces, and
calls named Gaussian wrappers. Transistor model files and their local mismatch
expressions retain their original contents. Samples consume random numbers in a
different order, so corresponding seeds need not produce the same current before
and after the rewrite.

The subset uses the three original continuous parameter files, original global
model file, and exact original `nfet_01v8` / `pfet_01v8` model files. It retains
the production probe's geometry, bias, temperatures, seed ordering, and current
measurements. Five probes per variant exercise nominal seeds 41/42 and process
seeds 41/41/42. The original subset fails only requested pair correlation;
the cached variant passes all six existing checks. The comparison uses the
unchanged **2e-6 relative / 1e-12 A absolute** matching tolerance.

## Reproduction and retained evidence

Run from the repository root with a new output directory:

```bash
python3 scripts/probe_sky130_global_correlation.py \
  --library .tools/pdks/sky130/1689ac3f2dc763876eaf967227c7dfe831b031ae/sky130A/libs.tech/combined/sky130.lib.spice \
  --simulator .tools/eda/bin/ngspice-sky130 \
  --binary /usr/local/bin/ngspice \
  --output /tmp/af-sky130-global-diagnosis
```

The default performs sixteen small real simulator runs. `--full-library` adds
one seed-41 `mc` sample for each full-library variant. This optional comparison
copies public model files into the evidence directory and redirects relocated
legacy includes to their unchanged installed files. It never edits the installed
PDK. Every process has a 180-second maximum; `-n` excludes personal initialization
files. Logs, generated decks, script/model/simulator hashes, measurements, and
source-unchanged checks remain in the output directory. Existing output paths are
rejected, and missing measurements or process failures make the command fail.

The first full-copy experiment failed because its legacy includes expected the
original `libs.ref` directory layout. That failure remains in
`.validation/sky130-global-correlation-20261009-full/evidence.json`; it supplies no
correlation evidence for the copied variant. The corrected diagnostic records
the recursively hashed original include closure and hashes each file whose
external include paths were relocated.

The completed full comparison is retained in
`.validation/sky130-global-correlation-20261009-full2/evidence.json`:

| Full `mc` seed-41 variant | N1 (A) | N2 (A) | P1 (A) | P2 (A) |
|---|---:|---:|---:|---:|
| Installed original | 1.187526261600159e-5 | 1.200714507466547e-5 | 2.698885781949982e-5 | 2.128178239958062e-5 |
| Experimental cached globals | 1.223211984669251e-5 | 1.223211984669251e-5 | 2.145767163035366e-5 | 2.145767163035366e-5 |

Both runs completed: 54.573 seconds for the original and 38.434 seconds for the
temporary copy. The original reproduces the earlier application result exactly.
The evidence hashes 278 original model files and 26 files with relocated external
include paths. The original library SHA-256 is
`48de7c677e2c6e7d09b2559279de9f818be71010a4aa933d728eb4db3b133c84`;
the launcher hash is
`2bcf5276073c5ab6732be715781de617dac57d323b43b8cabe6367865434ab9d`,
and the ngspice target-binary hash is
`5fd4ed85c2f84f152b772749894fb6a4693ef9a2fb0630679832ab09dcbd7b66`.

The diagnostic subsequently tightened its transformation to preserve brace
grouping as parentheses. The final script's sixteen small probes are retained in
`.validation/sky130-global-correlation-20261009-final-subset/evidence.json`; the
full comparison retains the script and transformation hashes used for those
earlier completed processes. All unchanged model source checks passed.

## Qualification limits

This identifies why matching fails and provides a controlled model-loading repair.
It does not certify the installed `mc` section, calibrate distributions, establish
yield, or qualify other devices, geometries, biases, or temperatures. Several
continuous-model process parameters remain fixed at zero with their alternative
statistical declarations commented out; pair matching cannot validate the
completeness of the process distribution. Existing qualified mismatch behavior is
a separate result.

## Explicit experimental qualification and sampling

The registered `simulation.statistics.qualify` operation accepts
`model_semantics: cache_global_fet_draws` for `kind: global_process`, `section: mc`.
Omitting the field uses `installed`; there is no automatic rewrite after a failed
qualification. The same explicit field is required in `batch.statistics` when a
batch uses the resulting certificate. Installed and adapted qualifications cannot
substitute for one another. The capabilities operation advertises the choice and
marks it experimental.

The adapter reads the original configured library and its bounded static include
closure. It produces project-relative copies only in immutable simulator run
snapshots, caches global FET expressions in the one supported parameter file,
and relocates static include paths within that copied closure. It preserves the
installed PDK, process/mismatch switches, and local device mismatch expressions.
Dynamic includes, symlinks in the copied dependency closure, unsupported control
or external-code directives, and missing dependencies are rejected.

Qualification runs the same five actual probes, then verifies generated model
hashes in every run input, original dependency hashes, and unchanged simulator
identity. Certificates additionally bind the exact semantics, adapter algorithm
and implementation hash, original model closure, and generated model hashes.
They remain server-session authority only. Each batch reconstructs the same
bundle, rejects semantic/source/tool changes, and rechecks original model hashes
before each snapshot. Circuit sources cannot redefine process parameters or
model functions.

The adapter limits its closure to 512 files and 16 MiB. The existing aggregate
32 MiB generated-input limit still applies: this installed generated closure is
3,542,669 bytes, so at most nine small-deck scenarios fit, below the general
128-scenario maximum. Qualification snapshots together must also fit the
aggregate limit. This bounded implementation does not establish a distribution,
yield, or foundry signoff.

The repeatable live driver creates an isolated public validation project and
performs qualification followed by two fresh sample runs:

```bash
python3 scripts/validate_sky130_global_adapter.py \
  --library .tools/pdks/sky130/1689ac3f2dc763876eaf967227c7dfe831b031ae/sky130A/libs.tech/combined/sky130.lib.spice \
  --simulator .tools/eda/bin/ngspice-sky130 \
  --output /tmp/af-sky130-adapted-qualification
```

Normal regression tests exercise a small declared simulator fixture. They are
tests of the operation and parser contract, separate from this actual-PDK driver.

### Final actual registered-operation evidence

The final frozen helper was exercised on 2026-10-09 in
`.validation/sky130-global-adapter-20261009-final/evidence.json` using seven real
full-library ngspice runs. Qualification job
`e9f43a4fb59b499da169c0acaacbe9bf` completed in 118.736 seconds and passed finite
positive currents, nominal seed invariance, nominal matching, same-seed
repeatability, both-family seed sensitivity, requested global correlation, and
unchanged simulator identity. Its 216 original dependencies and 216 generated
model inputs were hash-bound; generated copies retain all resolved library
sections and their static dependencies.

Seed 41 repeated n-channel pair currents of `1.223211984669251e-5 A` and
p-channel pair currents of `2.145767163035366e-5 A`. Seed 42 changed both pairs
to `1.185793146688853e-5 A` and `2.070495269152536e-5 A`, respectively. Nominal
seeds 41 and 42 remained invariant. The unchanged original library's failed
global correlation is the separately retained negative control above.

Fresh batch job `ddbed74fcc774955872025f79d49092c` completed two additional OP
runs in 59.806 seconds. Seeds 73 and 74 measured drain-supply powers of
`1.066218e-5 W` and `1.068308e-5 W`. Reusing the adapted certificate with default
installed semantics was rejected, and the project source remained unchanged.

The final helper implementation SHA-256 is
`04d8d37b80338aee58c0de84acb36692255093ebb9bf3e716ecc73d91e91e6a8`;
the full adapter identity SHA-256 is
`ac0502937c6cd9d227cf2a4c8590ac76a77fb18b46acb3a3656bc6db999c670e`.
An earlier completed registered validation remains in
`.validation/sky130-global-adapter-20261009/evidence.json`, along with an explicit
comparison showing unchanged generated model bytes after the final rejection
guard was added. Its older implementation identity does not authorize the final
helper. Validation server shutdown also ends live certificate authority; normal
users must run qualification again in their own current server session.
