"""Bounded real-ngspice scenario batches with explicit measurement provenance."""
import hashlib
import itertools
import json
import math
import os
import random
import re
import stat
import shlex
import time
import weakref
from pathlib import Path

from .operation_schema import validate
from .simulation import configure
from .storage import confined
from .toolchain import detect
from .statistical_models import SEMANTICS, prepare_global_models
from .workflows import invoke_engine, replace_parameter


MAX_RUNS = 128
MAX_GENERATED_BYTES = 32 * 1024 * 1024
# A durable task descriptor or imported JSON is evidence, not current authority.
# Only successful probes in this registry lifetime populate these certificates.
_LIVE_QUALIFICATIONS = weakref.WeakKeyDictionary()
NAME = {'type':'string', 'pattern':'^[A-Za-z][A-Za-z0-9_]{0,40}$'}
NODE = {'type':'string', 'pattern':'^(?:[A-Za-z_][A-Za-z0-9_.$]*|[0-9]+)$', 'maxLength':100}
NUMBER = {'type':'number'}
VALUE = {'oneOf':[NUMBER, {'type':'string','maxLength':40}]}


def obj(properties=None, required=(), **extra):
    return {'type':'object','properties':properties or {},'required':list(required),
            'additionalProperties':False, **extra}


def array(items, maximum=MAX_RUNS, **extra):
    return {'type':'array','items':items,'maxItems':maximum, **extra}


AXIS = obj({'parameter':NAME,'values':array(NUMBER,32,minItems=1)}, ('parameter','values'))
MEASUREMENT = obj({
    'kind':{'type':'string','enum':['gain','bandwidth','phase','phase_margin','power','swing']}, 'name':NAME,
    'output':NODE,'input':NODE,'input_negative':NODE,'output_negative':NODE,
    'frequency_hz':{'type':'number','exclusiveMinimum':0},
    'reference_hz':{'type':'number','exclusiveMinimum':0},
    'loop_gain_convention':{'type':'string','enum':['negative_feedback_L'],
        'description':'Explicit declaration that output/input is L(s) in the negative-feedback characteristic equation 1+L(s)=0; not a closed-loop response'},
    'drop_db':{'type':'number','exclusiveMinimum':0,'maximum':100},
    'supply_node':NODE,'supply_source':NAME,
    'start_s':{'type':'number','minimum':0},'stop_s':{'type':'number','exclusiveMinimum':0},
}, ('kind','name'))
STATISTICS = obj({
    'kind':{'type':'string','enum':['none','engineering_parameter','global_process','device_mismatch'],'default':'none'},
    'samples':{'type':'integer','minimum':1,'maximum':MAX_RUNS,'default':1},
    'seed':{'type':'integer','minimum':1,'maximum':2147483000,'default':1},
    'qualification_job':{'type':'string','pattern':'^[a-f0-9]{32}$'},
    'model_semantics':SEMANTICS,
    'variables':array(obj({'parameter':NAME,'distribution':{'type':'string','enum':['uniform','normal']},
                           'minimum':NUMBER,'maximum':NUMBER,'mean':NUMBER,
                           'sigma':{'type':'number','exclusiveMinimum':0}},
                          ('parameter','distribution','minimum','maximum')),8,default=[]),
}, default={})
BATCH_SCHEMA = obj({
    'source':{'type':'string','minLength':1,'maxLength':4096,'default':'testbenches/pre.cir'},
    'analysis':obj({'type':{'type':'string','enum':['op','dc','ac','tran','noise']},
                    'parameters':obj({name:VALUE for name in ('source','start','stop','step','points','output')}, default={})}, ('type',)),
    'axes':array(AXIS,3,default=[]),
    'temperatures_c':array({'type':'number','minimum':-273.15,'maximum':500},16,minItems=1,default=[27]),
    'supply':AXIS,
    'corners':obj({'library':{'type':'string','minLength':1,'maxLength':4096},
                   'sections':array(NAME,16,minItems=1,uniqueItems=True)}, ('library','sections')),
    'measurements':array(MEASUREMENT,16,minItems=1),
    'statistics':STATISTICS,
    'limits':obj({'max_runs':{'type':'integer','minimum':1,'maximum':MAX_RUNS,'default':MAX_RUNS},
                  'runtime_seconds':{'type':'number','minimum':1,'maximum':1800,'default':300},
                  'run_timeout':{'type':'number','minimum':.1,'maximum':300,'default':60}}, default={}),
}, ('analysis','measurements'))


def _spice_number(value, label):
    if type(value) in (int,float):
        number = float(value)
    else:
        match = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|[tgkmunpf])?', str(value), re.I)
        if not match:
            raise ValueError(label + ' must be a finite number or SPICE engineering value')
        number = float(match[1]) * {'t':1e12,'g':1e9,'meg':1e6,'k':1e3,'m':1e-3,'u':1e-6,'n':1e-9,'p':1e-12,'f':1e-15,None:1}[match[2].lower() if match[2] else None]
    if not math.isfinite(number):
        raise ValueError(label + ' must be finite')
    return number


def _analysis(spec):
    kind, p = spec['type'], dict(spec['parameters'])
    defaults = {'ac':{'start':1,'stop':1e6,'points':40}, 'tran':{'step':2e-6,'stop':.005},
                'dc':{'source':'VIN','start':0,'stop':3.3,'step':.01},
                'noise':{'source':'VIN','output':'out','start':1,'stop':1e6,'points':40}, 'op':{}}
    if set(p) - set(defaults[kind]):
        raise ValueError('Analysis parameters contain fields that do not apply to ' + kind)
    p = {**defaults[kind], **p}
    for key in set(p) - {'source','output'}:
        p[key] = _spice_number(p[key], key)
    if kind in ('ac','noise'):
        if not 1 <= p['points'] <= 1000 or not p['points'].is_integer() or not 0 < p['start'] < p['stop']:
            raise ValueError('AC/noise requires positive ascending frequencies and 1–1000 points per decade')
        if math.log10(p['stop']/p['start']) * p['points'] > 200000:
            raise ValueError('Analysis exceeds 200,000 requested samples')
        p['points'] = int(p['points'])
    if kind == 'tran' and (not 0 < p['step'] <= p['stop'] or p['stop']/p['step'] > 200000):
        raise ValueError('Transient analysis requires positive step/stop and at most 200,000 requested samples')
    if kind == 'dc' and (p['step'] == 0 or (p['stop']-p['start'])/p['step'] <= 0
                          or abs((p['stop']-p['start'])/p['step']) > 200000):
        raise ValueError('DC sweep direction and step must define 1–200,000 samples')
    return {'type':kind,'parameters':p}


def _regular_bytes(path, limit=16*1024*1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Model library must be a bounded regular file')
        data = stream.read(limit+1)
    if len(data) > limit:
        raise ValueError('Model library exceeded the size limit')
    return data


def _library(workspace, pid, reference):
    if not isinstance(reference,str) or any(c in reference for c in ('\n','\r','\0','"',"'",'`','$')):
        raise ValueError('Model library path contains unsupported characters')
    path = Path(reference)
    if path.is_absolute():
        configured = workspace.config(pid).get('pdk_root') or os.environ.get('PDK_ROOT')
        if not configured or not path.resolve().is_relative_to(Path(configured).expanduser().resolve()):
            raise ValueError('External model libraries must be inside the configured PDK root')
        target = path.resolve()
    else:
        target = confined(workspace.projects.path(pid), reference)
    try:
        data = _regular_bytes(target)
        text = data.decode()
    except (OSError,UnicodeError) as error:
        raise ValueError('Model library is missing or unreadable') from error
    sections = re.findall(r'^\s*\.lib\s+([A-Za-z][A-Za-z0-9_]*)\s*(?:\$[^\n]*)?$', text,re.I|re.M)
    if not sections or len({s.lower() for s in sections}) != len(sections):
        raise ValueError('Model library must declare unique .lib sections')
    return {'path':reference,'resolved_path':str(target),'sha256':hashlib.sha256(data).hexdigest(),
            'sections':sections,'text':text}


def capabilities(workspace, pid):
    detected = detect(workspace.config(pid))
    libraries = []
    model = detected['pdk']['paths'].get('models')
    candidates = [model, str(Path(model).parent.parent / 'combined/sky130.lib.spice') if model else None]
    for reference in candidates:
        if not reference or not Path(reference).is_file(): continue
        try:
            library = _library(workspace,pid,reference)
            libraries.append({k:v for k,v in library.items() if k != 'text'})
        except ValueError: continue
    qualifications = []
    for task_id,task in list(workspace.operations.tasks.items()):
        if task_id.startswith(f'job:{pid}:') and task.get('operation_id') == 'simulation.statistics.qualify':
            job = workspace.jobs.get(workspace.projects.path(pid),task_id.split(':')[2])
            record = job.get('qualification')
            if record:
                live = (pid,job['id']) in _LIVE_QUALIFICATIONS.get(workspace.operations,{})
                qualifications.append({'job_id':job['id'],'status':job['status'],'kind':record['kind'],
                    'qualified':record['qualified'] and live,'section':record['section'],'checks':record['checks'],
                    'library':record['library']['path'],'scope':record['scope'],
                    'model_semantics':record.get('model_semantics','installed'),
                    'experimental':record.get('model_semantics','installed') != 'installed'})
    result = {'simulator':detected['tools']['ngspice'],'libraries':libraries,'max_runs':MAX_RUNS,'qualifications':qualifications,
            'model_semantics':[{'id':'installed','name':'Installed model semantics','experimental':False},
                               {'id':'cache_global_fet_draws','name':'Experimental: cache global FET draws',
                                'experimental':True,'kinds':['global_process'],'sections':['mc']}],
            'engineering_parameter':{'available':True,'reason':'Seeded bounded variation of explicit .param values; not foundry Monte Carlo'},
            'global_process':{'available':False,'reason':'Run a model-specific statistical qualification before selecting process Monte Carlo'},
            'device_mismatch':{'available':False,'reason':'Run a model-specific statistical qualification before selecting device mismatch'}}
    for kind in ('global_process','device_mismatch'):
        records = [record for record in qualifications if record['kind'] == kind and record['qualified']]
        if records:
            result[kind] = {'available':True,'qualification_jobs':[record['job_id'] for record in records],
                            'reason':'Live qualification exists; each batch revalidates its exact simulator, model dependencies and covered families'}
    return result


def _measurement_commands(measurements, analysis):
    commands, definitions, names = [], {}, set()
    kind, p = analysis['type'], analysis['parameters']
    for index, measure in enumerate(measurements):
        name, metric = measure['name'], measure['kind']
        if name.lower() in names or name.lower().startswith('af_'):
            raise ValueError('Measurement names must be unique and cannot use the af_ prefix')
        names.add(name.lower())
        name = name.lower()
        prefix = f'af_measure_{index}'
        emitted = []
        if metric == 'phase_margin':
            if kind != 'ac' or not all(key in measure for key in ('output','input')):
                raise ValueError('Phase margin requires AC analysis and explicit loop input/output nodes')
            if measure.get('loop_gain_convention') != 'negative_feedback_L':
                raise ValueError('Phase margin requires explicit loop_gain_convention=negative_feedback_L')
            units = 'degrees'
            voltage = lambda node: 'v(' + measure[node] + (',' + measure[node+'_negative'] if measure.get(node+'_negative') else '') + ')'
            emitted = [f'let {prefix}_ratio = {voltage("output")}/{voltage("input")}',
                f'let {prefix}_gain = db({prefix}_ratio)',
                f'let {prefix}_phase = 180/PI*cph({prefix}_ratio)',
                f'if {prefix}_gain[0] <= 0',
                'echo AF_ERROR: declared loop gain must start above unity', 'else',
                f'if abs({prefix}_phase[0]) >= 90',
                'echo AF_ERROR: declared negative-feedback L must start with phase between -90 and 90 degrees', 'else',
                f'meas ac {prefix}_unity_hz WHEN {prefix}_gain=0 FALL=1',
                f'meas ac {prefix}_unity_phase FIND {prefix}_phase AT=$&{prefix}_unity_hz',
                f'let {name} = 180 + {prefix}_unity_phase', f'print {name}', 'end', 'end']
        elif metric in ('gain','phase','bandwidth'):
            if kind != 'ac' or not all(key in measure for key in ('output','input')):
                raise ValueError(metric + ' requires AC analysis and explicit input/output nodes')
            voltage = lambda node: 'v(' + measure[node] + (',' + measure[node+'_negative'] if measure.get(node+'_negative') else '') + ')'
            emitted.append(f'let {prefix}_ratio = {voltage("output")}/{voltage("input")}')
            if metric == 'phase':
                expression = f'180/PI*cph({prefix}_ratio)'; units = 'degrees'
            else:
                expression = f'db({prefix}_ratio)'; units = 'dB' if metric == 'gain' else 'Hz'
            emitted.append(f'let {prefix}_trace = {expression}')
            frequency = measure.get('reference_hz',1) if metric == 'bandwidth' else measure.get('frequency_hz',1000)
            if not p['start'] <= frequency <= p['stop']:
                raise ValueError('Measurement/reference frequency is outside the AC analysis range')
            if metric == 'bandwidth':
                emitted += [f'meas ac {prefix}_reference FIND {prefix}_trace AT={frequency:.12g}',
                            f'let {prefix}_threshold = {prefix}_reference - {measure.get("drop_db",3):.12g}',
                            f'meas ac {name} WHEN {prefix}_trace=$&{prefix}_threshold FALL=1']
            else:
                emitted.append(f'meas ac {name} FIND {prefix}_trace AT={frequency:.12g}')
        elif metric == 'power':
            if kind not in ('tran','op') or not all(key in measure for key in ('supply_node','supply_source')):
                raise ValueError('Power requires transient/operating-point analysis and supply node/source names')
            units = 'W'
            expression = f'-v({measure["supply_node"]})*i({measure["supply_source"]})'
            if kind == 'op':
                emitted = [f'let {name} = {expression}',f'print {name}']
            else:
                emitted.append(f'let {prefix}_trace = {expression}')
        elif metric == 'swing':
            if kind != 'tran' or 'output' not in measure:
                raise ValueError('Swing requires transient analysis and an output node')
            units = 'Vpp'
            emitted.append(f'let {prefix}_trace = v({measure["output"]})')
        if kind == 'tran' and metric in ('power','swing'):
            start, stop = measure.get('start_s',0), measure.get('stop_s',p['stop'])
            if not 0 <= start < stop <= p['stop']:
                raise ValueError('Measurement interval must lie within the transient analysis')
            emitted.append(f'meas tran {name} {"AVG" if metric == "power" else "PP"} {prefix}_trace FROM={start:.12g} TO={stop:.12g}')
        commands.extend(emitted)
        definitions[name] = {**measure,'name':name,'units':units,'analysis':kind,'directives':emitted,
                             'phase_definition':'continuous output/input phase; not phase margin' if metric == 'phase' else ''}
        if metric == 'phase_margin':
            definitions[name].update(definition='180 + continuous phase(L) in degrees at the first falling unity-gain crossing; L is declared in 1+L(s)=0',
                unity_frequency_metric=prefix+'_unity_hz', unity_phase_metric=prefix+'_unity_phase',
                prerequisite='Explicit loop injection/return measurement with preserved loading; initial gain >1 and phase between -90 and 90 degrees. A closed-loop voltage response is not loop gain.')
    return commands, definitions


def _samples(statistics, reserved):
    kind, samples = statistics['kind'], statistics['samples']
    if kind in ('global_process','device_mismatch'):
        if statistics['variables']:
            raise ValueError('Foundry statistics cannot be mixed with engineering parameter distributions')
        return [{} for _ in range(samples)]
    if kind == 'none':
        if samples != 1 or statistics['variables']:
            raise ValueError('Choose engineering parameter variation before specifying statistical samples')
        return [{}]
    variables = statistics['variables']
    if not variables:
        raise ValueError('Engineering parameter variation requires explicit parameter distributions')
    names = set(reserved)
    for variable in variables:
        name = variable['parameter'].lower()
        if name in names: raise ValueError('A parameter cannot occur in multiple axes or statistical distributions')
        names.add(name)
        if not variable['minimum'] < variable['maximum']:
            raise ValueError('Statistical parameter bounds must ascend')
        if variable['distribution'] == 'normal' and not all(k in variable for k in ('mean','sigma')):
            raise ValueError('A bounded normal distribution requires mean and sigma')
    rng, result = random.Random(statistics['seed']), []
    for _ in range(samples):
        values = {}
        for variable in variables:
            low, high = variable['minimum'], variable['maximum']
            if variable['distribution'] == 'uniform':
                value = rng.uniform(low,high)
            else:
                for attempt in range(1000):
                    value = rng.gauss(variable['mean'],variable['sigma'])
                    if low <= value <= high: break
                else: raise ValueError('Bounded normal distribution could not produce a sample within 1000 attempts')
            values[variable['parameter']] = value
        result.append(values)
    return result


def _config_digest(workspace,pid):
    return hashlib.sha256(json.dumps(workspace.config(pid),sort_keys=True,allow_nan=False).encode()).hexdigest()


def prepare_batch(workspace,pid,spec):
    spec = validate(spec,BATCH_SCHEMA)
    spec['analysis'] = _analysis(spec['analysis'])
    source = invoke_engine(workspace,pid,'simulation-batch','file.read',{'path':spec['source']})['content']
    if re.search(r'^\s*\.control\b',source,re.I|re.M):
        raise ValueError('Batch source must not contain its own .control block')
    axes = list(spec['axes']) + ([spec['supply']] if spec.get('supply') else [])
    names = [axis['parameter'].lower() for axis in axes]
    if len(names) != len(set(names)):
        raise ValueError('Each swept parameter must have only one axis')
    qualification = None
    adapted = None
    if spec['statistics']['kind'] in ('global_process','device_mismatch'):
        qualification = _qualified(workspace,pid,spec,source)
        if spec['statistics']['model_semantics'] != 'installed':
            model = _library(workspace,pid,spec['corners']['library'])
            adapted = _adapted_models(workspace,pid,model,spec['statistics']['model_semantics'],
                                      spec['statistics']['kind'],spec['corners']['sections'][0])
            if adapted['identity'] != qualification['adapter']:
                raise ValueError('Adapted model implementation or sources changed since qualification')
    elif spec['statistics']['model_semantics'] != 'installed':
        raise ValueError('Experimental model semantics require explicitly qualified global process sampling')
    statistical = _samples(spec['statistics'],names)
    for name in [axis['parameter'] for axis in axes] + [v['parameter'] for v in spec['statistics']['variables']]:
        replace_parameter(source,name,1)  # Reject missing/ambiguous declarations before starting work.
    corners, library = [None], None
    if spec.get('corners'):
        library = _library(workspace,pid,spec['corners']['library'])
        corners = spec['corners']['sections']
        if any(corner not in library['sections'] for corner in corners):
            raise ValueError('Selected corner is not an actual .lib section in the model library')
        if len(re.findall(r'^\s*\.lib\s+\S+(?:[^\n]*)$',source,re.I|re.M)) != 1:
            raise ValueError('Corner selection requires exactly one top-level .lib declaration in the source')
    count = math.prod(len(axis['values']) for axis in axes) * len(spec['temperatures_c']) * len(corners) * len(statistical)
    if count > spec['limits']['max_runs']:
        raise ValueError('Scenario product exceeds the declared maximum run budget')
    commands, definitions = _measurement_commands(spec['measurements'],spec['analysis'])
    base = configure(source,spec['analysis']['type'],spec['analysis']['parameters'])
    base = re.sub(r'^\s*\.(?:temp|save)\b[^\n]*\n?', '',base,flags=re.I|re.M)
    scenarios, generated_bytes = [], 0
    combinations = itertools.product(*(axis['values'] for axis in axes)) if axes else [()]
    for values, temperature, corner, sample in itertools.product(list(combinations),spec['temperatures_c'],corners,range(len(statistical))):
        parameters = {axis['parameter']:value for axis,value in zip(axes,values)}
        parameters.update(statistical[sample])
        deck = base
        for name,value in parameters.items(): deck = replace_parameter(deck,name,value)
        if library:
            reference = adapted['library'] if adapted else library['path']
            deck = re.sub(r'^\s*\.lib\b[^\n]*$',lambda match:f'.lib "{reference}" {corner}',deck,flags=re.I|re.M)
        deck = re.sub(r'^\s*\.end\s*$',f'.temp {temperature:.12g}\n.save all\n.end',deck,flags=re.I|re.M)
        seed = spec['statistics']['seed'] + sample
        driver = ('* Analog-factory measured scenario\n.control\nset filetype=ascii\n'
                  f'setseed {seed}\nsource af_scripts/circuit.cir\nrun\n' + '\n'.join(commands)
                  + '\nwrite ../output/waveforms.raw all\nquit\n.endc\n.end\n')
        generated = {spec['source']:deck,'af_scripts/simulate.cir':driver}
        if adapted:
            if spec['source'] in adapted['generated_files']:
                raise ValueError('Statistical source conflicts with generated model inputs')
            generated.update(adapted['generated_files'])
        generated_bytes += sum(len(value.encode()) for value in generated.values())
        if generated_bytes > MAX_GENERATED_BYTES:
            raise ValueError('Generated scenario inputs exceed the 32 MiB batch limit')
        conditions = {'parameters':parameters,'temperature_c':temperature,'corner':corner,
                      'sample_index':sample,'seed':seed,'statistical_classification':spec['statistics']['kind'],
                      'measurement_definitions':definitions,
                      'model_library':{k:v for k,v in library.items() if k not in ('text',)} if library else None,
                      'statistical_qualification':qualification}
        if adapted:
            conditions['statistical_model_adapter'] = adapted['identity']
        scenarios.append({'index':len(scenarios),'conditions':conditions,'generated_files':generated})
    model_dependencies = qualification.pop('_dependencies') if qualification else {}
    if library and Path(library['path']).is_absolute():
        model_dependencies[library['resolved_path']] = library['sha256']
    return {'spec':spec,'scenarios':scenarios,'source_version':workspace.operations.version(pid),
            'config_digest':_config_digest(workspace,pid),'model_dependencies':model_dependencies}


def preview(workspace,pid,spec):
    plan = prepare_batch(workspace,pid,spec)
    return {'scenario_count':len(plan['scenarios']),'spec':plan['spec'],'source_version':plan['source_version'],
            'scenarios':[{'index':s['index'],'conditions':s['conditions']} for s in plan['scenarios']],
            'first_inputs':plan['scenarios'][0]['generated_files']}


def execute_batch(workspace,pid,plan,job,cancel,update):
    started = time.monotonic()
    deadline = started + plan['spec']['limits']['runtime_seconds']
    iterations, active_run = [], None
    status, termination, error = 'succeeded','completed',None
    def state_changed():
        if workspace.operations.version(pid) != plan['source_version'] or _config_digest(workspace,pid) != plan['config_digest']:
            return True
        try:
            return any(hashlib.sha256(_regular_bytes(Path(name))).hexdigest() != digest
                       for name,digest in plan.get('model_dependencies',{}).items())
        except (OSError,ValueError):
            return True
    for scenario in plan['scenarios']:
        if cancel.is_set(): status,termination = 'cancelled','cancelled'; break
        if time.monotonic() >= deadline: status,termination = 'timed_out','runtime_limit'; break
        if state_changed(): status,termination,error = 'failed','source_conflict','Project sources or configuration changed during batch'; break
        conditions = {**scenario['conditions'],'batch_job_id':job['id'],'scenario_index':scenario['index']}
        update(active_scenario=scenario['index'],active_run=None)
        try:
            # The shared registry lock binds source checking and snapshot creation.
            with workspace.projects.lock:
                if state_changed():
                    status,termination,error = 'failed','source_conflict','Project sources or configuration changed before snapshot'; break
                run = invoke_engine(workspace,pid,'simulation-batch','run.start',{
                    'kind':'simulate','options':{'netlist':plan['spec']['source']},
                    'timeout':min(plan['spec']['limits']['run_timeout'],max(.1,deadline-time.monotonic())),
                    'metadata':{'generated_files':scenario['generated_files'],'conditions':conditions,
                                'workflow_id':job['id'],'iteration':scenario['index']+1}})
            active_run = run['id']
            update(active_run=active_run)
            while True:
                expired = time.monotonic() >= deadline
                if cancel.is_set() or expired:
                    invoke_engine(workspace,pid,'simulation-batch','run.cancel',{'run_id':active_run})
                result = invoke_engine(workspace,pid,'simulation-batch','run.get',{'run_id':active_run})
                if result['status'] not in ('queued','running'): break
                time.sleep(.025)
            requested = conditions['measurement_definitions']
            metrics = {name:result.get('metrics',{}).get(name) for name in requested
                       if isinstance(result.get('metrics',{}).get(name),(int,float)) and math.isfinite(result['metrics'][name])}
            item = {'index':scenario['index'],'run_id':active_run,'status':result['status'],'metrics':metrics,
                    'conditions':conditions,'stale':result.get('stale',False),
                    'evidence':[{'operation_id':operation,'parameters':{'project_id':pid,'run_id':active_run}}
                                for operation in ('run.get','run.log','run.waveforms')]}
            iterations.append(item)
            update(iterations=iterations,completed_runs=len(iterations),active_run=None)
            active_run = None
            if cancel.is_set(): status,termination = 'cancelled','cancelled'; break
            if time.monotonic() >= deadline: status,termination = 'timed_out','runtime_limit'; break
            if state_changed():
                status,termination,error = 'failed','source_conflict','Project sources, configuration or qualified models changed during the scenario'; break
            if result['status'] != 'succeeded' or len(metrics) != len(requested) or result.get('stale'):
                status,termination,error = 'failed','measurement_failure','Scenario has missing/invalid measurements or failed/stale run evidence'; break
        except Exception as failure:
            if active_run:
                try:
                    invoke_engine(workspace,pid,'simulation-batch','run.cancel',{'run_id':active_run})
                except (ValueError,RuntimeError): pass
            status,termination,error = 'failed','execution_failure',str(failure)
            break
    return {'status':status,'termination':termination,'error':error,'iterations':iterations,
            'completed_runs':len(iterations),'scenario_count':len(plan['scenarios']),
            'duration_seconds':time.monotonic()-started,'active_run':None,'spec':plan['spec']}


def start_batch(workspace,pid,spec):
    plan = prepare_batch(workspace,pid,spec)
    root = workspace.projects.path(pid)
    with workspace.projects.lock:
        if any(job['status'] in ('queued','running') for job in workspace.jobs.list(root)):
            raise ValueError('Another orchestration job is active in this project')
        return workspace.jobs.start(root,'simulation-batch',
            lambda job,cancel,update:execute_batch(workspace,pid,plan,job,cancel,update),
            {'spec':plan['spec'],'scenario_count':len(plan['scenarios']),'source_version':plan['source_version'],
             'iterations':[],'completed_runs':0})


QUALIFICATION_SCHEMA = obj({
    'source':{'type':'string','minLength':1,'maxLength':4096,'default':'testbenches/pre.cir'},
    'library':{'type':'string','minLength':1,'maxLength':4096},
    'kind':{'type':'string','enum':['global_process','device_mismatch']},
    'section':NAME,
    'model_semantics':SEMANTICS,
    'seed':{'type':'integer','minimum':1,'maximum':2147483000,'default':41},
    'runtime_seconds':{'type':'number','minimum':1,'maximum':900,'default':900},
    'run_timeout':{'type':'number','minimum':.1,'maximum':180,'default':180},
}, ('library','kind','section'))
QUALIFIED_FAMILIES = ('sky130_fd_pr__nfet_01v8','sky130_fd_pr__pfet_01v8')


def _simulator_identity(workspace,pid):
    simulator = detect(workspace.config(pid))['tools']['ngspice']
    if not simulator['available']:
        raise ValueError('Statistical qualification requires an available ngspice simulator')
    executable = Path(simulator['path']).resolve()
    binaries = {}
    for _ in range(4):
        data = _regular_bytes(executable,128*1024*1024)
        binaries[str(executable)] = hashlib.sha256(data).hexdigest()
        if not data.startswith(b'#!'):
            break
        lines = [line.strip() for line in data.decode().splitlines() if line.strip().startswith('exec ')]
        if len(lines) != 1:
            raise ValueError('Qualification requires a binary or a launcher with one explicit absolute exec target')
        command = shlex.split(lines[0])
        if len(command) < 2 or not Path(command[1]).is_absolute() or any(c in command[1] for c in '$`{}'):
            raise ValueError('Qualification launcher target must be an explicit absolute executable path')
        executable = Path(command[1]).resolve()
    else:
        raise ValueError('Statistical simulator launcher chain is too deep')
    return {**simulator,'binaries':binaries}


def _qualification_library(workspace,pid,reference,kind,section):
    library = _library(workspace,pid,reference)
    if not Path(reference).is_absolute() or Path(reference).name != 'sky130.lib.spice':
        raise ValueError('Statistical qualification supports configured external SKY130 model libraries')
    expected = {'global_process':(0,1),'device_mismatch':(1,0)}[kind]
    block = re.search(r'^\s*\.lib\s+' + re.escape(section) + r'\s*$([\s\S]*?)^\s*\.endl\b',library['text'],re.I|re.M)
    if section not in library['sections'] or 'tt' not in library['sections'] or not block:
        raise ValueError('Qualification requires the selected statistical section and nominal tt section')
    switches = []
    for switch in ('MC_MM_SWITCH','MC_PR_SWITCH'):
        matches = re.findall(r'\b'+switch+r'\s*=\s*([01])\b',block[1],re.I)
        switches.append(int(matches[0]) if len(matches) == 1 else None)
    if tuple(switches) != expected:
        raise ValueError('Selected model section does not declare the requested mismatch/process switches')
    return library


def _adapted_models(workspace,pid,library,semantics,kind,section):
    if semantics == 'installed':
        return None
    if semantics != 'cache_global_fet_draws' or kind != 'global_process' or section != 'mc':
        raise ValueError('Experimental global-draw semantics require global_process and the mc section')
    configured = workspace.config(pid).get('pdk_root') or os.environ.get('PDK_ROOT')
    return prepare_global_models(library['resolved_path'],configured)


def _probe_deck(library,section):
    return f'''* SKY130 matched-device statistical compatibility probe
.lib "{library}" {section}
VDD vdd 0 1.8
VGN gn 0 .8
VDN1 dn1 0 .9
VDN2 dn2 0 .9
XN1 dn1 gn 0 0 sky130_fd_pr__nfet_01v8 w=1 l=.15
XN2 dn2 gn 0 0 sky130_fd_pr__nfet_01v8 w=1 l=.15
VGP gp 0 .9
VDP1 dp1 0 .9
VDP2 dp2 0 .9
XP1 dp1 gp vdd vdd sky130_fd_pr__pfet_01v8 w=2 l=.15
XP2 dp2 gp vdd vdd sky130_fd_pr__pfet_01v8 w=2 l=.15
.op
.temp 27
.save all
.end
'''


def _qualification_checks(iterations,kind):
    if len(iterations) != 5:
        return {'five_complete_probes':False}
    currents = [item['metrics'] for item in iterations]
    names = ('n1','n2','p1','p2')
    finite_positive = all(type(values.get(name)) in (int,float) and math.isfinite(values[name]) and values[name] > 1e-12
                          for values in currents for name in names)
    if not finite_positive: return {'finite_positive_currents':False}
    close = lambda a,b: math.isclose(a,b,rel_tol=2e-6,abs_tol=1e-12)
    return {'finite_positive_currents':True,
            'nominal_seed_invariant':all(close(currents[0][n],currents[1][n]) for n in names),
            'same_seed_repeatable':all(close(currents[2][n],currents[3][n]) for n in names),
            'both_families_seed_sensitive':all(not close(currents[2][n],currents[4][n]) for n in ('n1','p1')),
            'nominal_matched_pairs':all(close(currents[0][n+'1'],currents[0][n+'2']) for n in ('n','p')),
            'requested_correlation':all(close(values[n+'1'],values[n+'2']) == (kind == 'global_process')
                                        for values in currents[2:] for n in ('n','p'))}


def _dependency_identity(workspace,pid,iterations,adapted=None):
    configured = workspace.config(pid).get('pdk_root') or os.environ.get('PDK_ROOT')
    if not configured: raise ValueError('Qualification has no configured PDK root')
    allowed = Path(configured).expanduser().resolve()
    dependencies = dict(adapted['identity']['source_dependencies']) if adapted else {}
    for item in iterations:
        record = invoke_engine(workspace,pid,'statistical-qualification','run.get',{'run_id':item['run_id']})
        if record.get('imported_evidence') or record.get('stale') or record['status'] != 'succeeded':
            raise ValueError('Qualification contains imported, stale or failed evidence')
        if adapted:
            if record.get('external_dependencies'):
                raise ValueError('Adapted model snapshot retains unbundled external dependencies')
            for name,digest in adapted['identity']['generated_hashes'].items():
                if record.get('input_hashes',{}).get(name) != digest:
                    raise ValueError('Adapted qualification input hashes do not match the prepared model bundle')
        for name,digest in record.get('external_dependencies',{}).items():
            path = Path(name)
            if not path.is_absolute() or not path.resolve().is_relative_to(allowed) or not digest:
                raise ValueError('Qualification model dependency is outside the configured PDK root or unresolved')
            if name in dependencies and dependencies[name] != digest:
                raise ValueError('Model dependencies changed during statistical qualification')
            dependencies[name] = digest
    if not dependencies: raise ValueError('Qualification requires recorded external model dependencies')
    for name,digest in dependencies.items():
        if not Path(name).resolve().is_relative_to(allowed) or hashlib.sha256(_regular_bytes(Path(name))).hexdigest() != digest:
            raise ValueError('Model dependency changed during statistical qualification')
    return dependencies


def start_qualification(workspace,pid,parameters):
    parameters = validate(parameters,QUALIFICATION_SCHEMA)
    library = _qualification_library(workspace,pid,parameters['library'],parameters['kind'],parameters['section'])
    invoke_engine(workspace,pid,'statistical-qualification','file.read',{'path':parameters['source']})
    simulator = _simulator_identity(workspace,pid)
    adapted = _adapted_models(workspace,pid,library,parameters['model_semantics'],parameters['kind'],parameters['section'])
    seed = parameters['seed']
    sequence = [('tt',seed),('tt',seed+1),(parameters['section'],seed),(parameters['section'],seed),(parameters['section'],seed+1)]
    definitions = {name:{'units':'A','analysis':'op','definition':'Measured drain current of matched '+name[0]+'-channel device '+name[1]}
                   for name in ('n1','n2','p1','p2')}
    commands = ['let n1 = -i(VDN1)','let n2 = -i(VDN2)','let p1 = i(VDP1)','let p2 = i(VDP2)',
                'print n1','print n2','print p1','print p2']
    scenarios = []
    for index,(section,probe_seed) in enumerate(sequence):
        driver = ('* Statistical qualification driver\n.control\nset filetype=ascii\nset numdgt=15\n'
                  f'setseed {probe_seed}\nsource af_scripts/circuit.cir\nrun\n'+'\n'.join(commands)
                  +'\nwrite ../output/waveforms.raw all\nquit\n.endc\n.end\n')
        generated = {parameters['source']:_probe_deck(adapted['library'] if adapted else library['path'],section),'af_scripts/simulate.cir':driver}
        if adapted:
            if parameters['source'] in adapted['generated_files']:
                raise ValueError('Qualification source conflicts with generated model inputs')
            generated.update(adapted['generated_files'])
        conditions = {'corner':section,'seed':probe_seed,'temperature_c':27,
            'statistical_classification':'qualification_probe','measurement_definitions':definitions}
        conditions['model_semantics'] = parameters['model_semantics']
        if adapted: conditions['statistical_model_adapter'] = adapted['identity']
        scenarios.append({'index':index,'conditions':conditions,'generated_files':generated})
    if sum(len(text.encode()) for scenario in scenarios for text in scenario['generated_files'].values()) > MAX_GENERATED_BYTES:
        raise ValueError('Qualification model snapshots exceed the generated input byte limit')
    spec = {'source':parameters['source'],'limits':{'runtime_seconds':parameters['runtime_seconds'],
                                                  'run_timeout':parameters['run_timeout']}}
    plan = {'spec':spec,'scenarios':scenarios,'source_version':workspace.operations.version(pid),
            'config_digest':_config_digest(workspace,pid),'model_dependencies':{library['resolved_path']:library['sha256']}}
    if adapted: plan['model_dependencies'].update(adapted['identity']['source_dependencies'])
    def execute(job,cancel,update):
        result = execute_batch(workspace,pid,plan,job,cancel,update)
        checks = _qualification_checks(result['iterations'],parameters['kind'])
        checks['simulator_unchanged'] = _simulator_identity(workspace,pid) == simulator
        qualified = result['status'] == 'succeeded' and all(checks.values())
        dependencies = _dependency_identity(workspace,pid,result['iterations'],adapted) if qualified else {}
        result['qualification'] = {'qualified':qualified,'kind':parameters['kind'],'section':parameters['section'],
            'model_semantics':parameters['model_semantics'],'adapter':adapted['identity'] if adapted else None,
            'library':{k:v for k,v in library.items() if k != 'text'},'simulator':simulator,
            'families':list(QUALIFIED_FAMILIES),'checks':checks,'external_dependencies':dependencies,
            'scope':'Seed repeatability and matched-device correlation at 27 C, 1.8 V; not distribution calibration or foundry signoff'}
        if qualified:
            with workspace.projects.lock:
                _LIVE_QUALIFICATIONS.setdefault(workspace.operations,{})[(pid,job['id'])] = json.loads(json.dumps(result['qualification']))
        if not qualified and result['status'] == 'succeeded':
            result.update(status='failed',termination='qualification_failed',error='Statistical seed/correlation checks failed')
        return result
    root = workspace.projects.path(pid)
    with workspace.projects.lock:
        if any(job['status'] in ('queued','running') for job in workspace.jobs.list(root)):
            raise ValueError('Another orchestration job is active in this project')
        return workspace.jobs.start(root,'statistical-qualification',execute,
            {'parameters':parameters,'iterations':[],'completed_runs':0,'scenario_count':5,'source_version':plan['source_version']})


def _qualified_source_models(text):
    """Conservative supported SPICE subset, based on active instance statements."""
    logical=[]
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('*'):continue
        line=line.split('$',1)[0].strip()
        if line.startswith('+'):
            if not logical:raise ValueError('Statistical source contains an orphan continuation')
            logical[-1]+=' '+line[1:]
        else:logical.append(line)
    models=set()
    for line in logical:
        words=line.split()
        if not words:continue
        first=words[0].lower()
        if first.startswith('.func'):
            raise ValueError('Statistical sources cannot override qualified model functions')
        if first.startswith('.para') and re.search(r'\b(?:sw_[a-z0-9_]+|af_cached_[a-z0-9_]+|corner_factor|process_mc_factor|mismatch_factor)\s*=',line,re.I):
            raise ValueError('Statistical sources cannot override qualified process model parameters')
        if first in ('.model','.subckt'):
            raise ValueError('Statistical qualification does not cover local model or subcircuit definitions')
        if first[0] in 'mdqjzsw' and not first.startswith('.'):
            # Qualified devices are the exact foundry X subcircuits; primitive
            # semiconductor/switch models were not part of this qualification.
            raise ValueError('Statistical qualification does not cover primitive or local device models')
        if first.startswith('x'):
            arguments=[]
            for word in words[1:]:
                if '=' in word or word.lower()=='params:':break
                arguments.append(word)
            model=arguments[-1] if arguments else ''
            if model not in QUALIFIED_FAMILIES:
                raise ValueError('Statistical qualification covers only actual SKY130 nfet_01v8 and pfet_01v8 instances')
            models.add(model)
    return models


def _qualified(workspace,pid,spec,source):
    identifier = spec['statistics'].get('qualification_job')
    task = workspace.operations.tasks.get(f'job:{pid}:{identifier}',{})
    certificate = _LIVE_QUALIFICATIONS.get(workspace.operations,{}).get((pid,identifier))
    if not identifier or not certificate or task.get('operation_id') != 'simulation.statistics.qualify':
        raise ValueError('A successful live statistical qualification is required; imported or previous-server records cannot authorize Monte Carlo')
    job = workspace.jobs.get(workspace.projects.path(pid),identifier)
    evidence = job.get('qualification',{})
    if evidence != certificate:
        raise ValueError('Statistical qualification record does not match the live certificate')
    if job['status'] != 'succeeded' or not evidence.get('qualified') or evidence.get('kind') != spec['statistics']['kind']:
        raise ValueError('Statistical qualification is incomplete or belongs to a different variation kind')
    if evidence.get('model_semantics','installed') != spec['statistics']['model_semantics']:
        raise ValueError('Statistical model semantics must be explicitly selected and match qualification')
    corners = spec.get('corners') or {}
    library = _qualification_library(workspace,pid,corners.get('library',''),evidence['kind'],evidence['section'])
    if corners.get('sections') != [evidence['section']] or library['sha256'] != evidence['library']['sha256'] or library['resolved_path'] != evidence['library']['resolved_path']:
        raise ValueError('Statistical qualification must match the exact library and selected section')
    if _simulator_identity(workspace,pid) != evidence['simulator']:
        raise ValueError('Simulator changed since statistical qualification')
    configured = Path(workspace.config(pid).get('pdk_root') or os.environ.get('PDK_ROOT','')).expanduser().resolve()
    for name,digest in evidence['external_dependencies'].items():
        path = Path(name)
        if not path.resolve().is_relative_to(configured) or hashlib.sha256(_regular_bytes(path)).hexdigest() != digest:
            raise ValueError('Model dependency changed since statistical qualification')
    # Follow only visible project .include files; additional libraries and
    # overrides would escape the exact model/section that was qualified.
    root = workspace.projects.path(pid)
    pending,seen,models,total = [(spec['source'],source)],set(),set(),0
    while pending:
        name,text = pending.pop()
        if name in seen: continue
        seen.add(name); total += len(text.encode())
        if len(seen)>64 or total>1024*1024: raise ValueError('Statistical source closure exceeds its bounded validation limit')
        # The first source line is a SPICE title, unlike included files.
        active_text='\n'.join(text.splitlines()[1:]) if name==spec['source'] else text
        models.update(_qualified_source_models(active_text))
        if re.search(r'\bMC_(?:MM|PR)_SWITCH\b|^\s*\.control\b',text,re.I|re.M):
            raise ValueError('Statistical sources cannot override qualified model switches or control commands')
        for line in text.splitlines():
            if not re.match(r'^\s*\.(?:inc|include|lib)\b',line,re.I): continue
            words = shlex.split(line)
            if words[0].lower() == '.lib':
                if name != spec['source']: raise ValueError('Statistical source includes cannot select another model library')
                continue
            if len(words) != 2: raise ValueError('Statistical includes must use one literal project-relative path')
            dependency = confined(root,words[1])
            pending.append((words[1],_regular_bytes(dependency,1024*1024).decode()))
    if not models or models - set(QUALIFIED_FAMILIES):
        raise ValueError('Statistical qualification covers only SKY130 nfet_01v8 and pfet_01v8 model families')
    return {'job_id':identifier,'kind':evidence['kind'],'section':evidence['section'],
            'families':sorted(models),'scope':evidence['scope'],'model_semantics':evidence.get('model_semantics','installed'),
            'adapter':evidence.get('adapter'),'_dependencies':dict(evidence['external_dependencies'])}
