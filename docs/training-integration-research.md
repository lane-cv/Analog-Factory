# Training integration research

Research date: 2026-10-08. This is a feasibility assessment and implementation
proposal for C07/C08, not evidence of completed training. No ML packages or model
weights were installed during this assessment. The subsequent implementation,
actual CPU weight update, saved/reloaded checkpoint, and full held-out results
are documented in [local training](training.md). The selected CPU path is now
qualified for execution; the initial pilot showed no electrical-success improvement.

The acceptance criteria come from section 7 and the final validation requirements
in [the original specification](../analog-factory-project-prompt-en.md), and
[C07/C08 in the completion ledger](completion-plan.md). Parameter search and chat
trajectory export alone do not satisfy model training.

## Observed host resources

Read-only checks used `lscpu`, `free -h`, `df -h`, PCI/device discovery, Python
executable discovery, and `importlib.metadata`; no credentials were inspected.

| Resource | Observed |
| --- | --- |
| CPU | Intel Core i5-1135G7, 4 cores / 8 threads, x86_64, AVX2 |
| Memory | 14 GiB total; approximately 11 GiB available at inspection; 4 GiB swap |
| Accelerator | Intel Tiger Lake Iris Xe integrated graphics; no NVIDIA device or `nvidia-smi` |
| Repository disk | Approximately 740 GiB available |
| `/tmp` | tmpfs, 7.5 GiB total; unsuitable for the ML environment/model cache |
| Python | System Python 3.14; Python 3.12 and `uv` not found in checked executable locations |
| ML packages | No installed torch, transformers, accelerate, trl, datasets, peft, numpy, scipy, ray, vllm, triton, agentlightning, areal, agentfly, gymnasium, or bitsandbytes in system Python |

The pinned ngspice launcher and real OTA reference already work; see
[local tools](local-toolchain.md) and [OTA evidence](ota5-reference.md). SKY130
combined-library startup took roughly 30–45 seconds per fresh process during the
OTA work. A lightweight educational MOS model is appropriate for the first
training experiment; it must be labeled accordingly and is not foundry evidence.

## Framework comparison

Version observations are dated, not floating production dependencies. Resolve
full source revisions and lock transitive dependencies before qualification.

| Candidate | Current upstream evidence | Integration and execution fit | Status on this host |
| --- | --- | --- | --- |
| Agent Lightning | v1.0.2, September 29; MIT; v1 was rewritten in August 2026. [Releases](https://github.com/microsoft/agent-lightning/releases), [metadata](https://raw.githubusercontent.com/microsoft/agent-lightning/main/pyproject.toml) | Gateway records model requests; controller runs the existing agent harness; trainer uses verl/vLLM. Multi-turn and asynchronous collection are documented. Connecting Workspace requires a rollout/reward bridge, preserving sampled tokens and cancellation. [Architecture](https://github.com/microsoft/agent-lightning) | GPU training unavailable. Official setup requires NVIDIA CUDA 12.9/13.0; recommended stack is verl 0.8.0, vLLM 0.20.2, FlashAttention 2.8.3. Its `verl-cpu` dependency group explicitly serves lint/type checking, not training. [Installation](https://raw.githubusercontent.com/microsoft/agent-lightning/main/docs/00-installation.md), [CPU group](https://raw.githubusercontent.com/microsoft/agent-lightning/main/pyproject.toml) |
| AReaL | v2.1.0, August 25; Apache-2.0; Python 3.11–3.12. [Releases](https://github.com/areal-project/AReaL/releases), [metadata](https://raw.githubusercontent.com/areal-project/AReaL/main/pyproject.toml) | Async/sync GRPO, PPO, RLOO and other algorithms; multi-turn agents; SGLang/vLLM inference and FSDP/Megatron training. Requires rollout workflow/service integration and token/reward provenance. [Support matrix](https://github.com/areal-project/AReaL) | Documented full training stack needs CUDA hardware. The published 8×H800/1 TB host is a tested configuration, **not a minimum**. Installing without CUDA is not proof of a supported CPU training run. [Installation](https://raw.githubusercontent.com/areal-project/AReaL/main/docs/en/tutorial/installation.md) |
| AgentFly | Package metadata 0.1.0, Apache-2.0, Python 3.12 only; no GitHub releases listed when inspected. README news includes April 2026 additions. [Metadata](https://raw.githubusercontent.com/Agent-One-Lab/AgentFly/main/pyproject.toml), [releases](https://github.com/Agent-One-Lab/AgentFly/releases), [repository](https://github.com/Agent-One-Lab/AgentFly) | Decorated tools/rewards and `BaseAgent`/`HFAgent`; verl training with multi-turn token masks, GRPO/Reinforce++, async tool execution. Workspace tools fit its interface; resource and token adapters still need implementation. [Training](https://agentfly.readthedocs.io/en/latest/api_references/training/training/) | NVIDIA training stack unavailable here. Metadata pins vLLM 0.19.0; examples target GPU hosts. Agent-only execution is a separate capability. Pin a commit plus verl submodule rather than assuming the package version identifies the source. [Installation](https://raw.githubusercontent.com/Agent-One-Lab/AgentFly/main/docs/start/installation.md) |
| Hugging Face TRL | v1.14.2, October 6; Apache-2.0. Latest patch fixes generation/token-boundary training errors. [Release](https://github.com/huggingface/trl/releases/tag/v1.14.2), [metadata](https://raw.githubusercontent.com/huggingface/trl/v1.14.2/pyproject.toml) | GRPO accepts independent reward functions and stateful tool environments. Local Transformers generation avoids vLLM; sync/async tools are supported. [GRPO](https://huggingface.co/docs/trl/grpo_trainer) | Selected **CPU qualification candidate**, not yet installed or proven here. Transformers exposes `use_cpu`; GPU-specific extras are unnecessary for this proposed route. [Trainer configuration](https://huggingface.co/docs/transformers/main_classes/trainer) |

Agent Lightning is the preferred subsequent GPU adapter because its current
gateway/controller separation can retain the existing Workspace harness. Its
default examples upload logs and trajectories to W&B: an integration must disable
external reporting before local-only use. Historical pre-v1 adapter examples
must not be presented as current v1 interfaces. See the upstream
[installation guide](https://raw.githubusercontent.com/microsoft/agent-lightning/main/docs/00-installation.md).

## Proposed first real model update

Use **TRL 1.14.2 GRPO with LoRA on Qwen/Qwen2.5-0.5B-Instruct**. This is a
pretrained causal language model whose adapter weights receive policy gradients
from real simulator rewards. Qwen2.5 is listed among TRL's tested tool-loop
families. Its Apache-2.0 model repository contains a 988 MB safetensors file;
the observed repository revision prefix is `7ae5576`. Resolve and record the
complete revision before downloading. [Model](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct),
[files](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/tree/main).

Start with `use_cpu=True`, `use_vllm=False`, FP32, no mixed precision, one process,
one training microbatch, two completions per identical task, and accumulation
compatible with that group size. Use a small LoRA rank (initial proposal: 8 on
attention projections), bounded context and generation, and four tool turns.
Set reporting off and use local checkpoints. The group size, learning rate,
context limits and adapter targets are experiment configuration, not qualified
defaults. TRL's current code excludes external tool tokens from the loss and can
use a disabled PEFT adapter as the reference, avoiding a second full reference
model. [Pinned trainer source](https://raw.githubusercontent.com/huggingface/trl/v1.14.2/trl/trainer/grpo_trainer.py).

A 0.5B FP32 model alone needs approximately 2 GB for weights (parameter-count
arithmetic, **not measured peak RAM**). Activations, logits, optimizer state and
generation caches add to this. LoRA makes this plausible within the host's RAM,
but speed and peak memory require a pilot. Do not infer CPU training availability
from imports or model inference alone.

SmolLM2-135M-Instruct is a smaller genuine pretrained fallback (269 MB
safetensors, Apache-2.0, documented CPU inference). Its model card describes
function calling specifically for the 1.7B variant, so do not assume its 135M
chat template supports TRL's native tool loop. It would need a qualified bounded
text-action rollout adapter preserving `env_mask`, token IDs and log probabilities.
[Model card](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct),
[files](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct/tree/main).

## Analog environment and fixed evaluation

Use a resistor-loaded common-source amplifier with a fixed, documented educational
MOS model. The initial task varies supply, load, initial sizing/bias and requested
gain; actions change only `RD`, gate bias and transistor width on declared grids.
For example, bound RD to 2–20 kΩ, gate bias to 0.65–1.1 V, and width to 2–20 µm
with fixed length. These ranges must first be checked with real ngspice to produce
a mix of feasible and infeasible cases; they are not assumed electrically valid.

Every episode has a new workspace derived from immutable task assets. The model
receives target constraints, current parameters, measured gain/current/DC output,
the previous action/result and remaining budget. Expose only typed tools such as
`set_parameter`, `simulate`, and `finish`; validate finite values, grid membership,
parameter names and bounds outside the model. Do not expose shell access, scoring
code, taskset writes or arbitrary file edits to the policy. EDA execution must
flow through registered operations and retain their run IDs and artifacts.

Keep these evaluator outputs separate:

- Hard constraints: valid/finite measurements, convergence, allowed bias range,
  current ceiling and all required corner checks.
- Objectives: normalized gain deficit, target bias distance, and power relative
  to the task's declared targets.
- Failures: invalid action, nonconvergence, timeout, missing metric, tool failure,
  exhausted budget and deliberate completion.
- Costs: actual simulator calls, elapsed time, policy tokens and wall time.
  Local monetary cost is unknown unless explicitly configured; never invent it.

An initial reward proposal is: invalid/failing episode = −1; otherwise
`1[all hard constraints pass] - 0.5*gain_deficit - 0.25*bias_error -
0.25*power_excess - 0.02*simulator_calls`, with the three normalized penalties
clipped to [0,1]. Freeze the exact evaluator and taskset hashes before training.
The scalar is only the optimizer's input; reports retain every component and raw
metric. Missing data never becomes a favorable zero. Terminate on independently
verified success, unrecoverable failure, four tool turns, simulation budget, or
wall-clock budget. PVT robustness uses the worst required case, not an average
that hides violations.

For the educational model, voltage/temperature sweeps and explicitly specified
parameter perturbations can test robustness, but must not be called foundry
process corners or mismatch Monte Carlo. A separate qualified SKY130 task can
subsequently use the installed TT/SS/SF/FS/FF sections. The presence of `_mm`/`mc`
sections alone does not qualify seeded statistical sampling.

Prepare at least 64 train tasks, 16 validation tasks and 32 sealed test tasks,
with split membership fixed before optimization and disjoint target/load/supply
combinations. Check feasibility with evaluator-only exploratory runs; exclude
their solutions from policy observations. Keep the test set out of prompt design,
checkpoint selection and reward tuning. Training length initially has a modest
fixed budget (for example 32 optimizer updates), then increases only based on
validation and resource measurements.

Evaluate the untouched base model, the selected reloaded checkpoint and seeded
random search on exactly the same sealed tasks, with equal simulator/action
budgets. Report success count and rate, objective distributions, constraint
violations, invalid actions, total calls, latency and paired per-task changes.
Use multiple fixed sampling seeds and confidence intervals where sample size
permits. Report negative or inconclusive results honestly; a changed checkpoint
is evidence of training, not evidence of better design performance.

## Required training records and acceptance gates

Each trajectory needs task/split/episode IDs, source/config/evaluator hashes,
model and tokenizer revisions, adapter checkpoint ID, rendered prompts, exact
prompt/completion token IDs, attention and policy-loss masks, sampled-action log
probabilities, tool call boundaries/arguments/results, rewards/advantages, terminal
reason, simulator run IDs, seeds and actual resource usage. Tool observations
remain context but never become model-generated targets. Preserve original token
IDs instead of retokenizing conversation text. Validate that each GRPO group
shares the same task and initial state.

The first qualification run must establish all of the following:

1. A fresh optional environment imports the pinned stack and loads a revision-pinned
   safetensors model without remote code. Record the full resolved lock and hashes.
2. At least one multi-turn rollout changes a parameter, executes real ngspice and
   uses the returned measurement in a subsequent action. Test cancellation and
   timeout propagation to simulator children.
3. A small batch has different independent rewards, nonzero trainable gradients,
   and an actual optimizer update. Compare adapter tensors before and after;
   log update norm and trainable parameter count. All-identical rewards can yield
   zero advantages and must not be counted as learning.
4. Save adapter weights, optimizer/scheduler/RNG state and base-model identity;
   reload in a new process and reproduce deterministic evaluation. Record actual
   checkpoint file hashes and measured RAM/time, not just an output directory.
5. Produce immutable held-out before/after/random-baseline reports and training
   curves from actual runs. If improvement is absent, preserve that result.
6. Exercise the registry/browser path for tasksets, framework/model resources,
   run status, cancellation, curves, checkpoints, replay and evaluation reports.

## Dependency and implementation boundary

Keep the CPU trainer in an optional dedicated environment under `.tools/training/`
and caches on the repository disk, outside `/tmp` and outside user designs. Prefer
isolated Python 3.12 for compatibility with later adapters. TRL 1.14.2 metadata
requires Transformers >=4.56.2; its environment interface needs Transformers
>=5.2.0 and tool response parsing additionally needs `jmespath`. Resolve a
compatible exact set of CPU PyTorch, Transformers, Accelerate, PEFT and Datasets
versions and record wheel hashes before installation. Do not install the full
development, CUDA, vLLM, Ray or container extras for the CPU pilot. [Dependencies](https://raw.githubusercontent.com/huggingface/trl/v1.14.2/pyproject.toml),
[environment API](https://huggingface.co/docs/trl/grpo_trainer).

Set an initial 8 GiB process-memory ceiling and 30-minute pilot wall budget,
leaving the core EDA workspace usable; these are proposed limits, not measured
requirements. Benchmark two complete updates before scheduling the larger run.
If the host cannot execute the real update within its budget, mark CPU training
unavailable with the measured reason, retain collection/replay/evaluation, and
leave C08 incomplete. A GPU framework option remains unavailable until its
hardware and full training path pass qualification; an empty settings panel or
successful import does not change that status.

Implement one framework adapter first behind a common capability/status
interface. Subsequent Agent Lightning, AReaL and AgentFly adapters reuse the fixed
environment and evaluator, but each requires its own real execution evidence.
Core application imports and manual EDA operations must work with the optional
environment absent and all AI disabled.
