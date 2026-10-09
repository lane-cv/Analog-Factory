# Local language-model training and circuit evaluation

Analog-factory has an optional CPU language-model RL adapter and an independent
ngspice evaluator. This trains Qwen LoRA weights with TRL GRPO; the existing
parameter-search baseline remains a separate feature. The demonstrated pilot
**did not improve electrical success**: base and trained models each solved
1 of 32 held-out amplifier tasks, while random search solved 17.

## Actual qualification, 2026-10-08

The first pilot used Qwen/Qwen2.5-0.5B-Instruct, TRL 1.14.2, FP32 CPU execution,
LoRA rank 8 on `q_proj` and `v_proj`, two optimizer steps, and seed 42. Training
changed 540,672 trainable parameters with an aggregate update L2 norm of
0.01644373353502051. Step one had tied rewards and zero gradient; step two had
gradient norm 0.7452458739280701. These are observed values, not assumed updates.
The adapter was saved as safetensors and reloaded in a separate worker process.
The pilot took 264.27 seconds with peak worker RSS 3,435,880,448 bytes.

| Held-out policy | Electrical successes | Episodes with valid simulator calls | Mean reward | Actual ngspice calls |
| --- | ---: | ---: | ---: | ---: |
| Untouched base | 1 / 32 | 13 / 32 | −0.829172 | 26 |
| Reloaded trained adapter | 1 / 32 | 15 / 32 | −0.814424 | 30 |
| Seeded random search | 17 / 32 | 32 / 32 | 0.202287 | 150 |

The small reward/validity changes do not establish better circuit design. Invalid
or truncated language-model tool calls are retained and penalized. Electrical
feasibility is reported separately from producing a syntactically valid action.

All policies had the same 32 tasks and environment ceiling of three sizing
actions, with two independently simulated voltage/temperature conditions per
action: at most six ngspice executions per task. They did **not** consume equal
compute. The original LM experiment additionally limited completion to 96 tokens
and native tool interaction to two rounds. Random search has no language-model
generation cost. See the recorded limits before comparing algorithms.

The full comparison first reached its 1,800-second wall limit after completing
32 base, 32 random and 24 trained-policy episodes. That failed job remains intact.
A separate resume verified 342 evidence hashes, actual simulator inputs/logs,
independent scores, task membership, evaluator identity and completed token
trajectories, then ran the eight missing trained-policy episodes. Aggregate
elapsed time was 1,961.91 seconds, including the stopped attempt.

Portable small evidence lives in
[`examples/training/evidence`](../examples/training/evidence): numeric results,
training curves, a real token/action trajectory and the original evaluator text.
Full logs, waveform artifacts, optimizer state and checkpoints remain in the
ignored validation workspace. The JSON receipt identifies their project/job IDs.
The initial pilot predates automatic source-file snapshotting; its preserved
evaluator hash and complete model/checkpoint identities remain available.

A second amplifier qualification reproduced the exact same adapter safetensors
hash and update norm, then reproduced a recorded ngspice reward by replay. The
configurable **current-mirror** family also completed two genuine optimizer
steps: update L2 0.06408644236450649, 231.83 seconds, peak RSS 3,567,452,160 bytes.
Its separate-process reload and simulator replay passed. On its two-case smoke
evaluation, base and trained policies both solved 1/2 cases; this also provides
no improvement evidence. That job includes frozen source snapshots and permits
three LM tool rounds, matching its three-action environment ceiling. Its receipt
is `current-mirror-pilot.json` alongside the amplifier evidence.

## Install and run

The core application never imports torch or TRL. Install the optional runtime
only when training is wanted:

```sh
python3 scripts/setup_training.py
python3 scripts/validate_training.py --workspace .validation/training-reference
```

The setup uses project-local `uv` 0.12.23, CPython 3.12.15, the pinned CPU package
lock and a pinned Apache-2.0 pretrained model. It does not install system packages
or call paid APIs. Default storage is `.tools/training`; observed installation
size is approximately 2.3 GiB. The model revision is
`7ae557604adf67be50417f59c2c2f167def9a775` and its safetensors SHA-256 is
`fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe`.
The setup verifies the uv archive and model hashes; the Python dependency lock
pins versions rather than every wheel hash.

The validator creates a new project, invokes the registered training operation,
waits for actual weight changes and reload evaluation, then replays a recorded
random-baseline episode through real ngspice. It exits unsuccessfully if the
worker fails, no weights change, or replay changes the recorded reward. A model
can pass this execution qualification while showing no design improvement.
Use `--ngspice /path/to/ngspice` when the repository-local launcher is absent.

Training is bounded by a configurable wall limit, an 8 GiB worker RSS limit,
fixed action limits, and cancellation of the worker process group and active
simulator children. Memory measurements cover the supervised worker; they are
not whole-host accounting. Monetary cost is recorded as unknown. A project may
have only one active orchestration job at a time.

An actual cancellation check stopped the supervised worker in 11.45 seconds,
left no active simulator children, and confirmed that a synthetic credential
environment variable was excluded from the worker. The browser acceptance passed
real checkpoint/curve inspection, simulator replay, task publication, and
acknowledged remote commands through the same controller. Source hashes are
checked before and after worker phases; editing the training implementation
during a job invalidates that job rather than mixing evaluator versions.

## Completion budget and feedback

The training dialog and `training.start` / `training.evaluate` share
`options.max_completion_length`: an integer from 96 to 2048 total completion
tokens, including tool observations and framing. The default remains 96 for new
jobs. For example, a new checkpoint evaluation can select:

```json
{"project_id":"<project-id>","checkpoint_job_id":"<checkpoint-job-id>",
 "options":{"max_completion_length":768,"eval_tasks":4,"wall_seconds":1800}}
```

Agents can prepare the same value through `ui.training.set_options` on a connected
client. Editing other options preserves this draft. Reports show recorded limits
and actual existing token logs; missing measurements are not reported as zero.

A resume preserves the original budget, seed and task identity. Historical records
without a budget normalize to 96; changing the budget requires a new evaluation.
The adapter's actual scripted acceptance confirms that 768 retains three real
ngspice observations where 96 drops the first. This does not demonstrate better
model decisions. See [feedback budget validation](training-feedback-budget.md)
for exact evidence, reproduction and the remaining paired held-out experiment.

## Tasksets and independent scoring

`training.taskset.validate/create/list/get` manage immutable, content-addressed
project tasksets. Publication requires `configure` permission; the policy has
only its bounded simulation tool and cannot edit scoring code, decks, tasksets
or split membership. `training.start` selects an approved `taskset_id` and writes
a frozen copy into its request. The original `educational-amplifier-v1` remains
available for reproducing the qualification.

Two trusted circuit families are supported:

| Family | Actions | Independently measured behavior |
| --- | --- | --- |
| Common-source amplifier | Drain resistance, gate bias, NMOS width | AC gain, DC output voltage, supply current |
| Two-transistor current mirror | Reference width, output width, common channel length | Output/reference current ratio, gate compliance and current limit |

Numerical grids, supply/load/temperature conditions, targets, hard constraints,
objective weights, simulator-call penalty, action budget and split counts are
configurable within validated physical bounds. A seed deterministically shuffles
distinct condition/target combinations into independent train/validation/test
splits. Defaults contain 64/16/32 cases. Duplicates, insufficient task domains,
nonfinite values, unknown fields and executable model/scoring paths are rejected.
The validation split is reserved; this bounded pilot selects the final checkpoint
at a fixed step count and does not tune on validation or test outcomes.

Each episode uses fresh `.af/episodes/<job>/<index>` files and registered
`run.start/get/cancel` operations. Every action runs all configured paired supply
and temperature conditions. The worst independently scored condition contributes
the scalar reward, and success requires every hard constraint in every condition.
Reports preserve constraints, normalized objective penalties, simulator failures,
raw metrics and costs separately. Missing/nonfinite measurements cannot pass.

The MOS model is a documented educational level-1 model. These voltage/temperature
probes are **not foundry process corners or mismatch Monte Carlo**. SKY130 manual
design qualification is a separate [OTA reference](ota5-reference.md).

Examples are in [`examples/training`](../examples/training). To exercise the second
family with actual language-model training:

```sh
python3 scripts/validate_training.py --family current_mirror \
  --workspace .validation/current-mirror-training
```

## Browser and operation parity

Open **Evaluation → Model training**. The dialog displays resources and explicit
unavailable-framework reasons, task selection and validated task publication,
bounded training options, job status/cancellation, reward curves, exact checkpoint
hashes, held-out comparisons and replay. It calls `AFOperations.invoke`; semantic
`ui.training.*` commands use the same controllers and versioned browser state.
Publishing tasksets through a browser command also requires configuration rights.

The backend operations are `training.resources`, `training.tasksets`, the four
`training.taskset.*` operations, `training.start/evaluate/get/list/cancel/replay`.
They are exposed through the shared HTTP, CLI and MCP operation adapters.
Use a running server for asynchronous CLI requests so it owns the worker lifetime.

`training.evaluate` accepts an optional `resume_job_id` for a stopped incomplete
local checkpoint evaluation. It verifies the original checkpoint, tasks,
evaluator, completed trajectories, seeds and simulator evidence; imported records
cannot authorize resume. It preserves the stopped job and costs, creates a fresh
bounded job, and runs only missing cases. The current implementation permits one
resume of an original evaluation, not resume chains. Source edits invalidate
resume qualification instead of silently reusing results.

`training.replay` executes only validated numerical actions through the current
trusted circuit implementation. It never executes archived commands, loads
optimizer pickle files, or runs a language model. Checkpoint evaluation only
loads a verified local safetensors adapter onto the fixed pretrained model.

## Framework availability and provenance

Agent Lightning, AReaL and AgentFly adapters are explicitly reported as not
integrated on this CPU host. Current upstream capabilities, licenses and GPU
requirements were checked in the dated
[framework research](training-integration-research.md). TRL was chosen because
its real GRPO trainer supports local Transformers generation, CPU execution and
native tool environments. The adapter records exact prompt/completion tokens,
masks, environment-tool masks, pre-update log probabilities, advantages, actions,
rewards, task hashes and simulator run IDs. Environment tokens are excluded from
the policy loss by TRL's tool mask. [Pinned trainer source](https://raw.githubusercontent.com/huggingface/trl/v1.14.2/trl/trainer/grpo_trainer.py).

Workers use an explicit environment whitelist, offline model loading, disabled
telemetry/W&B, and `trust_remote_code=False`. Provider credentials are not copied
into training artifacts. Base model files are global optional resources and are
not part of project backups. Checkpoints contain safetensors, tokenizer/config
data and local training arguments; optimizer/RNG state is retained as evidence
but never unpickled during checkpoint evaluation or trajectory replay.

Framework and model licenses: [TRL Apache-2.0](https://github.com/huggingface/trl/tree/v1.14.2),
[Qwen model card and Apache-2.0 terms](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct).
