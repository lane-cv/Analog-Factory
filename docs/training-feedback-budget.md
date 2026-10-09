# Training feedback budget: implementation and bounded validation

The completion budget is now configurable through the registered `training.start`,
`training.evaluate` and acknowledged `ui.training.set_options` interfaces, and the
same manual training control. `max_completion_length` accepts integers 96–2048,
with the historical default 96 for new jobs. It includes policy text, tool
observations and framing. Changing another draft option or resuming an evaluation
preserves its existing budget; omission is not an instruction to reset it.

Requests, trajectories and reports record `policy_and_tool_feedback_v1`. Resume
checks this identity and rejects changed budgets, mixed trajectories and token
arrays exceeding the declared limit. Missing historical identity normalizes to
96. To evaluate an old checkpoint at another budget, start a new evaluation job.
Environment action/condition limits and independent electrical scoring are unchanged.

No new optimization, checkpoint update or electrical-success improvement was
produced by this change. The existing CPU pilot still reports one electrical
success out of 32 held-out tasks for both the base and the reloaded adapter.

## Actual evidence and separate failure causes

The compact qualification receipt is [cpu-pilot-and-heldout.json](../examples/training/evidence/cpu-pilot-and-heldout.json), with context in [training.md](training.md). Full retained evidence belongs to project `139160807efb40d4b90a1dc399ec5632` in `.validation/training-pilot`:

- Pilot/checkpoint job: `fdcfb096e6db476f98c6c8d47cf0cdcd`.
- Initial held-out attempt: `2510c341ebbc4d2c839c66b7cc3b55de`.
- Verified resume completing eight missing trained episodes: `0ee3a9ca912441e69f6bb3d9f5d8309f`.
- Trajectory directories: `.validation/training-pilot/projects/139160807efb40d4b90a1dc399ec5632/.af/training/<job-id>/trajectories/`.

Reading the initial attempt's 56 trajectories and the resume's eight trajectories gives the following breakdown. These are observed records, not inferred simulator outcomes:

| Policy | Valid simulator episode | Rejected out-of-range index | No dispatched call; multiple calls followed by truncation |
| --- | ---: | ---: | ---: |
| Base, 32 tasks | 13 | 12 | 7 |
| Reloaded adapter, 32 tasks | 15 | 12 | 5 |

Three mechanisms must remain separate:

1. **Index/physical-value confusion.** Initial trajectory `000000.json` uses `width_index=10`, although the allowed indices are 0–5. Its results retain `invalid_action`, zero simulator calls and the actual validation error. Other records use physical values such as `rd_index=2000`. `AnalogEnvironment.simulate` ends an episode after an invalid action; it does not silently coerce or repair the value ([environment.py](../analog_factory/training/environment.py), lines 152–160). This is a policy action-validity problem, not a simulator failure.
2. **Batch output truncation.** Initial trajectory `000001.json` emits two complete `<tool_call>` blocks and an incomplete third block at the 96-token ceiling, with no dispatched action. Resume trajectory `000001.json` shows the same pattern. The records establish truncated batch-shaped output and no execution; they do not justify a general claim that every multi-call response is unsupported.
3. **Measured feedback dropped by the aggregate completion budget.** All 28 valid simulator episodes contain only one action. Twenty-six are unsolved and end as `policy_finish`, below the environment's three-action ceiling. Their retained completion contains the initial tool call without the measured tool response. The concrete token calculation below identifies why a valid first action can still provide no usable observation to the next policy turn.

The generic `termination=policy_finish` label alone does not distinguish these mechanisms. Classify from the recorded actions, validation errors, completion tokens and simulator evidence rather than treating all policy finishes as valid completed tuning episodes.

## Historical budget diagnosis: action plus feedback exceeds 96

Concrete failed observation:

`.validation/training-pilot/projects/139160807efb40d4b90a1dc399ec5632/.af/training/2510c341ebbc4d2c839c66b7cc3b55de/trajectories/000002.json`

This trajectory has a valid `{rd_index: 0, bias_index: 0, width_index: 0}` action, one action/two real simulator conditions, unsatisfied constraints, no electrical execution failure, and `termination=policy_finish`. Its completion is the 34-token tool call followed by `<|im_end|>`, with no `<tool_response>`. The real reported measurements include `vout_dc=1.987028`, `supply_current=-6.486e-06` and `gain_db=-17.1991`.

The installed qualification runtime is **TRL 1.14.2**, **Transformers 5.3.0**, **tokenizers 0.22.2**. Its fixed local tokenizer is `.tools/training/model`, from `Qwen/Qwen2.5-0.5B-Instruct`, model revision `7ae557604adf67be50417f59c2c2f167def9a775`. No model weights or generation were loaded for this calculation.

The primary installed source is `.tools/training/venv/lib/python3.12/site-packages/trl/trainer/grpo_trainer.py`:

- Lines 1969–2009: `_get_tool_suffix_ids` constructs a dummy conversation, tokenizes before and after the tool response, and returns the suffix difference.
- Lines 2091–2118: real tool results are stringified, then appended to prompt + completion + tool suffix.
- Lines 2120–2146: the suffix counts against `max_completion_length`; if the aggregate is too long, the tool messages are removed and that sample exits the tool loop.

The historical worker hard-coded `max_completion_length=96`; the current [worker.py](../analog_factory/training/worker.py) uses the normalized registered option. Therefore 34 action tokens plus 129 feedback/framing tokens requires 163 tokens **before another generated action**. The feedback already exceeds the configured aggregate budget. This is an adapter-budget seam even when the action is valid and the simulator worked.

The following read-only reproduction uses the dummy-message difference from the installed TRL helper, including its explicit `return_dict=False`. It omits the helper's subsequent EOS-prefix trimming: the raw difference is 129 tokens, while the exact helper retains a newline and returns 130 for this historical record. Both exceed the 96-token cap with the 34-token action. The actual integration gate below uses the complete helper and normal GRPO tokenizer/parser setup. The reconstructed result has the exact environment return fields; `remaining_calls=2` follows from three allowed actions minus the one recorded unsolved action.

```python
import json
from pathlib import Path
from transformers import AutoTokenizer

path = Path('.validation/training-pilot/projects/139160807efb40d4b90a1dc399ec5632'
            '/.af/training/2510c341ebbc4d2c839c66b7cc3b55de'
            '/trajectories/000002.json')
trace = json.loads(path.read_text())
item = trace['results'][-1]
score = item['score']
response = {
    'metrics': score['metrics'],
    'constraints': score['constraints'],
    'satisfied': item['satisfied'],
    'parameters': item['parameters'],
    'remaining_calls': 2,
}
tokenizer = AutoTokenizer.from_pretrained(
    '.tools/training/model', local_files_only=True, trust_remote_code=False)
messages = [
    {'role': 'user', 'content': 'dummy'},
    {'role': 'assistant', 'content': '', 'tool_calls': [
        {'type': 'function', 'function': {'name': 'simulate', 'arguments': {}}}
    ]},
]
prefix = tokenizer.apply_chat_template(
    messages, add_generation_prompt=False, tokenize=True, return_dict=False)
full = tokenizer.apply_chat_template(
    messages + [{'role': 'tool', 'name': 'simulate', 'content': str(response)}],
    add_generation_prompt=True, tokenize=True, return_dict=False)
action_tokens = sum(trace['completion_mask'])
feedback_tokens = len(full) - len(prefix)
print(action_tokens, feedback_tokens, action_tokens + feedback_tokens)
# Observed: 34 129 163
```

This calculation explains the specific feedback-loss case. It does not demonstrate that fixing the budget will solve the task, or that all 64 failures have the same cause. Invalid indices and truncated batches remain independent targets for later experiments.

## Implemented budget contract and remaining instrumentation

The bounded option is implemented consistently across the registered schema, manager, worker, manual UI, request/trajectory/report receipts and resume identity. The range is 96–2048 total tokens, default 96. The scripted acceptance uses 768; it is not a proven adequate value for every real model response.

The option means **the total completion including tool observations**, matching TRL. Runtime trajectories preserve the existing tool masks and exact tokens, and the UI displays recorded limits and existing measured token logs. The scripted acceptance records offered/retained feedback and generation prompts. General per-rollout dropped-feedback/truncation/time counters are not yet implemented; missing counters must not be presented as measured zero.

Use the existing three-action environment ceiling and two supply/temperature conditions per action: **at most six actual ngspice executions per task**. Expanding the token budget must not expand simulator actions, fabricate additional measurements, repair invalid indices, change scoring, or turn failed actions into successes. The current worker uses `max_tool_calling_iterations=max_actions`; the historical pilot additionally had a two-round LM limit. Record that difference explicitly rather than rewriting its receipt as a three-round experiment.

Resume identity must include the normalized completion budget and its feedback-inclusive semantics, alongside the existing seed, taskset/evaluator, model/checkpoint and source identities. Legacy requests without this field normalize to the original 96-token behavior. A completed 96-token episode cannot be reused as an expanded-budget episode. Evaluating the old checkpoint under a new budget requires a **new evaluation job**, not a resume that mixes policies. Historical evidence remains unchanged.

## Actual adapter acceptance and remaining model experiments


The actual acceptance at `.validation/training-feedback-budget-20261009-corrected/evidence.json`
uses installed TRL 1.14.2, Transformers 5.3.0, the pinned local tokenizer, the real
production GRPO config builder and `GRPOTrainer._tool_call_loop`, and real ngspice
observations. It loads no model weights and scripts only the generated actions.

| Total budget | Initial action tokens | Feedback offered / retained | Subsequent generations | Real ngspice calls |
| --- | ---: | --- | ---: | ---: |
| 96 | 34 | 132 / 0 | 0 | 2 |
| 768 | 34 | 396 / 396 across three rounds | 3 | 6 |

Every continuation receives the exact tokenizer suffix from its real observation;
mask lengths and budget bounds are checked. The three-action environment rejects
an extra action after the last round. Electrical targets intentionally remain
unsatisfied in both fixtures. A failed initial harness missing TRL's normal
response-parser setup remains at `.validation/training-feedback-budget-20261009/`;
it is not evidence against or for the product behavior.

Repeat with the prepared optional interpreter and a new output directory:

```sh
.tools/training/venv/bin/python scripts/validate_training_budget.py \
  --output .validation/training-feedback-budget-new \
  --ngspice .tools/eda/bin/ngspice-sky130
```

This is an explicit local optional-runtime gate; ordinary CI runs the portable
Python budget/resume contracts and Node/browser parity without installing ML.
It validates transport and unchanged simulator bounds, not improved model actions.
Remaining work before claims of better design:

1. The scripted gate above tests the real pinned tokenizer and installed TRL tool-loop semantics; extend instrumentation before a model comparison. A scripted generation fixture should make one valid unsolved action, deliver its actual-shaped observation and then generate another action. Verify the 96-token case loses this feedback and the selected expanded budget retains it; verify action/tool masks, dropped-feedback counters and the unchanged three-action ceiling. This is adapter testing, not evidence of model learning.
2. Run a small actual ngspice smoke evaluation using the existing untouched base and the verified saved checkpoint. Confirm that successful tool observations reach a subsequent policy turn. Keep invalid indices and malformed responses as failures; retain their raw tokens and categories.
3. Before any new optimization, evaluate both models on the same fixed 32 held-out tasks, matched seeds, evaluator, expanded completion budget and action ceiling. Reload the same safetensors checkpoint and report its hash. Compare this pair with the preserved old-budget results as a **budget intervention**, not a newly trained-policy improvement.
4. Report electrical successes separately from action validity, number of rounds, feedback retention, truncation and valid-but-unsolved outcomes. Record actual ngspice calls, policy/feedback token counts, model-generation time, simulator time, elapsed time and memory. Keep cancelled/incomplete episodes explicit.

The previous random baseline consumed 150 real ngspice executions, while base/trained policies consumed 26/30. The policies shared an action ceiling but did not consume equal actual compute ([qualification receipt](../examples/training/evidence/cpu-pilot-and-heldout.json), lines 119–159). A new comparison must continue reporting this distinction. More electrical successes after enabling feedback would establish a better operating configuration for these checkpoints; it would not by itself prove that RL learned better circuit design or that random search is inferior at equal cost.
