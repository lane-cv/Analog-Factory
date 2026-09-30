import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from analog_factory import agent_models


@unittest.skipUnless(os.name == 'posix', 'CLI discovery uses POSIX process groups')
class AgentModelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.executable = self.root / 'fake-cli'
        agent_models._CACHE.clear()

    def fixture(self, code):
        self.executable.write_text(f'#!{sys.executable}\n'
                                   'import json, os, sys, time\nfrom pathlib import Path\n'
                                   'def emit(data): print(json.dumps(data), flush=True)\n' + code)
        self.executable.chmod(0o700)

    def catalog(self, backend='codex', **kwargs):
        return agent_models.catalog(backend, self.executable, self.root, **kwargs)

    def successful_codex(self):
        self.fixture('''
with Path('requests.jsonl').open('a') as log:
 for line in sys.stdin:
  request=json.loads(line); log.write(line); log.flush()
  method=request['method']
  if method=='initialize': emit({'id':request['id'],'result':{}})
  elif method=='model/list':
   if request['params'].get('cursor') is None:
    emit({'method':'catalog/status','params':{}})
    emit({'id':request['id'],'result':{'data':[
     {'id':'native','model':'native','displayName':'Native model','isDefault':True,
      'serviceTiers':[{'id':'priority','name':'Fast'}]},
     {'id':'hidden','hidden':True}, {'id':'bad\\nmodel'}], 'nextCursor':'page-2'}})
   else: emit({'id':request['id'],'result':{'data':[
    {'id':'slow','displayName':'Slow model'},
    {'id':'legacy','additionalSpeedTiers':['fast']}], 'nextCursor':None}})
  elif method!='initialized': raise AssertionError(method)
''')

    def test_codex_discovers_paginated_catalog_without_turn_or_config(self):
        self.successful_codex()
        result = self.catalog()
        self.assertEqual(result['source'], 'cli')
        self.assertEqual([model['id'] for model in result['models']], ['native', 'slow', 'legacy'])
        self.assertTrue(result['models'][0]['is_default'])
        self.assertEqual([model['fast_mode'] for model in result['models']], [True, False, True])
        self.assertTrue(result['fast_mode']['supported'])
        requests = [json.loads(line) for line in (self.root / 'requests.jsonl').read_text().splitlines()]
        self.assertEqual([request['method'] for request in requests],
                         ['initialize', 'initialized', 'model/list', 'model/list'])
        self.assertFalse(requests[2]['params']['includeHidden'])

    def test_cache_refresh_executable_change_and_return_copy(self):
        self.successful_codex()
        first = self.catalog()
        first['models'].clear()
        self.assertEqual(len(self.catalog()['models']), 3)
        self.assertEqual(len((self.root / 'requests.jsonl').read_text().splitlines()), 4)
        self.catalog(refresh=True)
        self.assertEqual(len((self.root / 'requests.jsonl').read_text().splitlines()), 8)
        self.fixture("print('2.1.205 (Claude Code)')\n")
        self.assertEqual(self.catalog('claude')['source'], 'builtin')

    def test_failures_do_not_expose_cli_stderr_or_protocol_errors(self):
        self.fixture("print('private credential detail', file=sys.stderr)\nemit({'id':1,'error':{'message':'private credential detail'}})\ntime.sleep(10)\n")
        result = self.catalog()
        self.assertEqual(result['source'], 'unavailable')
        self.assertFalse(result['fast_mode']['supported'])
        self.assertNotIn('private', json.dumps(result))

    def test_timeout_and_output_are_bounded(self):
        self.fixture('time.sleep(10)\n')
        started = time.monotonic()
        with patch.object(agent_models, '_TIMEOUT', .1):
            self.assertEqual(self.catalog()['source'], 'unavailable')
        self.assertLess(time.monotonic() - started, 2)
        self.fixture("print('x'*50000, flush=True)\ntime.sleep(10)\n")
        with patch.object(agent_models, '_MAX_OUTPUT', 1000):
            self.assertEqual(self.catalog(refresh=True)['source'], 'unavailable')

    def test_discovery_kills_descendants_after_parent_exits(self):
        self.fixture('''
import subprocess
subprocess.Popen([sys.executable, '-c', "import time; from pathlib import Path; time.sleep(.7); Path('escaped-child').touch()"])
''')
        with patch.object(agent_models, '_TIMEOUT', .1):
            self.assertEqual(self.catalog()['source'], 'unavailable')
        time.sleep(.8)
        self.assertFalse((self.root / 'escaped-child').exists())

    def test_codex_bounds_large_catalog_and_ignores_malformed_tier_metadata(self):
        self.fixture('''
for line in sys.stdin:
 req=json.loads(line)
 if req['method']=='initialize': emit({'id':req['id'],'result':{}})
 elif req['method']=='model/list':
  cursor=int(req['params'].get('cursor') or 0)
  rows=[{'id':str(index), 'serviceTiers':[{'id':[]}]} for index in range(cursor,cursor+100)]
  emit({'id':req['id'],'result':{'data':rows,'nextCursor':str(cursor+100)}})
''')
        result = self.catalog()
        self.assertEqual(len(result['models']), 200)
        self.assertFalse(result['fast_mode']['supported'])

    def test_pagination_cycle_and_malformed_catalog_fail_safely(self):
        for result in ({'data': [], 'nextCursor': 'same'}, {'data': 'not-a-list'}):
            self.fixture('''
for line in sys.stdin:
 req=json.loads(line)
 if req['method']=='initialize': emit({'id':req['id'],'result':{}})
 elif req['method']=='model/list': emit({'id':req['id'],'result':''' + repr(result) + '''})
''')
            self.assertEqual(self.catalog(refresh=True)['source'], 'unavailable')

    def test_claude_aliases_version_gate_and_environment_switch(self):
        self.fixture("assert sys.argv[1:]==['--version']\nprint('2.1.205 (Claude Code)')\n")
        result = self.catalog('claude')
        self.assertEqual(result['source'], 'builtin')
        self.assertEqual([model['id'] for model in result['models']], ['opus', 'sonnet', 'haiku'])
        self.assertEqual([model['fast_mode'] for model in result['models']], [True, False, False])
        with patch.dict(os.environ, {'CLAUDE_CODE_DISABLE_FAST_MODE': '1'}):
            self.assertFalse(self.catalog('claude')['fast_mode']['supported'])
        self.fixture("print('2.1.204 (Claude Code)')\n")
        self.assertFalse(self.catalog('claude')['fast_mode']['supported'])

    def test_failed_cache_expires_earlier_than_success(self):
        self.fixture("raise SystemExit(1)\n")
        with patch.object(agent_models.time, 'monotonic', return_value=100):
            with patch.object(agent_models, '_discover', return_value={'source': 'unavailable'}) as discover:
                self.catalog()
                self.catalog()
                self.assertEqual(discover.call_count, 1)
        with patch.object(agent_models.time, 'monotonic', return_value=131):
            with patch.object(agent_models, '_discover', return_value={'source': 'cli'}) as discover:
                self.catalog()
                self.assertEqual(discover.call_count, 1)

    def test_missing_executable_and_unknown_backend(self):
        self.assertEqual(self.catalog()['source'], 'unavailable')
        with self.assertRaises(ValueError):
            self.catalog('unknown')


if __name__ == '__main__':
    unittest.main()
