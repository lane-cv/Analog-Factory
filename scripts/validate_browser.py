#!/usr/bin/env python3
"""Run browser acceptance against a frozen checkout and an owned disposable server.

Inherits Node/Playwright dependency environment; installs nothing. Real native
canvas/browser and actual DRC acceptance remain separate gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_ubuntu24 import SOURCE_ROOTS, copy_source, digest

TESTS = ('operations','operation-extension','agent-models','lifecycle','waveform-analysis',
    'graph-controls','workflow-editor','source-patch','editor-recovery','editor-ownership',
    'editor-version-race','simulation-batches','characterization','training',
    'provider-controls','agent-capabilities','workflow-io','phase-margin','project-settings',
    'project-configuration','transfers','native-terminal','agents','agent-cli',
    'first-user','characterization-history','ordinary-comparison','design-navigation','native-recovery')


def now():
    return datetime.now(timezone.utc).isoformat()


def stop_owned(process):
    """Drain only a process group created by this driver with start_new_session."""
    try:
        os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        process.wait(timeout=5)
    # A failed leader may have left an owned child in the group.
    try:
        os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:
        pass
    return process.returncode


def wait_ready(process,ready_file,source,workspace,*,timeout=45):
    """No HTTP request precedes validation of the live owned child's receipt."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Owned server exited before readiness: {process.returncode}')
        try:
            data=json.loads(Path(ready_file).read_text())
        except (FileNotFoundError,json.JSONDecodeError):
            time.sleep(.05)
            continue
        if (not isinstance(data,dict) or data.get('pid')!=process.pid
                or data.get('source')!=str(Path(source).resolve())
                or data.get('workspace')!=str(Path(workspace).resolve())
                or type(data.get('port')) is not int or not 1<=data['port']<=65535):
            raise RuntimeError('Ready-file identity does not match the owned server')
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{data['port']}/api/operations",timeout=1):
                pass
        except OSError:
            time.sleep(.05)
            continue
        if process.poll() is not None:
            raise RuntimeError('Owned server exited while checking readiness')
        return data
    raise TimeoutError('Owned server did not publish a valid ready receipt before timeout')


def run_step(name,command,source,env,log,*,timeout=240):
    start=time.monotonic();process=None
    row={'name':name,'command':command,'status':'error','exit_code':127,
         'log':str(log),'started_at':now()}
    with Path(log).open('w') as stream:
        try:
            process=subprocess.Popen(command,cwd=source,env=env,stdout=stream,
                stderr=subprocess.STDOUT,start_new_session=True)
            row['pid']=process.pid
            try:
                row['exit_code']=process.wait(timeout=timeout)
                row['status']='passed' if row['exit_code']==0 else 'failed'
            except subprocess.TimeoutExpired:
                row.update(status='timeout',exit_code=124)
        except OSError as error:
            row['error']=str(error);stream.write(str(error)+'\n')
        finally:
            if process is not None:
                try:row['process_exit_code']=stop_owned(process)
                except (OSError,subprocess.TimeoutExpired) as error:
                    row.update(status='error',exit_code=125,cleanup_error=str(error))
    row['elapsed_s']=time.monotonic()-start
    return row


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT,help='Checkout to freeze from the explicit source allowlist')
    parser.add_argument('--output',type=Path,required=True,help='New evidence directory; existing directories are refused')
    parser.add_argument('--test',action='append',choices=TESTS,help='Allowlisted subset; default runs all registered browser tests')
    parser.add_argument('--node',default='node',help='Installed Node executable; dependencies are inherited')
    parser.add_argument('--timeout',type=float,default=240,help='Per-test runtime bound in seconds')
    parser.add_argument('--ready-timeout',type=float,default=45)
    args=parser.parse_args(argv)
    if args.timeout<=0 or args.ready_timeout<=0:parser.error('Timeouts must be positive')
    source=args.source.resolve(strict=True);output=args.output.resolve()
    if output==source or any(output.is_relative_to(source/name) for name in SOURCE_ROOTS):
        parser.error('Output must be outside the allowlisted source trees')
    try:output.mkdir(parents=True,exist_ok=False)
    except FileExistsError:parser.error('Output must be a new directory')
    frozen=output/'source';workspace=output/'workspace';ready=output/'server-ready.json'
    selected=list(dict.fromkeys(args.test or TESTS))
    report={'started_at':now(),'status':'running','source_input':str(source),
        'frozen_source':str(frozen),'workspace':str(workspace),'source_allowlist':list(SOURCE_ROOTS),
        'selected_tests':selected,'full_default_suite':selected==list(TESTS),
        'separate_gates':['actual DRC','real native canvas/browser'],
        'results':[{'name':name,'status':'not_run','exit_code':None,'log':str(output/(name+'.log'))} for name in selected]}
    def save():
        (output/'evidence.json').write_text(json.dumps(report,indent=2)+'\n')
        (output/'results.json').write_text(json.dumps(report['results'],indent=2)+'\n')
    save();server=None
    try:
        manifest=copy_source(source,frozen)
        for name in ['tests/operation_test_server.py','analog_factory/__init__.py',
                     *('tests/browser-'+name+'.cjs' for name in selected)]:
            if name not in manifest:raise RuntimeError(f'Missing allowlisted source: {name}')
        manifest_file=output/'source-manifest.json'
        manifest_file.write_text(json.dumps(manifest,indent=2)+'\n')
        report['source_manifest_sha256']=digest(manifest_file)
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
        command=[sys.executable,'tests/operation_test_server.py','--workspace',str(workspace),
                 '--port','0','--ready-file',str(ready)]
        if 'agent-cli' in selected:
            command.append('--agent-cli-fixture')
            report['cli_releases']='offline public-release fixture; real official endpoint checks are a separate validation'
        with (output/'server.log').open('w') as log:
            server=subprocess.Popen(command,cwd=frozen,env=env,stdout=log,
                stderr=subprocess.STDOUT,start_new_session=True)
            report['server']={'pid':server.pid,'command':command,'log':str(output/'server.log')};save()
            receipt=wait_ready(server,ready,frozen,workspace,timeout=args.ready_timeout)
            report['server']['ready']=receipt
            report['server']['ready_sha256']=digest(ready)
            env['AF_URL']=f"http://127.0.0.1:{receipt['port']}";save()
            for index,name in enumerate(selected):
                if server.poll() is not None:raise RuntimeError('Owned server exited during browser acceptance')
                report['results'][index]=run_step(name,[args.node,'tests/browser-'+name+'.cjs'],
                    frozen,env,output/(name+'.log'),timeout=args.timeout)
                save();print(name,report['results'][index]['status'],report['results'][index]['exit_code'],flush=True)
            changed=[name for name,sha in manifest.items() if not (frozen/name).is_file() or digest(frozen/name)!=sha]
            report['frozen_source_changed']=changed
            if changed:raise RuntimeError('Frozen source changed while running browser acceptance')
            if server.poll() is not None:raise RuntimeError('Owned server exited before acceptance completion')
        report['status']='passed' if all(row['status']=='passed' for row in report['results']) else 'failed'
    except Exception as error:
        report.update(status='failed',error=f'{type(error).__name__}: {error}')
    finally:
        if server is not None:
            try:report['server']['cleanup_exit_code']=stop_owned(server)
            except (OSError,subprocess.TimeoutExpired) as error:
                report['server']['cleanup_error']=str(error);report['status']='failed'
        report['finished_at']=now();save()
    print(f"Browser validation {report['status']}; evidence: {output}",flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
