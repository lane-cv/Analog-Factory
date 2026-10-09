"""Discovery remains traversable when catalogs and metadata exceed model budgets."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest

from analog_factory.agent_api import _context, _execute, MAX_TOOL_BYTES, SCHEMAS
from analog_factory.operation_mcp import _dispatch
from analog_factory.service import Workspace


class OperationDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.workspace=Workspace(Path(temporary.name));self.registry=self.workspace.operations
        self.addCleanup(self.workspace.agents.close);self.addCleanup(self.registry.close)
        self.pid=self.workspace.projects.create('Discovery')['id']
        self.context=_context(self.pid)

    def page(self,**options):
        return self.registry.list_operations_page(context=self.context,parameters={'project_id':self.pid},**options)

    def execute(self,name,arguments):
        return _execute(name,arguments,self.workspace,self.pid,'confirm',lambda event:None,
                        threading.Event(),lambda *args:False)

    def add(self,identifier,**options):
        return self.registry.register(replace(self.registry.operations['project.detail'],
            operation_id=identifier,module='future',**options))

    def test_growth_unicode_reasons_and_byte_bound_preserve_every_operation_once(self):
        for index in range(350):
            self.add(f'future.entrya{index:04d}',name='新功能'*100,description='"\\\n新操作'*100,
                     availability=lambda context,parameters:['模型未提供\\"'*700]*8)
        expected=sorted(self.registry.operations);seen=[];cursor=None;pages=[]
        while True:
            page=self.page(cursor=cursor,limit=100)
            self.assertLessEqual(len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode()),49152)
            self.assertLess(len(json.dumps(page,ensure_ascii=True).encode()),1024*1024)
            self.assertGreater(page['returned'],0)
            self.assertEqual(page['total'],len(expected));self.assertEqual(page['offset'],len(seen))
            pages.append(page);seen.extend(row['operation_id'] for row in page['operations'])
            cursor=page['next_cursor']
            self.assertEqual(page['complete'],cursor is None)
            if cursor is None:break
            self.assertLess(len(pages),len(expected))
        self.assertEqual(seen,expected)
        self.assertTrue(any(page['returned']<100 and not page['complete'] for page in pages))
        row=next(row for page in pages for row in page['operations'] if row['module']=='future')
        self.assertTrue(row['summary_truncated']);self.assertFalse(row['availability']['available'])
        self.assertEqual(row['availability']['reason_count'],8)

    def test_cursor_binds_catalog_filter_project_principal_and_permissions(self):
        page=self.page(limit=1);cursor=page['next_cursor']
        # Item limits can vary without skipping the next item.
        next_page=self.page(cursor=cursor,limit=2)
        self.assertEqual(next_page['offset'],1)
        for arguments in ({'module':'file'},{'keyword':'read'}):
            with self.assertRaisesRegex(ValueError,'cursor'):
                self.page(cursor=cursor,**arguments)
        for changed in (replace(self.context,principal='different'),replace(self.context,project_id=None),
                        replace(self.context,permissions=frozenset({'read'}))):
            with self.assertRaisesRegex(ValueError,'cursor'):
                self.registry.list_operations_page(context=changed,parameters={'project_id':self.pid},cursor=cursor)
        self.add('future.newentry')
        with self.assertRaisesRegex(ValueError,'cursor'):
            self.page(cursor=cursor)

    def test_invalid_limits_cursors_and_oversize_item_fail_without_false_completion(self):
        for limit in (0,-1,101,True,1.5):
            with self.assertRaises(ValueError):self.page(limit=limit)
        for cursor in ('bad','x'*1025,True,{},'a'*64+'.-1','a'*64+'.1.5'):
            with self.assertRaises(ValueError):self.page(cursor=cursor)
        cursor=self.page(limit=1)['next_cursor'].split('.')[0]+'.999999999'
        with self.assertRaisesRegex(ValueError,'cursor'):self.page(cursor=cursor)
        self.add('future.'+'a'*60000)
        with self.assertRaisesRegex(ValueError,'summary.*limit'):
            self.page(module='future')

    def test_agent_and_mcp_share_pagination_schema_and_structured_pages(self):
        schema=SCHEMAS['list_operations']
        mcp=next(row for row in _dispatch({'method':'tools/list'})['tools'] if row['name']=='list_operations')
        self.assertEqual(mcp['inputSchema'],schema)
        self.assertIn('cursor',schema['properties']);self.assertIn('limit',schema['properties'])
        first=self.execute('list_operations',{'limit':1})
        second=self.execute('list_operations',{'cursor':first['next_cursor'],'limit':1})
        self.assertEqual(second['offset'],1)
        self.assertNotEqual(first['operations'][0]['operation_id'],second['operations'][0]['operation_id'])
        self.add('future.hugedescription',description='字'*MAX_TOOL_BYTES)
        result=self.execute('describe_operation',{'operation_id':'future.hugedescription'})
        self.assertEqual(result['error']['code'],'DISCOVERY_ITEM_TOO_LARGE')
        self.assertNotIn('preview',result);self.assertNotIn('input_schema',result)

    def test_scoped_project_defaults_and_live_availability_match_full_discovery(self):
        ready=[True]
        def availability(context,parameters):
            return [] if parameters.get('project_id')==self.pid and ready[0] else ['Prerequisite changed']
        for name in ('first','second'):self.add('future.'+name,availability=availability)
        page=self.registry.list_operations_page(context=self.context,module='future',limit=1)
        self.assertTrue(page['operations'][0]['availability']['available'])
        ready[0]=False
        next_page=self.registry.list_operations_page(context=self.context,module='future',cursor=page['next_cursor'])
        self.assertFalse(next_page['operations'][0]['availability']['available'])
        self.assertTrue(next_page['complete'])


if __name__=='__main__':unittest.main()
