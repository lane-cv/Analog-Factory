"""Anonymous official release observations never install or authenticate a CLI."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone

class CliUpdatesTests(unittest.TestCase):
    def manager(self,root):
        from analog_factory.cli_updates import CliUpdates
        class Projects:
            def get(self,pid):return {'config':{'tools':{'codex':'/project/codex'}}}
        class Workspace:
            projects=Projects()
            def config(self,pid=None):return {'tools':{'codex':'/project/codex' if pid else '/workspace/codex'}}
            def settings(self):return {'cli_updates':{'automatic':False,'interval_hours':24}}
        workspace=Workspace();workspace.root=Path(root)
        manager=CliUpdates(workspace);self.addCleanup(manager.close);return manager
    def release(self,backend='codex',version='1.2.3'):
        repo='openai/codex' if backend=='codex' else 'anthropics/claude-code'
        tag=('rust-v' if backend=='codex' else 'v')+version
        return {'tag_name':tag,'html_url':'https://github.com/'+repo+'/releases/tag/'+tag,'published_at':'2026-10-08T12:00:00Z','prerelease':False,'draft':False,'body':'<script>unsafe</script>'}
    def test_fresh_official_success_persists_and_failed_attempt_never_becomes_current(self):
        from analog_factory.operations import OperationError
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root)
            with patch.object(manager,'_fetch',return_value=self.release()) as fetch:
                result=manager.check_updates(['codex']);self.assertEqual(fetch.call_count,1)
                self.assertEqual(result['backends'][0]['latest_success']['version'],'1.2.3')
                manager.check_updates(['codex'],force=True);self.assertEqual(fetch.call_count,1,'force respects cooldown')
            restarted=self.manager(root)
            self.assertEqual(restarted.releases()['backends'][0]['latest_success']['version'],'1.2.3')
            restarted._cache['codex']['latest_attempt']['checked_at']='2020-01-01T00:00:00Z'
            with patch.object(restarted,'_fetch',side_effect=OSError('private-secret-url')):
                with self.assertRaises(OperationError):restarted.check_updates(['codex'],force=True)
            row=restarted.releases()['backends'][0]
            self.assertTrue(row['stale']);self.assertEqual(row['latest_success']['version'],'1.2.3')
            self.assertNotIn('private-secret',json.dumps(row))
    def test_release_parser_rejects_untrusted_or_malformed_observations(self):
        from analog_factory.cli_updates import parse_release
        good=self.release();row=parse_release('codex',good)
        self.assertEqual(row['notes'],'<script>unsafe</script>')
        self.assertEqual(parse_release('codex',{**good,'body':'ok\x00\x1b\x7f\x85\r\nnext\tline'})['notes'],'ok\nnext\tline')
        for field,value in [('html_url','https://evil.invalid/release'),('prerelease',True),('draft',True),('published_at','2026-02-30T00:00:00Z'),('tag_name','rust-v01.2.3'),('tag_name','rust-v1.2.3-beta'),('body',{})]:
            with self.subTest(field=field),self.assertRaises(ValueError):parse_release('codex',{**good,field:value})
        long=parse_release('codex',{**good,'body':'<'*10000});self.assertLessEqual(len(long['notes']),8000);self.assertTrue(long['notes_truncated'])
    def test_health_is_project_scoped_and_never_publishes_cli_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root)
            with patch('analog_factory.cli_updates.toolchain._tool',return_value={'available':True,'path':'/project/codex','version':'codex-cli 1.2.3','diagnostic':'PRIVATE_AUTH_CONTENT'}):
                status=manager.status('a'*32)
            codex=status['tools'][0];self.assertEqual(codex['config_source'],'project');self.assertEqual(codex['version'],'1.2.3');self.assertEqual(codex['login'],'not_checked');self.assertEqual(codex['update_status'],'unknown')
            self.assertNotIn('PRIVATE_AUTH',json.dumps(status))
    def test_confined_cache_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            outside=Path(root)/'outside';outside.write_text('{}');(Path(root)/'.cli-updates.json').symlink_to(outside)
            with self.assertRaises(ValueError):self.manager(root)
    def test_fetch_rejects_redirect_and_oversize_without_exposing_network_error(self):
        from analog_factory.cli_updates import MAX_RESPONSE
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root)
            class Response:
                status=302
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def geturl(self):return 'https://evil.invalid/private'
                def read(self,n):return b'x'*(MAX_RESPONSE+1)
            with patch('analog_factory.cli_updates.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value=Response()
                with self.assertRaises(ValueError):manager._fetch('codex')
    def test_registry_validation_and_failure_envelope_preserve_public_cache(self):
        from analog_factory.service import Workspace
        from analog_factory.operation_cli_updates import register_cli_updates
        with tempfile.TemporaryDirectory() as root:
            workspace=Workspace(Path(root));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            if 'agent.cli.status' not in registry.operations:register_cli_updates(registry)
            manager=workspace.cli_updates
            with patch.object(manager,'_fetch',return_value=self.release()) as fetch:
                denied=registry.invoke('agent.cli.check_updates',{'backends':['unknown']})
                self.assertFalse(denied['ok']);self.assertEqual(fetch.call_count,0)
                result=registry.invoke('agent.cli.check_updates',{'backends':['codex']})
                self.assertTrue(result['ok'],result)
            manager._cache['claude']={'latest_success':None,'latest_attempt':None}
            with patch.object(manager,'_fetch',side_effect=RuntimeError('PRIVATE_STDOUT_TOKEN')):
                failed=registry.invoke('agent.cli.check_updates',{'backends':['claude']})
                self.assertFalse(failed['ok']);self.assertIn(failed['error']['code'],('UNAVAILABLE','EXECUTION_FAILED'))
                self.assertNotIn('PRIVATE_STDOUT',json.dumps(failed))
            releases=registry.invoke('agent.cli.releases',{})
            self.assertTrue(releases['ok'],releases)
            self.assertEqual(releases['data']['backends'][1]['latest_attempt']['status'],'failed')

    def test_single_flight_and_opt_in_scheduler_use_same_read_only_registry_handler(self):
        import threading,time
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root);entered=threading.Event();release=threading.Event();calls=[]
            def fetch(backend):
                calls.append(backend);entered.set();release.wait(1);return self.release(backend)
            results=[]
            with patch.object(manager,'_fetch',side_effect=fetch):
                first=threading.Thread(target=lambda:results.append(manager.check_updates(['codex'])))
                second=threading.Thread(target=lambda:results.append(manager.check_updates(['codex'])))
                first.start();self.assertTrue(entered.wait(1));second.start();release.set();first.join(2);second.join(2)
            self.assertEqual(calls,['codex']);self.assertEqual(len(results),2)
            setting={'tools':{},'cli_updates':{'automatic':False,'interval_hours':24}}
            observed=[];scheduled=threading.Event()
            class Registry:
                def invoke(self,name,params,context):observed.append((name,params,context));scheduled.set();return {'ok':True}
            with patch.object(manager.workspace,'settings',side_effect=lambda:setting):
                manager.attach_registry(Registry());self.assertFalse(scheduled.wait(.05))
                setting['cli_updates']['automatic']=True;manager.reconfigure();self.assertTrue(scheduled.wait(1))
                manager.close()
            name,params,context=observed[0]
            self.assertEqual(name,'agent.cli.check_updates');self.assertNotIn('project_id',params)
            self.assertEqual(context.permissions,frozenset({'read'}));self.assertEqual(context.source,'scheduler')
            self.assertFalse(manager.thread.is_alive())

    def test_oversize_200_and_unknown_installed_versions_are_rejected(self):
        from analog_factory.cli_updates import MAX_RESPONSE,installed_version
        self.assertIsNone(installed_version('codex','PRIVATE_SECRET 1.2.3'))
        self.assertIsNone(installed_version('claude','01.2.3 (Claude Code)'))
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root)
            class Response:
                status=200
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def geturl(self):return 'https://api.github.com/repos/openai/codex/releases/latest'
                def read(self,n):return b'x'*(MAX_RESPONSE+1)
            with patch('analog_factory.cli_updates.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value=Response()
                with self.assertRaises(ValueError):manager._fetch('codex')

    def test_workspace_settings_owns_scheduler_and_null_attempt_cannot_crash_it(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root);manager._cache['codex']={'latest_success':None,'latest_attempt':None}
            event=threading.Event()
            class Registry:
                def invoke(self,*args):event.set();return {'ok':True}
            with patch.object(manager.workspace,'settings',return_value={'cli_updates':{'automatic':True,'interval_hours':6}}):
                manager.attach_registry(Registry());self.assertTrue(event.wait(1),'Opt-in workspace setting must drive scheduler')
            manager.close()
            self.assertFalse(manager.thread.is_alive())

    def test_closed_checker_rejects_cache_hit_and_never_persists_inflight_result(self):
        import threading
        from analog_factory.operations import OperationError
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root)
            with patch.object(manager,'_fetch',return_value=self.release()):manager.check_updates(['codex'])
            cache=Path(root)/'.cli-updates.json';original=cache.read_bytes()
            entered=threading.Event();finish=threading.Event();errors=[]
            def fetch(backend):entered.set();finish.wait(2);return self.release(backend)
            def check():
                try:manager.check_updates(['claude'])
                except OperationError as error:errors.append(error)
            with patch.object(manager,'_fetch',side_effect=fetch):
                thread=threading.Thread(target=check);thread.start();self.assertTrue(entered.wait(1));manager.close();finish.set();thread.join(1)
                with self.assertRaises(OperationError):manager.check_updates(['codex'])
            self.assertTrue(errors);self.assertFalse(thread.is_alive());self.assertEqual(cache.read_bytes(),original)
    def test_malformed_cache_map_does_not_break_registry_initialization(self):
        from analog_factory.service import Workspace
        for invalid in ([],None,'bad'):
            with self.subTest(invalid=invalid),tempfile.TemporaryDirectory() as root:
                (Path(root)/'.cli-updates.json').write_text(json.dumps({'schema_version':1,'backends':invalid}))
                workspace=Workspace(Path(root));self.addCleanup(workspace.agents.close)
                registry=workspace.operations;self.addCleanup(registry.close)
                result=registry.invoke('agent.cli.releases',{})
                self.assertTrue(result['ok'],result)
                self.assertTrue(all(row['latest_success'] is None for row in result['data']['backends']))

    def test_empty_effective_override_reports_path_source(self):
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root)
            with patch.object(manager.workspace.projects,'get',return_value={'config':{'tools':{'codex':''}}}),patch.object(manager.workspace,'config',side_effect=lambda pid=None:{'tools':{'codex':'' if pid else '/workspace/codex'}}),patch('analog_factory.cli_updates.toolchain._tool',return_value={'available':True,'path':'/path/codex','version':'codex-cli 1.2.3'}):
                row=manager.status('a'*32)['tools'][0]
            self.assertEqual(row['configured_path'],None);self.assertEqual(row['config_source'],'PATH')

    def test_scheduler_configuration_change_before_wait_is_not_lost(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root);reading=threading.Event();finish=threading.Event();scheduled=threading.Event();calls=[]
            def configuration():
                calls.append(True)
                if len(calls)==1:
                    reading.set();finish.wait(1)
                    return {'enabled':False,'interval_hours':24,'server_running_only':True}
                return {'enabled':True,'interval_hours':24,'server_running_only':True}
            class Registry:
                def invoke(self,*args):scheduled.set();return {'ok':True}
            with patch.object(manager,'_configuration',side_effect=configuration):
                manager.attach_registry(Registry());self.assertTrue(reading.wait(1))
                manager.reconfigure();finish.set()
                self.assertTrue(scheduled.wait(.5),'Configuration changed between snapshot and wait must be reread')
            manager.close()

    def test_status_uses_numeric_versions_only_with_fresh_successful_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root)
            with patch.object(manager,'_fetch',return_value=self.release(version='1.10.0')):manager.check_updates(['codex'])
            for version,expected in [('1.9.0','update_available'),('1.10.0','current'),('2.0.0','ahead')]:
                with self.subTest(version=version),patch('analog_factory.cli_updates.toolchain._tool',return_value={'available':True,'path':'/path/codex','version':'codex-cli '+version}):
                    self.assertEqual(manager.status()['tools'][0]['update_status'],expected)
            manager._cache['codex']['latest_attempt']['status']='failed'
            with patch('analog_factory.cli_updates.toolchain._tool',return_value={'available':True,'path':'/path/codex','version':'codex-cli 1.10.0'}):
                self.assertEqual(manager.status()['tools'][0]['update_status'],'unknown')

    def test_network_timeout_is_bounded_and_publishes_only_generic_failure(self):
        import threading,time
        from analog_factory.operations import OperationError
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root);finish=threading.Event();entered=threading.Event()
            def fetch(*args):entered.set();finish.wait(1);raise OSError('PRIVATE_DNS_DETAILS')
            with patch('analog_factory.cli_updates.TIMEOUT',.05),patch.object(manager,'_fetch',side_effect=fetch):
                started=time.monotonic()
                try:
                    with self.assertRaises(OperationError):manager.check_updates(['codex'])
                    self.assertLess(time.monotonic()-started,.5)
                    self.assertTrue(entered.is_set())
                    self.assertNotIn('PRIVATE_DNS',json.dumps(manager.releases()))
                    self.assertEqual(manager.releases()['backends'][0]['latest_attempt']['status'],'failed')
                finally:finish.set()

    def test_registry_emits_release_change_only_for_persisted_new_attempts(self):
        from analog_factory.service import Workspace
        from analog_factory.operations import InvocationContext
        with tempfile.TemporaryDirectory() as root:
            workspace=Workspace(Path(root));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            manager=workspace.cli_updates
            context=InvocationContext(client_id='cli-check-test')
            def changes():return [e for e in registry.subscribe_events()['events'] if e['operation_id']=='agent.cli.releases']
            with patch.object(manager,'_fetch',return_value=self.release()):
                self.assertTrue(registry.invoke('agent.cli.check_updates',{'backends':['codex']},context)['ok'])
                self.assertEqual(len(changes()),1)
                self.assertTrue(registry.invoke('agent.cli.check_updates',{'backends':['codex']},context)['ok'])
                self.assertEqual(len(changes()),1,'Cooldown/cache hits are not new attempts')
            with patch.object(manager,'_fetch',side_effect=OSError('PRIVATE_NOTES')):
                self.assertFalse(registry.invoke('agent.cli.check_updates',{'backends':['claude']},context)['ok'])
            self.assertEqual(len(changes()),2,'Persisted failures must refresh observers too')
            self.assertNotIn('PRIVATE_NOTES',json.dumps(changes()));self.assertNotIn('unsafe',json.dumps(changes()))
            self.assertTrue(all(e['status']=='changed' and e['client_id']=='cli-check-test' for e in changes()))
            manager.close()
            self.assertFalse(registry.invoke('agent.cli.check_updates',{'backends':['codex']},context)['ok'])
            self.assertEqual(len(changes()),2)

    def test_concurrent_registry_checks_emit_one_change_and_closed_inflight_emits_none(self):
        import threading
        from analog_factory.service import Workspace
        with tempfile.TemporaryDirectory() as root:
            workspace=Workspace(Path(root));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            manager=workspace.cli_updates;entered=threading.Event();finish=threading.Event();results=[]
            def fetch(backend):entered.set();finish.wait(1);return self.release(backend)
            def invoke(backend):results.append(registry.invoke('agent.cli.check_updates',{'backends':[backend]}))
            with patch.object(manager,'_fetch',side_effect=fetch):
                first=threading.Thread(target=invoke,args=('codex',));second=threading.Thread(target=invoke,args=('codex',))
                first.start();self.assertTrue(entered.wait(1));second.start();finish.set();first.join(1);second.join(1)
            self.assertEqual(len(results),2);self.assertTrue(all(r['ok'] for r in results))
            events=lambda:[e for e in registry.subscribe_events()['events'] if e['operation_id']=='agent.cli.releases']
            self.assertEqual(len(events()),1)
            entered.clear();finish.clear();results.clear()
            with patch.object(manager,'_fetch',side_effect=fetch):
                pending=threading.Thread(target=invoke,args=('claude',));pending.start();self.assertTrue(entered.wait(1))
                manager.close();finish.set();pending.join(1)
            self.assertFalse(pending.is_alive());self.assertFalse(results[0]['ok']);self.assertEqual(len(events()),1)

    def test_journal_failure_does_not_replace_real_network_failure(self):
        from analog_factory.service import Workspace
        with tempfile.TemporaryDirectory() as root:
            workspace=Workspace(Path(root));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            with patch.object(workspace.cli_updates,'_fetch',side_effect=OSError('PRIVATE_NETWORK')),patch.object(registry,'_event',side_effect=OSError('PRIVATE_EVENT')):
                result=registry.invoke('agent.cli.check_updates',{'backends':['codex']})
            self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'UNAVAILABLE')
            self.assertNotIn('PRIVATE_',json.dumps(result))

    def test_existing_environment_proxy_is_honored_without_leaking_proxy_errors(self):
        import os
        import urllib.request
        from analog_factory.operations import OperationError
        with tempfile.TemporaryDirectory() as root:
            manager=self.manager(root);seen=[]
            def opener(*handlers):
                seen.extend(handlers)
                class FailingOpener:
                    def open(self,*args,**kwargs):raise OSError('PRIVATE_PROXY_USER:PRIVATE_PROXY_PASS@proxy.invalid')
                return FailingOpener()
            with patch.dict(os.environ,{'https_proxy':'http://PRIVATE_PROXY_USER:PRIVATE_PROXY_PASS@proxy.invalid:8080'},clear=True),patch('analog_factory.cli_updates.urllib.request.build_opener',side_effect=opener):
                with self.assertRaises(OperationError) as failure:manager.check_updates(['codex'])
            proxy=next(h for h in seen if isinstance(h,urllib.request.ProxyHandler))
            self.assertEqual(proxy.proxies.get('https'),'http://PRIVATE_PROXY_USER:PRIVATE_PROXY_PASS@proxy.invalid:8080')
            self.assertNotIn('PRIVATE_PROXY',str(failure.exception));self.assertNotIn('proxy.invalid',json.dumps(manager.releases()))
