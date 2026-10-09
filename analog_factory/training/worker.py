"""Optional TRL worker. Imported only by the isolated ML interpreter."""
from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import sys
import time

from ..service import Workspace
from ..storage import atomic_json, read_json
from .environment import AnalogEnvironment, GRIDS, digest, prompt, tasksets
from . import manager


def policy_limits(options, max_actions, conditions):
    return {**manager.completion_identity(options.get('max_completion_length',96), manager.COMPLETION_BUDGET_SEMANTICS),
            'tool_rounds':max_actions, 'environment_actions':max_actions,
            'simulator_conditions_per_action':conditions}


def grpo_config(config_type, options, max_actions, output):
    """Construct the real trainer config; injectable type permits core-only checks."""
    return config_type(output_dir=str(output), use_cpu=True, use_vllm=False,
        bf16=False, fp16=False, optim='adamw_torch', report_to='none', push_to_hub=False,
        per_device_train_batch_size=1, per_device_eval_batch_size=1, gradient_accumulation_steps=2,
        num_generations=2, num_generations_eval=1, max_steps=options['steps'],
        max_completion_length=options['max_completion_length'], max_tool_calling_iterations=max_actions, learning_rate=1e-4,
        temperature=.8, top_p=1., top_k=0, beta=.01, seed=options['seed'], data_seed=options['seed'],
        gradient_checkpointing=True, gradient_checkpointing_kwargs={'use_reentrant': False},
        dataloader_pin_memory=False, logging_steps=1, save_steps=options['steps'], save_total_limit=1,
        disable_tqdm=True, log_completions=False, mask_truncated_completions=False)


def run(request_path, reload=False):
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback, set_seed
    from trl import GRPOConfig, GRPOTrainer

    request = read_json(request_path)
    output, runtime = Path(request['output']), Path(request['runtime'])
    opts = manager.options(request['options'])
    manager.completion_identity(opts['max_completion_length'], request.get('completion_budget_semantics'))
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    set_seed(opts['seed'])
    workspace = Workspace(Path(request['workspace']))
    catalog = request.get('taskset_record')
    if catalog:
        from . import task_catalog, configurable_environment
        if catalog != task_catalog.build(catalog['specification']):
            raise ValueError('Frozen taskset failed integrity validation')
    data = catalog['splits'] if catalog else tasksets()
    family = catalog['specification']['family'] if catalog else 'amplifier'
    grids = catalog['specification']['actions'] if catalog else GRIDS
    max_actions = catalog['specification']['max_actions'] if catalog else 3
    limits = policy_limits(opts, max_actions,
        len(catalog['specification']['robustness']['supply_factors']) if catalog else 2)
    tasks = {t['id']: t for values in data.values() for t in values}
    state = read_json(output / 'progress.json', {'curves': [], 'phase': 'loading'})
    (output / 'episodes').mkdir(exist_ok=True)
    (output / 'trajectories').mkdir(exist_ok=True)
    resumed = request.get('resume', {}).get('completed', {}) if request.get('resume') else {}
    if resumed:
        reused_index = 0
        for group in resumed.values():
            for episode in group:
                episode['episode'] = reused_index
                atomic_json(output / 'episodes' / f'{reused_index}.json', episode)
                reused_index += 1
    existing = [int(p.stem) for p in (output / 'episodes').glob('*.json')]
    counter = itertools.count(max(existing, default=-1) + 1)
    trace_counter = itertools.count(len(list((output / 'trajectories').glob('*.json'))))
    class Cancel:
        def is_set(self):
            return (output / 'cancel').exists()
    cancel = Cancel()
    def progress(**values):
        state.update(values)
        atomic_json(output / 'progress.json', state)
        print(json.dumps({k: v for k, v in values.items() if k != 'iterations'}), flush=True)
    def record(snapshot):
        snapshot['phase'] = state['phase']
        atomic_json(output / 'episodes' / f"{snapshot['episode']}.json", snapshot)
        state['completed_episodes'] = len(list((output / 'episodes').glob('*.json')))
        atomic_json(output / 'progress.json', state)
    class Tools:
        def __init__(self):
            arguments = (workspace, request['project_id'], request['job_id'], lambda: next(counter), cancel, record)
            self._environment = (configurable_environment.AnalogEnvironment(*arguments, catalog)
                                 if catalog else AnalogEnvironment(*arguments))
        def reset(self, task_id, **kwargs):
            return self._environment.reset(tasks[task_id])
        def get_reward(self):
            record(self._environment.snapshot())
            return self._environment.reward()
        def simulate(self, rd_index: int, bias_index: int, width_index: int) -> dict:
            """Simulate a sizing choice and return real measurements. Integers must be 0 to 5.

            Args:
                rd_index: Drain-resistance grid index, 0 to 5.
                bias_index: Gate-bias grid index, 0 to 5.
                width_index: Transistor-width grid index, 0 to 5.
            """
            return self._environment.simulate(rd_index, bias_index, width_index)
    if catalog:
        class ConfigurableTools(Tools):
            def simulate(self, rd_index: int, bias_index: int, width_index: int) -> dict:
                """Simulate amplifier indices from the declared task grids.

                Args:
                    rd_index: Drain-resistance grid index.
                    bias_index: Gate-bias grid index.
                    width_index: Transistor-width grid index.
                """
                return self._environment.simulate(rd_index=rd_index, bias_index=bias_index, width_index=width_index)
        if family == 'current_mirror':
            class ConfigurableTools(Tools):
                def simulate(self, reference_width_index: int, output_width_index: int, length_index: int) -> dict:
                    """Simulate current-mirror indices from the declared task grids.

                    Args:
                        reference_width_index: Reference MOS width grid index.
                        output_width_index: Output MOS width grid index.
                        length_index: Both MOS channel length grid index.
                    """
                    return self._environment.simulate(reference_width_index=reference_width_index,
                        output_width_index=output_width_index, length_index=length_index)
        Tools = ConfigurableTools
    class Curves(TrainerCallback):
        def on_log(self, args, trainer_state, control, logs=None, **kwargs):
            row = {'step': trainer_state.global_step, **(logs or {})}
            row = {k: v if not isinstance(v, float) or math.isfinite(v) else None for k, v in row.items()}
            state['curves'].append(row)
            progress(curves=state['curves'])
        def on_step_begin(self, args, trainer_state, control, **kwargs):
            if cancel.is_set():
                raise InterruptedError('Training cancelled')
    class AuditedTrainer(GRPOTrainer):
        def _generate_and_score_completions(self, inputs):
            result = super()._generate_and_score_completions(inputs)
            # Exact pre-update policy log probabilities are required even on TRL's
            # optimized on-policy path, which otherwise discards them.
            old = result.get('old_per_token_logps')
            if old is None:
                with torch.no_grad():
                    old, _, _ = self._get_per_token_logps_and_entropies(self.model,
                        torch.cat([result['prompt_ids'], result['completion_ids']], dim=1),
                        torch.cat([result['prompt_mask'], result['completion_mask']], dim=1),
                        result['completion_ids'].shape[1], batch_size=1)
            for i, env in enumerate(self.environments):
                item = env._environment.snapshot()
                for name in ('prompt_ids', 'prompt_mask', 'completion_ids', 'completion_mask',
                             'tool_mask', 'advantages'):
                    if name in result:
                        item[name] = result[name][i].detach().cpu().tolist()
                item['old_policy_logprobs'] = old[i].detach().cpu().tolist()
                item.update(phase=state['phase'], optimizer_step=self.state.global_step,
                            model=read_json(runtime / 'model-source.json'), seed=opts['seed'],
                            rendered_prompt=self.processing_class.decode(item['prompt_ids']),
                            completion_text=self.processing_class.decode(item['completion_ids']),
                            policy_limits=dict(limits))
                atomic_json(output / 'trajectories' / f'{next(trace_counter):06d}.json', item)
            return result

    progress(phase='loading')
    tokenizer = AutoTokenizer.from_pretrained(runtime / 'model', local_files_only=True, trust_remote_code=False)
    tokenizer.padding_side = 'left'
    base = AutoModelForCausalLM.from_pretrained(runtime / 'model', local_files_only=True,
                                               trust_remote_code=False, dtype=torch.float32)
    parent_id = request.get('checkpoint_job_id')
    checkpoint = output / 'checkpoint'
    if reload or parent_id:
        if parent_id:
            checkpoint = output.parent / parent_id / 'checkpoint'
        base = PeftModel.from_pretrained(base, checkpoint, is_trainable=False, local_files_only=True)
    config = grpo_config(GRPOConfig, opts, max_actions, output / 'optimizer')
    def rows(task_list):
        return [{'prompt': [{'role': 'user', 'content': ''}], 'task_id': t['id']} for t in task_list]
    trainer = AuditedTrainer(model=base, processing_class=tokenizer, args=config,
        train_dataset=Dataset.from_list(rows(data['train'][:opts['train_tasks']])),
        environment_factory=Tools, callbacks=[Curves()],
        peft_config=None if reload or parent_id else LoraConfig(r=8, lora_alpha=16,
            target_modules=['q_proj', 'v_proj'], lora_dropout=0., task_type='CAUSAL_LM'))
    def evaluate(label):
        progress(phase=label)
        trainer.model.eval()
        results = []
        key = 'before' if label == 'heldout_before' else 'after'
        reused = {episode['task_id']: episode for episode in resumed.get(key, [])}
        with torch.no_grad():
            for i, row in enumerate(rows(data['test'][:opts['eval_tasks']])):
                if cancel.is_set():
                    raise InterruptedError('Evaluation cancelled')
                if row['task_id'] in reused:
                    results.append(reused[row['task_id']])
                    continue
                set_seed(opts['seed'] + 10000 + i)
                trainer._generate_and_score_completions([row])
                results.append(trainer.environments[0]._environment.snapshot())
        return summarize(results)
    def random_baseline():
        progress(phase='random_baseline')
        rng = random.Random(opts['seed'])
        baseline = []
        reused = {episode['task_id']: episode for episode in resumed.get('random', [])}
        for task in data['test'][:opts['eval_tasks']]:
            if task['id'] in reused:
                episode = reused[task['id']]
                for _ in range(episode['calls']):
                    for values in grids.values():
                        rng.randrange(len(values))
                baseline.append(episode)
                continue
            env = Tools()._environment
            env.reset(task)
            for _ in range(max_actions):
                action = {name + '_index': rng.randrange(len(values)) for name, values in grids.items()}
                env.simulate(**action)
                if env.terminal:
                    break
            baseline.append(env.snapshot())
        return summarize(baseline)
    if reload or parent_id:
        previous = read_json(output / 'training-result.json', {})
        if parent_id:
            with trainer.model.disable_adapter():
                before = evaluate('heldout_before')
            previous['evaluation'] = {'before': before, 'random': random_baseline()}
        after = evaluate('heldout_after_reload')
        evaluation = {**previous.get('evaluation', {}), 'after': after,
                      'same_tasks_and_budget': True, 'checkpoint_reloaded': True,
                      'reload_process_pid': os.getpid()}
        if 'before' in evaluation:
            evaluation['success_rate_change'] = after['success_rate'] - evaluation['before']['success_rate']
            evaluation['mean_reward_change'] = after['mean_reward'] - evaluation['before']['mean_reward']
        result = {**previous, 'evaluation': evaluation,
                  'status': 'succeeded' if parent_id or previous.get('model_weights_updated') else 'failed'}
        result['policy_limits'] = dict(limits)
        if parent_id:
            result.update(classification='heldout_checkpoint_evaluation', model_weights_updated=False,
                          checkpoint_job_id=parent_id)
        atomic_json(output / 'result.json', result)
        progress(phase='completed', evaluation=evaluation)
        return

    before = evaluate('heldout_before')
    baseline = random_baseline()
    initial = {name: p.detach().clone() for name, p in trainer.model.named_parameters() if p.requires_grad}
    trainable_count = sum(p.numel() for p in initial.values())
    progress(phase='training')
    set_seed(opts['seed'])
    trainer.train()
    squared = sum(float((p.detach()-initial[name]).square().sum())
                  for name, p in trainer.model.named_parameters() if name in initial)
    changed = math.sqrt(squared)
    trainer.save_model(str(checkpoint))
    tokenizer.save_pretrained(checkpoint)
    trainer.save_state()
    hashes = {p.relative_to(checkpoint).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in checkpoint.rglob('*') if p.is_file()}
    result = {'status': 'checkpoint_saved', 'classification': 'language_model_reinforcement_learning',
              'model_weights_updated': changed > 0 and trainer.state.global_step > 0,
              'optimizer_steps': trainer.state.global_step, 'parameter_update_l2': changed,
              'trainable_parameter_count': trainable_count, 'model': read_json(runtime / 'model-source.json'),
              'checkpoint': {'path': 'checkpoint', 'hashes': hashes, 'format': 'PEFT safetensors',
                             'optimizer_state': 'optimizer/checkpoint-' + str(trainer.state.global_step)},
              'curves': state['curves'], 'training_process_pid': os.getpid(),
              'policy_limits': dict(limits),
              'taskset': request['taskset'], 'evaluation': {'before': before, 'random': baseline}}
    if not result['model_weights_updated']:
        result['error'] = 'No actual trainable parameter change; inspect reward diversity and gradients'
    atomic_json(output / 'training-result.json', result)
    atomic_json(output / 'result.json', result)
    progress(phase='checkpoint_saved', checkpoint=result['checkpoint'])


def summarize(episodes):
    valid = sum(e['calls'] > 0 for e in episodes)
    return {'tasks': len(episodes), 'successes': sum(e['satisfied'] for e in episodes),
            'success_rate': sum(e['satisfied'] for e in episodes)/len(episodes),
            'mean_reward': sum(e['reward'] for e in episodes)/len(episodes),
            'simulator_calls': sum(e.get('simulator_calls', e['calls']*2) for e in episodes),
            'episodes_with_valid_tool_calls': valid, 'tool_call_validity_rate': valid/len(episodes),
            'successes_among_valid_tool_episodes': sum(e['satisfied'] for e in episodes if e['calls'] > 0),
            'episodes_without_valid_tool_calls': len(episodes)-valid, 'episodes': episodes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()
    try:
        run(args.request, args.reload)
    except Exception as error:
        request = read_json(args.request)
        atomic_json(Path(request['output']) / 'result.json', {'status': 'failed', 'error': str(error),
                    'model_weights_updated': False})
        raise


if __name__ == '__main__':
    main()
