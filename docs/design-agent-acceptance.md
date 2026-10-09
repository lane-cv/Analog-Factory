# Live design-agent acceptance

`scripts/validate_design_agent.py` exercises the installed, authenticated Codex
CLI through the same registered operations as the UI. It creates a new isolated
public SKY130 project, gives the model an empty five-MOS OTA interface, and asks
the model to choose connectivity and dimensions. The driver never supplies a
finished OTA design or repairs the model's circuit behind its back.

```bash
python3 scripts/validate_design_agent.py \
  --settings .validation/settings.json \
  --workspace .validation/design-agent-new-trial \
  --runtime 900 --max-actions 12 --max-runs 4
```

The workspace must be new and outside `.workspace`. Existing evidence is never
overwritten. Only public EDA tool/PDK settings are loaded. Codex owns its existing
authentication; the driver neither reads credentials nor changes global config.
Public `--version`, `login status`, and registered `agent.models` establish
prerequisites. Unless supplied explicitly, the model and effort are the defaults
advertised by that native catalog, selected explicitly for the session.

The automatic grant allows only `file.write` and `run.start` on three explicit
paths: `netlists/agent.spice`, `testbenches/agent-ac.cir`, and
`testbenches/agent-dc.cir`. The current grant schema shares paths between these
two operations, so it technically permits writing the testbenches. The prompt
requires those harnesses to remain unchanged, and the driver compares their
exact contents before any independent verification; modifying either fails
acceptance. Agent calls beyond the grant or budget wait for human approval in
the ordinary API. This unattended driver cancels them, never approves them.

Native automatic sessions consume the same durable human-issued grants as API
sessions. Native filesystem access stays read-only, direct shell/tools stay
disabled, and all mutations pass through the authenticated project-scoped MCP
bridge. Missing or forged grants cannot start a turn. Conversation tool-result
display previews are bounded to 16 KiB with explicit truncation metadata; the
actual MCP result delivered to the model and the original run artifacts remain
unchanged. Ordinary assistant output still has the 1 MiB conversation limit.

The model runtime has both the requested outer deadline (at most 900 seconds)
and the native adapter's existing stricter 300-second turn timeout. The prompt
requests sequential simulator runs with 180-second limits because this prepared
PDK has substantial startup cost. Cancellation drains the model and cancels any
remaining EDA children through `run.cancel`. Independent verification permits
at most two further runs, each limited to 180 seconds, plus bounded cleanup.

Electrical acceptance requires a changed source consisting of exactly three
public SKY130 NMOS and two PMOS devices, with the fixed OTA interface, explicit
finite dimensions, and no injected analyses, sources or measurements. A changed
valid design receives fresh independent AC/DC simulations, even when the model
session failed; a failed or cancelled session still cannot pass acceptance.
Simulator status, exit code, source hashes, staleness and waveform artifacts
must agree with the final design. The driver reconstructs the gain from complex
`v(out)/(v(inp)-v(inn))`, confirms the actual 1 V differential stimulus, finds a
downward unity-gain crossing, and reconstructs output bias and supply current
from DC waveform data. Targets are gain above 30 dB at 1 Hz, output bias inside
0.2–1.6 V, and supply current magnitude below 100 µA at TT/27 °C, VDD=1.8 V,
input common mode=0.9 V, external bias=0.65 V and CL=1 pF. This is schematic
electrical design acceptance; it does not establish layout, PVT or signoff.

The retained `design-agent-validation.json` includes backend/model/effort,
native/session/turn/run/trace IDs, limits, grant counters, usage when supplied,
source revisions and hashes, failed agent runs, fresh independent run manifests,
operation events and the electrical verdict. Native login availability, model
transport, successful tool execution, and electrical pass are distinct facts.
Model prose is never a pass condition.

`python3 -m unittest tests.test_design_agent_validation` tests rejected fabricated
success, missing/failed/stale evidence, invalid source, cancellation, exhausted
budgets, failed prerequisites, native grants, output bounds, full artifact
retention and child cleanup. Its synthetic waveform records and fixture native
transport are contract tests, **not live-model acceptance**.

The first actual trial on 2026-10-09 is retained at
`.validation/design-agent-live-20261009-a/design-agent-validation.json`. Native
Codex `gpt-6.1-sol`, effort `low`, authored a five-device OTA and consumed five
actions including four real simulator starts. The first two runs timed out at
60 seconds under concurrent EDA load; repeated full run records then exhausted
the conversation event budget after 73 events. The native session failed and no
electrical pass was claimed. The remaining two runs were recovered and stopped
through registered run inspection after the old driver exited. This reproduced
the bounded-display and child-cleanup fixes above. A subsequent trial uses the
fixed driver and retains its separate report.

The subsequent actual trial passed:
`.validation/design-agent-live-20261009-b/design-agent-validation.json`. Session
`6e57534ea91348d9a04f0e64965dec0c` used native `gpt-6.1-sol`/`low`, three granted
actions and two sequential simulator runs. The model chose L=2 µm, W=10 µm for
the input pair, W=20 µm for the PMOS mirror, and W=4 µm for the tail. Source SHA256
changed from `f73d0903f69d4a0930f2db8dbbc9beb1215a8437d85029be434df7a5cee3515f`
to `52a70620be58300c59aed657c8c78d41e8efe1c4b5800ce90117b2003deb7889`.
Independent AC run `37bdd75760ba4a5eb1c1cac39d3833ac` and DC run
`5e500f315c7f4fc0b13557ddd61c0bdc` both succeeded with matching final source hashes
and `stale=false`. Total driver elapsed time was 306.22 seconds.

| Independently reconstructed metric | Result | Required target |
| --- | ---: | --- |
| Differential gain at 1 Hz | 42.78024797 dB | >30 dB |
| Downward unity-gain crossing | 4.10581655 MHz | Actual positive crossing |
| DC output | 0.75965893 V | 0.2–1.6 V |
| Supply current | −2.45978363 µA | −100–0 µA |
| Supply power | 4.42761054 µW | Reported observation |

This trial still exposed an efficiency gap: native usage reported 901,871 input
tokens, including 844,800 cached input tokens, plus 2,060 output tokens and 173
reasoning-output tokens. Display clipping protects persistence but cannot reduce
the actual model context. This cost is not an acceptable steady-state polling
pattern.

Compact polling now uses the same authoritative reads with a bounded projection:
`run.get` accepts `view: "summary"`; `get_operation_status` accepts the same view
and defaults to it for shared agent tools. Legacy registry/HTTP/CLI defaults stay
`full`, with HTTP `?view=summary` and CLI `operations status ... --view summary`
available explicitly. Native and API guidance requests compact polling and a
single full provenance fetch after completion. Summaries retain actual status,
exit code, metrics, verification/staleness, explicit electrical verdicts, termination
and completed/scenario counts, bounded qualification state and progress/error text, manifest
hashes and full-record/artifact operation references. Metric names and artifact
paths are never shortened into different references; extra items are omitted
with explicit counts. Summaries cap metric count at 32, artifact references at
eight, and text at 512 UTF-8 bytes. Complete records remain retrievable.

Eight projection contract/integration tests cover a 278-dependency manifest,
source changes, electrical failure despite successful execution, size limits, cross-project permissions, invalid views, full-view
compatibility and real HTTP/MCP/CLI transport. A read-only replay of the live
independent AC record reduced its serialized data from 66,853 bytes to 1,697
bytes with identical status, metrics and staleness. No additional paid model
trial was run after this change; its actual model-token savings remain unmeasured.

The independent acceptance runs now reuse characterization's closed-input guard
before execution. It rejects unresolved or ambiguous include provenance, nested
project analysis/control overrides, and model includes outside the validated PDK
closure. A new rejection/acceptance contract test passed after reproducing the
missing guard. This is a SPICE input validation boundary, not an OS sandbox.
The original live report remains unchanged. A separate read-only replay at
`.validation/design-agent-live-20261009-b/current-input-closure-replay.json`
validates both retained independent run snapshots against the current guard,
records its implementation hash, and verifies the final netlist and all 216
external dependency hashes still match the actual run evidence. This replay used
no model call and did not rerun electrical simulations.
