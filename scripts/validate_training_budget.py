#!/usr/bin/env python3
"""Qualify feedback delivery in installed TRL using scripted actions and real ngspice.

No model weights, optimization, cloud calls or synthetic electrical results. Run
with the prepared optional training interpreter. Output must be a new directory.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import threading
from types import MethodType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def qualify(output, model, ngspice, expanded=768):
    from transformers import AutoTokenizer
    import transformers
    import trl
    from trl import GRPOConfig, GRPOTrainer
    from trl.chat_template_utils import add_response_schema, get_training_chat_template, is_chat_template_prefix_preserving, parse_response
    from analog_factory.service import Workspace
    from analog_factory.storage import atomic_json
    from analog_factory.training.environment import AnalogEnvironment, MAX_ACTIONS, tasksets
    from analog_factory.training.manager import options
    from analog_factory.training.worker import grpo_config, policy_limits

    budget = options({'max_completion_length': expanded})['max_completion_length']
    if budget <= 96:
        raise ValueError('Expanded comparison requires a budget greater than 96')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = {'status': 'failed', 'classification': 'scripted_policy_adapter_acceptance',
                'model_weights_loaded': False, 'model_weights_updated': False,
                'learning_improvement_established': False, 'cases': []}
    workspace = None
    try:
        tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
        # Match GRPOTrainer's normal tokenizer setup without constructing a model.
        tokenizer = add_response_schema(tokenizer)
        chat_template = None if is_chat_template_prefix_preserving(tokenizer) else get_training_chat_template(tokenizer)
        model_config = json.loads((Path(model) / 'config.json').read_text())
        workspace = Workspace(output / 'workspace')
        workspace.save_settings({'tools': {'ngspice': str(Path(ngspice).resolve())}})
        reply = workspace.operations.invoke('project.create', {'name': 'Training feedback transport', 'template': 'amplifier'})
        if not reply['ok']:
            raise RuntimeError(reply['error'])
        project_id = reply['data']['id']
        # Repeated legal but unsolved actions isolate feedback delivery from learning.
        action = {'rd_index': 0, 'bias_index': 0, 'width_index': 0}
        tool_call = {'type': 'function', 'function': {'name': 'simulate', 'arguments': action}}
        text = '<tool_call>\n' + json.dumps(tool_call['function']) + '\n</tool_call><|im_end|>'
        action_ids = tokenizer.encode(text, add_special_tokens=False)
        evidence.update(project_id=project_id, task=tasksets()['test'][0],
                        trl_version=trl.__version__, transformers_version=transformers.__version__,
                        source_hashes={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in [Path(__file__), ROOT / 'analog_factory/training/environment.py',
                                      ROOT / 'analog_factory/training/manager.py', ROOT / 'analog_factory/training/worker.py']},
                        tokenizer_sha256=hashlib.sha256((Path(model) / 'tokenizer.json').read_bytes()).hexdigest(),
                        tool_loop_sha256=hashlib.sha256(inspect.getsource(GRPOTrainer._tool_call_loop).encode()).hexdigest(),
                        suffix_helper_sha256=hashlib.sha256(inspect.getsource(GRPOTrainer._get_tool_suffix_ids).encode()).hexdigest())
        for index, maximum in enumerate((96, budget)):
            settings = options({'max_completion_length': maximum})
            config = grpo_config(GRPOConfig, settings, MAX_ACTIONS, output / f'config-{maximum}')
            records, offered, generations, observations = [], [], [], []
            env = AnalogEnvironment(workspace, project_id, f'{index + 1:032x}', lambda: 0,
                                    threading.Event(), records.append)
            prompt = env.reset(evidence['task'])

            def simulate(**arguments):
                observation = env.simulate(**arguments)
                observations.append(copy.deepcopy(observation))
                return observation

            harness = SimpleNamespace(processing_class=tokenizer, _tokenizer=tokenizer,
                _is_vlm=False, chat_template=chat_template, chat_template_kwargs={}, use_vllm=False,
                model=SimpleNamespace(config=SimpleNamespace(**model_config)),
                max_completion_length=config.max_completion_length, max_tool_calling_iterations=config.max_tool_calling_iterations,
                _sync_tool_dicts=[{'simulate': simulate}], _async_tool_dicts=[{}])

            def suffix(self, messages):
                ids = GRPOTrainer._get_tool_suffix_ids(self, messages)
                offered.append(list(ids))
                return ids

            def generate(self, prompts, images, fields, **kwargs):
                generations.append(list(prompts[0]))
                return [list(action_ids)], None

            harness._get_tool_suffix_ids = MethodType(suffix, harness)
            harness._generate_single_turn = MethodType(generate, harness)
            messages = [[{'role': 'user', 'content': prompt}]]
            prompt_ids = [tokenizer.apply_chat_template(messages[0], add_generation_prompt=True,
                                                        tokenize=True, return_dict=False, chat_template=chat_template)]
            result = GRPOTrainer._tool_call_loop(harness, messages, prompt_ids, [list(action_ids)],
                [[parse_response(tokenizer, action_ids, prefix=prompt_ids[0])]], None, None, {})
            mask, completions, tokens, _, calls, failures, _ = result
            episode = env.snapshot()
            if failures or episode['satisfied'] or any(row['score']['failure'] for row in episode['results']):
                raise AssertionError('Fixture must have real successful simulations with unsatisfied electrical targets')
            if len(mask[0]) != len(tokens[0]) or len(tokens[0]) > maximum:
                raise AssertionError('Completion IDs and tool mask must align within the configured budget')
            if any(value not in (0, 1) for value in mask[0]):
                raise AssertionError('Unexpected tool mask')
            case = {'max_completion_length': maximum, 'initial_policy_tokens': len(action_ids),
                    'policy_limits': policy_limits(settings, MAX_ACTIONS, 2),
                    'offered_feedback_tokens': [len(ids) for ids in offered],
                    'retained_feedback_tokens': mask[0].count(0), 'retained_policy_tokens': mask[0].count(1),
                    'post_feedback_generations': len(generations), 'tool_calls': calls,
                    'simulator_calls': sum(len(row['run_ids']) for row in episode['results']),
                    'completion_ids': tokens[0], 'tool_mask': mask[0], 'completions': completions[0],
                    'observations': observations, 'generation_prompts': generations, 'episode': episode}
            evidence['cases'].append(case)
            if maximum == 96:
                if not (len(action_ids) + len(offered[0]) > maximum and not generations
                        and not case['retained_feedback_tokens'] and env.calls == 1):
                    raise AssertionError('Legacy budget did not reproduce loss of the first real observation')
            else:
                if env.calls != MAX_ACTIONS or calls != MAX_ACTIONS or len(generations) != MAX_ACTIONS:
                    raise AssertionError('Expanded budget did not retain the complete bounded tool sequence')
                if case['retained_feedback_tokens'] != sum(map(len, offered)):
                    raise AssertionError('Not all offered tool feedback is retained in the mask')
                for prompt_tokens, suffix_ids in zip(generations, offered, strict=True):
                    if prompt_tokens[-len(suffix_ids):] != suffix_ids:
                        raise AssertionError('Subsequent generation did not receive the exact tool response')
                before = env.calls
                denied = env.simulate(**action)
                if env.calls != before or denied.get('remaining_calls') != 0 or not denied.get('error'):
                    raise AssertionError('Expanded tokens changed the environment action ceiling')
        evidence['status'] = 'passed'
    except Exception as error:
        evidence['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if workspace is not None:
            workspace.operations.close()
        atomic_json(output / 'evidence.json', evidence)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=ROOT / '.tools/training/model')
    parser.add_argument('--ngspice', type=Path)
    parser.add_argument('--expanded-budget', type=int, default=768)
    args = parser.parse_args()
    executable = args.ngspice or shutil.which('ngspice')
    if not executable:
        parser.error('A real installed ngspice executable is required')
    result = qualify(args.output, args.model, executable, args.expanded_budget)
    print(json.dumps({'status': result['status'], 'evidence': str(args.output / 'evidence.json'),
                      'classification': result['classification']}))


if __name__ == '__main__':
    main()
