"""Disposable browser-test server with a newly registered example operation."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analog_factory.operations import Operation
from analog_factory.server import create_server
from analog_factory.service import Workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8878)
    parser.add_argument('--ready-file', type=Path, help='Exclusively create an owned server identity receipt')
    parser.add_argument('--agent-cli-fixture', action='store_true', help='Offline public CLI release observations; no network checks')
    args = parser.parse_args()
    workspace = Workspace(args.workspace)
    workspace.operations.register(Operation(
        operation_id='example.project_summary', name='Project source summary',
        description='Count visible source files in the selected project.', module='example',
        input_schema={'type': 'object', 'properties': {'project_id': {'type': 'string'}},
                      'required': ['project_id'], 'additionalProperties': False},
        output_schema={'type': 'object', 'properties': {'file_count': {'type': 'integer', 'minimum': 0}},
                       'required': ['file_count'], 'additionalProperties': False},
        handler=lambda context, parameters: {'file_count': len(workspace.projects.files(parameters['project_id']))},
        kind='query', permissions=('read',), idempotent=True,
    ))
    if args.agent_cli_fixture:
        from analog_factory.cli_updates import SOURCES, parse_release
        manager=workspace.cli_updates
        for backend,(repo,prefix,_) in SOURCES.items():
            value=parse_release(backend,{'tag_name':prefix+'2.0.0',
                'html_url':f'https://github.com/{repo}/releases/tag/{prefix}2.0.0',
                'published_at':'2020-01-01T00:00:00Z','draft':False,'prerelease':False,
                'body':'Offline fixture notes <script>throw new Error("unsafe")</script>'})
            value['checked_at']='2020-01-01T00:00:00Z'
            manager._cache[backend]={'latest_success':value,'latest_attempt':{
                'checked_at':'2020-01-01T00:00:00Z','status':'succeeded','error':None}}
        def offline_release(backend):
            raise OSError('Offline release fixture')
        manager._fetch=offline_release
    server = create_server(workspace, port=args.port)
    try:
        if args.ready_file:
            with args.ready_file.open('x', encoding='utf-8') as receipt:
                json.dump({'pid':os.getpid(),'port':server.server_port,
                    'source':str(Path(__file__).resolve().parents[1]),
                    'workspace':str(args.workspace.resolve())},receipt)
                receipt.write('\n');receipt.flush();os.fsync(receipt.fileno())
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
