"""Completion budget is a policy identity, not extra simulator authority."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class CompletionBudgetTests(unittest.TestCase):
    def test_options_have_bounded_strict_integer_feedback_inclusive_budget(self):
        from analog_factory.training import manager
        self.assertEqual(manager.options({})['max_completion_length'],96)
        for value in (96,768,2048):
            self.assertEqual(manager.options({'max_completion_length':value})['max_completion_length'],value)
        for value in (True,False,96.,95,2049,'768',None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                manager.options({'max_completion_length':value})

    def test_registered_train_and_evaluate_validate_budget_before_any_launch(self):
        from analog_factory.service import Workspace
        from analog_factory.training import manager
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            pid=workspace.projects.create('Budget schema')['id']
            with patch.object(manager,'start',return_value={'id':'a'*32,'kind':'training','status':'queued'}) as launch:
                for name in ('training.start','training.evaluate'):
                    base={'project_id':pid,**({'checkpoint_job_id':'b'*32} if name.endswith('evaluate') else {})}
                    for value in (True,96.5,95,2049):
                        result=registry.invoke(name,{**base,'options':{'max_completion_length':value}})
                        self.assertFalse(result['ok'],result)
                    self.assertEqual(launch.call_count,0)
                    result=registry.invoke(name,{**base,'options':{'max_completion_length':768}})
                    self.assertTrue(result['ok'],result)
                    self.assertEqual(launch.call_args.args[2]['max_completion_length'],768)
                    launch.reset_mock()

    def test_worker_grpo_config_and_limits_share_option_without_changing_actions(self):
        from analog_factory.training import worker,manager
        for value in (96,768,2048):
            opts=manager.options({'max_completion_length':value})
            config=worker.grpo_config(dict,opts,3,Path('/unused/optimizer'))
            limits=worker.policy_limits(opts,3,2)
            self.assertEqual(config['max_completion_length'],value)
            self.assertEqual(config['max_tool_calling_iterations'],3)
            self.assertEqual(limits['completion_tokens'],value)
            self.assertEqual(limits['completion_budget_semantics'],'policy_and_tool_feedback_v1')
            self.assertEqual(limits['environment_actions'],3)
            self.assertEqual(limits['simulator_conditions_per_action'],2)

    def test_resume_rejects_budget_or_semantics_changes_before_evidence_reuse(self):
        from analog_factory.training import manager,resume
        from analog_factory.storage import atomic_json
        source={'kind':'training_evaluation','status':'failed','owner_identity':'local','parent_id':'c'*32}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for original,requested,semantics in ((96,768,None),(768,96,'policy_and_tool_feedback_v1'),(768,768,None),(96,96,'different_semantics')):
                atomic_json(root/'request.json',{'checkpoint_job_id':'c'*32,'options':{**manager.DEFAULTS,'max_completion_length':original},**({'completion_budget_semantics':semantics} if semantics else {})})
                with self.subTest(original=original,requested=requested,semantics=semantics),patch.object(manager,'get',return_value=source),patch.object(manager,'directory',return_value=root):
                    with self.assertRaisesRegex(ValueError,'budget|semantics'):
                        resume.prepare(None,'a'*32,'b'*32,'c'*32,{'max_completion_length':requested})

    def test_legacy_identity_normalizes_to_96_but_cannot_label_expanded_budget_legacy(self):
        from analog_factory.training import manager
        self.assertEqual(manager.completion_identity(),{'completion_tokens':96,'completion_budget_semantics':'policy_and_tool_feedback_v1'})
        self.assertEqual(manager.completion_identity(96,None),manager.completion_identity(96,'policy_and_tool_feedback_v1'))
        with self.assertRaises(ValueError):manager.completion_identity(768,None)
        with self.assertRaises(ValueError):manager.completion_identity(96,'unknown')

    def test_resume_accepts_legacy96_and_declared_expanded_trace_but_rejects_mixed_identity(self):
        from analog_factory.service import Workspace
        from analog_factory.training import manager,resume,environment
        from analog_factory.storage import atomic_json
        import hashlib
        source={'kind':'training_evaluation','status':'failed','owner_identity':'local','parent_id':'c'*32}
        task=environment.tasksets()['test'][0]
        episode={'episode':0,'phase':'heldout_before','task_id':task['id'],'task_sha256':environment.digest(task),
            'evaluator_sha256':hashlib.sha256(Path(environment.__file__).read_bytes()).hexdigest(),
            'calls':0,'results':[],'reward':-1.,'satisfied':False,'termination':'policy_finish'}
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory)/'workspace');self.addCleanup(workspace.operations.close);self.addCleanup(workspace.agents.close)
            pid=workspace.projects.create('Resume budget identity')['id']
            root=Path(directory)/'training';root.mkdir();(root/'episodes').mkdir();(root/'trajectories').mkdir()
            runtime=Path(directory)/'runtime';runtime.mkdir();atomic_json(runtime/'model-source.json',{})
            atomic_json(root/'episodes/0.json',episode)
            for budget,trace_limits,size in ((96,None,1),(96,None,96),(96,None,97),(768,{'completion_tokens':768,'completion_budget_semantics':'policy_and_tool_feedback_v1'},1),(768,{'completion_tokens':768,'completion_budget_semantics':'policy_and_tool_feedback_v1'},768),(768,{'completion_tokens':768,'completion_budget_semantics':'policy_and_tool_feedback_v1'},769),(768,{'completion_tokens':96},1),(96,{'completion_tokens':96,'completion_budget_semantics':'other'},1)):
                request={'checkpoint_job_id':'c'*32,'options':{**manager.DEFAULTS,'max_completion_length':budget},'taskset':manager.taskset_summary()}
                if budget==96 and trace_limits is None:request['options'].pop('max_completion_length')
                if budget!=96:request['completion_budget_semantics']=manager.COMPLETION_BUDGET_SEMANTICS
                atomic_json(root/'request.json',request)
                trace={**episode,'model':{},'seed':42,'completion_ids':[1]*size,'completion_mask':[1]*size,'old_policy_logprobs':[-1.]*size}
                if trace_limits is not None:trace['policy_limits']=trace_limits
                atomic_json(root/'trajectories/0.json',trace)
                with self.subTest(budget=budget,trace_limits=trace_limits,size=size),patch.object(manager,'get',return_value=source),patch.object(manager,'directory',return_value=root),patch.object(manager,'RUNTIME',runtime):
                    if size<=budget and (trace_limits is None or trace_limits.get('completion_tokens')==768):
                        settings,receipt=resume.prepare(workspace,pid,'b'*32,'c'*32,{})
                        self.assertEqual(settings['max_completion_length'],budget)
                        self.assertEqual(len(receipt['completed']['before']),1)
                    else:
                        with self.assertRaisesRegex(ValueError,'budget|semantics'):
                            resume.prepare(workspace,pid,'b'*32,'c'*32,{})

    def test_supervisor_records_explicit_request_identity_even_when_worker_does_not_run(self):
        from analog_factory.service import Workspace
        from analog_factory.training import manager
        import json, threading
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));self.addCleanup(workspace.operations.close);self.addCleanup(workspace.agents.close)
            pid=workspace.projects.create('Recorded budget')['id'];captured={}
            def capture(root,kind,handler,metadata):
                captured.update(handler=handler,metadata=metadata)
                return {'id':'a'*32,'kind':kind,'status':'queued'}
            class FailedWorker:
                pid=0;returncode=1
                def poll(self):return self.returncode
                def wait(self):return self.returncode
            with patch.object(manager,'resources',return_value={'frameworks':{'trl_cpu':{'available':True}}}),patch.object(workspace.jobs,'list',return_value=[]),patch.object(workspace.jobs,'start',side_effect=capture):
                manager.start(workspace,pid,{'max_completion_length':768})
            self.assertEqual(captured['metadata']['options']['max_completion_length'],768)
            self.assertEqual(captured['metadata']['completion_budget_semantics'],'policy_and_tool_feedback_v1')
            # The process double exits before any ML execution or trajectory.
            with patch.object(manager.subprocess,'Popen',return_value=FailedWorker()):
                result=captured['handler']({'id':'a'*32},threading.Event(),lambda **kwargs:None)
            self.assertEqual(result['status'],'failed')
            request=json.loads((manager.directory(workspace,pid,'a'*32)/'request.json').read_text())
            self.assertEqual(request['options']['max_completion_length'],768)
            self.assertEqual(request['completion_budget_semantics'],'policy_and_tool_feedback_v1')

    def test_registry_partial_options_do_not_inject_new_job_defaults(self):
        from analog_factory.service import Workspace
        from analog_factory.training import manager
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            pid=workspace.projects.create('Budget partial patch')['id']
            session=registry.sessions.connect({'project_id':pid})
            ack={'client_id':session['client_id'],'data':{},'state':{'project_id':pid},'version':1}
            with patch.object(registry.sessions,'command',return_value=ack) as command:
                result=registry.invoke('ui.training.set_options',{'client_id':session['client_id'],'options':{'steps':4}})
                self.assertTrue(result['ok'],result)
                self.assertEqual(command.call_args.args[2]['options'],{'steps':4})

    def test_registry_resume_partial_options_are_forwarded_without_budget(self):
        from analog_factory.service import Workspace
        from analog_factory.training import manager
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            pid=workspace.projects.create('Budget resume patch')['id']
            with patch.object(manager,'start',return_value={'id':'a'*32,'kind':'training_evaluation','status':'queued'}) as start:
                result=registry.invoke('training.evaluate',{'project_id':pid,'checkpoint_job_id':'b'*32,'resume_job_id':'c'*32,'options':{'wall_seconds':2400}})
                self.assertTrue(result['ok'],result)
                self.assertEqual(start.call_args.args[2],{'wall_seconds':2400})
                self.assertEqual(start.call_args.kwargs['resume_job_id'],'c'*32)
