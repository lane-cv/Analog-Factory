#!/usr/bin/env python3
"""Real registered qualification and sampling of explicit experimental semantics."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analog_factory.service import Workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--simulator', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    library, simulator = args.library.resolve(strict=True), args.simulator.resolve(strict=True)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    workspace = Workspace(output / 'runtime')
    pid = workspace.projects.create('Experimental global FET cache validation')['id']
    workspace.configure_project(pid, {'pdk_root':str(library.parents[3]), 'tools':{'ngspice':str(simulator)}})
    source = (f'* Actual SKY130 adapted global-process sample\n.lib "{library}" tt\n'
              'VD dn 0 .9\nVG gn 0 .8\nX1 dn gn 0 0 sky130_fd_pr__nfet_01v8 w=1 l=.15\n.op\n.end\n')
    workspace.projects.write(pid, 'testbenches/pre.cir', source)
    evidence = {'project_id':pid, 'library':str(library), 'simulator':str(simulator),
                'model_semantics':'cache_global_fet_draws','status':'running', 'source_sha256':hashlib.sha256(source.encode()).hexdigest()}
    def save():
        (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    def invoke(name, parameters):
        result = workspace.operations.invoke(name, {'project_id':pid, **parameters},
                                             expected_version=workspace.operations.version(pid))
        if not result['ok']:
            raise RuntimeError(json.dumps(result['error']))
        return result['data']
    def wait(job):
        last = None
        deadline = time.monotonic()+1000
        while True:
            current = workspace.jobs.get(workspace.projects.path(pid),job['id'])
            progress = (current['status'],current.get('completed_runs',0))
            if progress != last:
                print(json.dumps({'job_id':job['id'],'status':progress[0],'completed_runs':progress[1]}),flush=True)
                last = progress
            if current['status'] not in ('queued','running'):
                return current
            if time.monotonic() > deadline:
                workspace.jobs.cancel(workspace.projects.path(pid),job['id'])
                raise RuntimeError('Validation wait deadline exceeded; cancellation requested')
            time.sleep(.25)
    try:
        qualified = wait(invoke('simulation.statistics.qualify', {'library':str(library),
            'kind':'global_process','section':'mc','model_semantics':'cache_global_fet_draws'}))
        evidence['qualification'] = qualified
        save()
        if qualified['status'] != 'succeeded' or not qualified['qualification']['qualified']:
            raise RuntimeError('Fresh full-library statistical qualification did not pass')
        spec={'analysis':{'type':'op'},'corners':{'library':str(library),'sections':['mc']},
              'statistics':{'kind':'global_process','samples':2,'seed':73,'qualification_job':qualified['id'],
                            'model_semantics':'cache_global_fet_draws'},
              'measurements':[{'kind':'power','name':'power_w','supply_node':'dn','supply_source':'VD'}],
              'limits':{'runtime_seconds':500,'run_timeout':180}}
        # A qualified adapter does not silently authorize the default semantics.
        installed=json.loads(json.dumps(spec));installed['statistics'].pop('model_semantics')
        rejected=workspace.operations.invoke('simulation.batch.preview',{'project_id':pid,'spec':installed})
        evidence['installed_semantics_rejected']=not rejected['ok']
        if rejected['ok']:
            raise RuntimeError('Installed semantics incorrectly reused an adapted certificate')
        batch=wait(invoke('simulation.batch.start',{'spec':spec}))
        evidence['batch']=batch
        if batch['status'] != 'succeeded' or len(batch['iterations']) != 2:
            raise RuntimeError('Fresh statistical batch did not complete')
        powers=[item['metrics']['power_w'] for item in batch['iterations']]
        evidence['sample_powers_w']=powers
        if powers[0] == powers[1]:
            raise RuntimeError('Fresh seeds did not change the measured sample power')
        evidence['source_unchanged']=workspace.projects.read(pid,'testbenches/pre.cir')['content']==source
        if not evidence['source_unchanged']:
            raise RuntimeError('Validation changed the project source')
        evidence['status']='succeeded'
        save()
        print(json.dumps({'evidence':str(output/'evidence.json'),'status':evidence['status'],
                          'checks':qualified['qualification']['checks'],'sample_powers_w':powers}),flush=True)
        return 0
    except Exception as error:
        evidence.update(status='failed',error=str(error))
        save()
        print(json.dumps({'evidence':str(output/'evidence.json'),'status':'failed','error':str(error)}),flush=True)
        return 1
    finally:
        workspace.agents.close()
        workspace.operations.close()


if __name__ == '__main__':
    raise SystemExit(main())
