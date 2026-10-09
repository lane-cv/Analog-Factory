#!/usr/bin/env python3
"""Provision a fresh real OTA, then characterize through the shared registry."""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.validate_ota5 import configuration,validate as validate_ota
from analog_factory.service import Workspace
from analog_factory.operations import InvocationContext
from analog_factory.operation_characterization import register_characterization


def validate(workspace_path,config,spec):
    path=Path(workspace_path).resolve()
    if path==(ROOT/'.workspace').resolve():raise ValueError('Use an isolated validation workspace')
    acceptance=validate_ota(path,config,template=True)
    if acceptance['status']!='pass':raise RuntimeError('Original nominal OTA acceptance failed')
    ws=Workspace(path);registry=ws.operations
    try:
        if 'reference.characterize' not in registry.operations:register_characterization(registry)
        pid=acceptance['project_id'];context=InvocationContext(principal='ota-characterization-validation',source='cli')
        envelope=registry.invoke('reference.characterize',{'project_id':pid,'spec':spec},context,expected_version=registry.version(pid))
        if not envelope['ok']:raise RuntimeError(envelope['error'])
        deadline=time.monotonic()+1805;job=envelope['data']
        while job['status'] in ('queued','running'):
            if time.monotonic()>deadline:
                registry.invoke('job.cancel',{'project_id':pid,'job_id':job['id']},context)
                raise TimeoutError('Characterization deadline exceeded')
            time.sleep(.1)
            response=registry.invoke('job.get',{'project_id':pid,'job_id':job['id']},context)
            if not response['ok']:raise RuntimeError(response['error'])
            job=response['data']
        report={'schema_version':1,'project_id':pid,'baseline_status':acceptance['status'],
                'baseline_report':'ota5-validation.json','job':job}
        (path/'ota5-characterization.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
        return report
    finally:
        ws.agents.close();registry.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',required=True)
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--spec',help='JSON characterization specification; default runs all nominal analyses')
    args=parser.parse_args()
    specification=json.loads(Path(args.spec).read_text()) if args.spec else {'targets':{'gain_db_min':30}}
    report=validate(args.workspace,configuration(args.settings),specification)
    job=report['job'];print(json.dumps({'report':str(Path(args.workspace)/'ota5-characterization.json'),'status':job['status'],'electrical_status':job['electrical_status'],'completed_runs':job['completed_runs'],'groups':job['groups']},indent=2))
    sys.exit(0 if job['status']=='succeeded' and job['electrical_status'] in ('pass','observed') else 1)
