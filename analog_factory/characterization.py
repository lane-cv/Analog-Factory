"""Bounded OTA characterization; observations derive from full actual raw samples."""
import hashlib
import math
import re
import shlex
import time
from pathlib import Path

from .operation_schema import validate
from .eda import input_dependencies
from .simulation import configure
from .simulation_batches import obj, array, _library, _config_digest, execute_batch, _simulator_identity, _regular_bytes
from .workflows import invoke_engine
from .waveforms import parse_raw
from .storage import confined

METRICS={'gain_db':('dB','Differential output/input magnitude at 1 Hz'),
         'bandwidth_hz':('Hz','First falling -3.0103 dB crossing relative to 1 Hz'),
         'power_w':('W','DC -V(vdd)*I(VDD)'),
         'output_dc_v':('V','DC output voltage'),
         'offset_v':('V','Differential input yielding output VDD/2 within +/-10 mV sweep'),
         'settling_s':('s','Observed open-loop 100 uV differential step: last 1% error crossing relative to final-window mean'),
         'noise_v_rms':('V RMS','Trapezoidal integral of output-noise density squared over 1 Hz..100 MHz')}
TARGETS={'gain_db_min':('gain_db','min'), 'bandwidth_hz_min':('bandwidth_hz','min'),
         'power_w_max':('power_w','max'), 'offset_v_abs_max':('offset_v','abs_max'),
         'settling_s_max':('settling_s','max'),'noise_v_rms_max':('noise_v_rms','max')}
CASE=obj({'corner':{'type':'string','enum':['tt','ff','ss','fs','sf'],'default':'tt'},
          'supply_v':{'type':'number','minimum':1.2,'maximum':1.95,'default':1.8},
          'temperature_c':{'type':'number','minimum':-40,'maximum':125,'default':27}})
SCHEMA=obj({'views':array({'type':'string','enum':['pre','post']},2,minItems=1,uniqueItems=True,default=['pre','post']),
            'cases':array(CASE,12,minItems=1,uniqueItems=True,default=[{}]),
            'analyses':array({'type':'string','enum':['ac','op','dc','tran','noise']},5,minItems=1,uniqueItems=True,default=['ac','op','dc','tran','noise']),
            'targets':obj({key:{'type':'number','minimum':0} for key in TARGETS},default={}),
            'limits':obj({'runtime_seconds':{'type':'number','minimum':1,'maximum':1800,'default':1800},
                          'run_timeout':{'type':'number','minimum':.1,'maximum':300,'default':180}},default={})})


def normalize(spec):
    result=validate(spec,SCHEMA)
    if any(case in result['cases'][:index] for index,case in enumerate(result['cases'])):
        raise ValueError('Normalized PVT cases must be unique')
    if len(result['cases'])*len(result['views'])*len(result['analyses'])>128:
        raise ValueError('Characterization exceeds 128 runs')
    return result


def assess(metrics,targets):
    checks={}
    for target,limit in targets.items():
        name,comparison=TARGETS[target];value=metrics.get(name)
        checks[target]={'value':value,'limit':limit,'status':'unknown' if value is None else
                        ('pass' if (value>=limit if comparison=='min' else abs(value)<=limit if comparison=='abs_max' else value<=limit) else 'fail')}
    statuses={item['status'] for item in checks.values()}
    return {'status':'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass' if statuses else 'observed','checks':checks}


def _columns(raw):
    if raw.get('display_sampling'):
        raise ValueError('Characterization requires full original samples, not reduced display waveforms')
    return {v['name'].lower():i for i,v in enumerate(raw['variables'])}


def measure(analysis,raw,case):
    cols=_columns(raw);rows=raw['points']
    def channel(name):
        if name not in cols:raise ValueError(f'Missing raw measurement channel: {name}')
        values=[row[cols[name]] for row in rows]
        if not values or any(not math.isfinite(v) for v in values):raise ValueError('Missing/nonfinite raw measurements')
        return values
    if analysis=='ac':
        frequencies=channel('frequency');complex_rows=raw.get('complex_points',[])
        if len(complex_rows)!=len(rows):raise ValueError('AC requires actual complex waveforms')
        gains=[]
        for row in complex_rows:
            differential=complex(*row[cols['v(inp)']])-complex(*row[cols['v(inn)']])
            gain=abs(complex(*row[cols['v(out)']])/differential)
            if not math.isfinite(gain) or gain<=0:raise ValueError('Invalid differential AC gain')
            gains.append(20*math.log10(gain))
        if not math.isclose(frequencies[0],1,rel_tol=1e-9):raise ValueError('AC must start at 1 Hz')
        target=gains[0]-10*math.log10(2)
        result={'gain_db':gains[0]}
        for index in range(1,len(gains)):
            if gains[index]<=target<gains[index-1]:
                ratio=(target-gains[index-1])/(gains[index]-gains[index-1])
                result['bandwidth_hz']=math.exp(math.log(frequencies[index-1])+ratio*math.log(frequencies[index]/frequencies[index-1]));break
        if 'bandwidth_hz' not in result:raise ValueError('AC lacks falling bandwidth crossing')
        return result
    if analysis=='op':
        current=channel('i(vdd)')[0];voltage=channel('v(vdd)')[0]
        return {'power_w':-voltage*current,'output_dc_v':channel('v(out)')[0]}
    if analysis=='dc':
        sweep=rows;outputs=channel('v(out)');target=case['supply_v']/2
        matches=[]
        for index in range(1,len(rows)):
            a,b=outputs[index-1]-target,outputs[index]-target
            if a==0:matches.append(rows[index-1][0])
            elif a*b<=0 and b!=a:matches.append(rows[index-1][0]+(-a/(b-a))*(rows[index][0]-rows[index-1][0]))
        matches=list(dict.fromkeys(matches))
        if len(matches)!=1:raise ValueError('Offset requires one unambiguous output midpoint crossing')
        return {'offset_v':matches[0]}
    if analysis=='noise':
        frequency=channel('frequency');density=channel('onoise_spectrum')
        if len(rows)<2 or any(b<=a for a,b in zip(frequency,frequency[1:])):raise ValueError('Noise requires ordered frequency spectrum')
        integral=sum((b-a)*(x*x+y*y)/2 for a,b,x,y in zip(frequency,frequency[1:],density,density[1:]))
        return {'noise_v_rms':math.sqrt(integral)}
    if analysis=='tran':
        times=channel('time');outputs=channel('v(out)');step=1e-6
        before=[v for t,v in zip(times,outputs) if t<step];tail=[v for t,v in zip(times,outputs) if t>=times[-1]*.9]
        if not before or len(tail)<2:raise ValueError('Insufficient settling observation window')
        final=sum(tail)/len(tail);amplitude=abs(final-before[-1]);tolerance=amplitude*.01
        if amplitude<1e-9 or max(tail)-min(tail)>tolerance:raise ValueError('No stable settling tail within observation window')
        outside=[i for i,(t,v) in enumerate(zip(times,outputs)) if t>=step and abs(v-final)>tolerance]
        index=outside[-1]+1 if outside else next(i for i,t in enumerate(times) if t>=step)
        if index>=len(times):raise ValueError('No settling within observation window')
        return {'settling_s':times[index]-step}
    raise ValueError('Unknown characterization analysis')


def _replace(source,name,line):
    expression=rf'^{name}\s+[^\n]*$'
    if len(re.findall(expression,source,re.I|re.M))!=1:raise ValueError(f'OTA reference requires one {name} stimulus')
    return re.sub(expression,lambda m:line,source,flags=re.I|re.M)



# Only the trusted generated driver may issue measurements/control commands.
# Included files are circuit/model cards, never alternate analysis programs.
_CIRCUIT_CARDS={'subckt','ends','model','param','func','global','include','inc'}
_TOP_CARDS=_CIRCUIT_CARDS|{'lib','ac','op','dc','tran','noise','meas','measure','temp','save','end'}
_MODEL_CARDS=_CIRCUIT_CARDS|{'lib','endl','option','options'}


def _logical_cards(text,title=False):
    lines=text.splitlines()[1:] if title else text.splitlines()
    logical=[]
    for line in lines:
        line=line.strip()
        if not line or line.startswith('*'):continue
        if line.startswith('+'):
            if not logical:raise ValueError('Orphan continuation in characterization source')
            logical[-1]+=' '+line[1:]
        else:logical.append(line)
    return logical


def _checked_cards(text,label,allowed,title=False,project=True):
    cards=_logical_cards(text,title)
    for line in cards:
        if project and re.search(r'\bMC_(?:MM|PR)_SWITCH\b',line,re.I):
            raise ValueError('Characterization project sources cannot override statistical model switches')
        if not line.startswith('.'):continue
        directive=line.split(None,1)[0][1:].lower()
        if directive not in allowed:
            raise ValueError(f'Characterization source {label} cannot contain control or analysis override directive .{directive}')
    return cards


def validate_source_closure(root,source_path,source,external_dependencies,pdk_root=None):
    """Reject executable overrides before trusting any simulator-written raw data.

    Project includes must have one literal confined path. Model-native cards are
    allowed only in the configured PDK closure, whose resolution must be known.
    This validates SPICE card ownership; it is not an operating-system sandbox.
    """
    root=Path(root).resolve();pending=[(source_path,source)];seen=set();total=0
    while pending:
        name,text=pending.pop()
        if name in seen:continue
        seen.add(name);total+=len(text.encode())
        if len(seen)>64 or total>1024*1024:raise ValueError('Characterization project include closure exceeds 64 files or 1 MiB')
        cards=_checked_cards(text,name,_TOP_CARDS if name==source_path else _CIRCUIT_CARDS,
                             title=name==source_path)
        for line in cards:
            if not re.match(r'^\.(?:inc|include)\b',line,re.I):continue
            try:words=shlex.split(line)
            except ValueError as error:raise ValueError('Unparseable characterization include') from error
            if len(words)!=2 or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./ -]{0,4095}',words[1]):
                raise ValueError('Characterization includes require one literal visible project-relative path')
            target=confined(root,words[1])
            # ngspice may resolve from the containing file or working directory.
            # Reject two real, distinct candidates instead of choosing one.
            alternate=(root/Path(name).parent/words[1]).resolve()
            if alternate!=target.resolve() and alternate.is_file():
                raise ValueError('Ambiguous characterization project include resolution')
            try:text=_regular_bytes(target,1024*1024).decode()
            except (OSError,UnicodeError) as error:raise ValueError('Missing or unreadable characterization project include') from error
            pending.append((target.relative_to(root).as_posix(),text))
    total=0
    if len(external_dependencies)>1024:raise ValueError('Characterization model closure exceeds 1024 files')
    model_paths={Path(name).resolve() for name in external_dependencies}
    for name,digest in external_dependencies.items():
        path=Path(name).resolve()
        if digest is None:raise ValueError('Missing external model dependency')
        if pdk_root is not None and not path.is_relative_to(Path(pdk_root).resolve()):
            raise ValueError('Characterization model dependency is outside the configured PDK root')
        try:data=_regular_bytes(path)
        except OSError as error:raise ValueError('Missing external model dependency') from error
        total+=len(data)
        if total>64*1024*1024:raise ValueError('Characterization model closure exceeds 64 MiB')
        cards=_checked_cards(data.decode(),name,_MODEL_CARDS,project=False)
        for line in cards:
            if not re.match(r'^\.(?:inc|include|lib)\b',line,re.I):continue
            try:words=shlex.split(line)
            except ValueError as error:raise ValueError('Unparseable characterization model include') from error
            if words[0].lower()=='.lib' and len(words)==2:continue
            expected=3 if words[0].lower()=='.lib' else 2
            if len(words)!=expected or any(c in words[1] for c in '$`{}\\'):
                raise ValueError('Characterization model include requires a literal resolved reference')
            reference=Path(words[1])
            candidates=[reference.resolve()] if reference.is_absolute() else list(dict.fromkeys([(root/reference).resolve(),(path.parent/reference).resolve()]))
            existing=[candidate for candidate in candidates if candidate.is_file()]
            if len(existing)!=1 or existing[0] not in model_paths:
                raise ValueError('Characterization model include escapes its validated model closure or has ambiguous/missing resolution')


def prepare(workspace,pid,spec):
    spec=normalize(spec);scenarios=[];dependencies={}
    config=workspace.config(pid)
    library=str(Path(config.get('pdk_root',''))/config.get('pdk_variant','sky130A')/'libs.tech/combined/sky130.lib.spice')
    model=_library(workspace,pid,library)
    if Path(model['resolved_path']).is_relative_to(workspace.projects.path(pid).resolve()):
        raise ValueError('Configured characterization models must remain outside project sources')
    dependencies[model['resolved_path']]=model['sha256']
    dependency_environment={'PDK_ROOT':config.get('pdk_root',''),'PDKPATH':str(Path(config.get('pdk_root',''))/config.get('pdk_variant','sky130A'))}
    # The generated deck selects the configured model, which can differ from the
    # library currently written in the saved source. Validate the effective
    # model closure independently; validating only the old source misses it.
    effective_model_closure=input_dependencies(workspace.projects.path(pid),model['resolved_path'],dependency_environment)
    unresolved=[reason for reason in effective_model_closure['provenance']['limitations'] if reason!='External model files are hashed but are not included in the immutable input snapshot']
    if unresolved:raise ValueError('Characterization configured model requires a closed include closure: '+unresolved[0])
    dependencies.update(effective_model_closure['external_dependencies'])
    simulator=_simulator_identity(workspace,pid)
    for name,digest in simulator['binaries'].items():
        if Path(name).stat().st_size>16*1024*1024:raise ValueError('Tracked simulator binary exceeds the 16 MiB dependency bound')
        dependencies[name]=digest
    for view in spec['views']:
        source_path=f'testbenches/{view}.cir'
        original=invoke_engine(workspace,pid,'characterization','file.read',{'path':source_path})['content']
        closure=input_dependencies(workspace.projects.path(pid),source_path,dependency_environment)
        unresolved=[reason for reason in closure['provenance']['limitations'] if reason!='External model files are hashed but are not included in the immutable input snapshot']
        if unresolved:raise ValueError('Characterization requires a closed, unambiguous source/model include closure: '+unresolved[0])
        validate_source_closure(workspace.projects.path(pid),source_path,original,{**closure['external_dependencies'],**effective_model_closure['external_dependencies']},config.get('pdk_root'))
        for name,digest in closure['external_dependencies'].items():
            if digest is None:raise ValueError('Missing external model dependency')
            dependencies[name]=digest
        netlist_path='netlists/schematic.spice' if view=='pre' else 'netlists/extracted.spice'
        netlist=invoke_engine(workspace,pid,'characterization','file.read',{'path':netlist_path})['content']
        if not re.search(r'^\s*\.include\s+["\']?'+re.escape(netlist_path)+r'["\']?\s*$',original,re.I|re.M):raise ValueError('Reference testbench must include its expected netlist')
        if view=='post' and not re.search(r'^C\S+\s',netlist,re.M|re.I):raise ValueError('Post-layout characterization requires actual PEX capacitors')
        if re.search(r'^\s*\.control\b',original,re.I|re.M):raise ValueError('Reference source must not have its own .control')
        for case in spec['cases']:
            if case['corner'] not in model['sections']:raise ValueError('Requested PVT section is absent from configured model library')
            for analysis in spec['analyses']:
                deck=configure(original,'op',{})
                if len(re.findall(r'^\s*\.lib\b',deck,re.I|re.M))!=1:raise ValueError('Reference requires one top-level library')
                deck=re.sub(r'^\s*\.lib\b[^\n]*',lambda m:f'.lib "{model["path"]}" {case["corner"]}',deck,flags=re.I|re.M)
                deck=re.sub(r'^\s*\.temp\b[^\n]*','',deck,flags=re.I|re.M)
                deck=_replace(deck,'VDD',f'VDD vdd 0 {case["supply_v"]:.12g}')
                # Preserve the reference's 0.9 V common-mode, 0.65 V bias and 1 pF load.
                if analysis=='dc':
                    deck=_replace(deck,'VP','VP inp inn DC 0 AC 1')
                    deck=_replace(deck,'VN','VN inn 0 DC 0.9')
                elif analysis=='tran':
                    deck=_replace(deck,'VP','VP inp 0 PWL(0 0.9 1u 0.9 1.001u 0.90005 50u 0.90005)')
                    deck=_replace(deck,'VN','VN inn 0 PWL(0 0.9 1u 0.9 1.001u 0.89995 50u 0.89995)')
                options={'ac':{'start':1,'stop':'100Meg','points':60},'op':{},
                         'dc':{'source':'VP','start':-.01,'stop':.01,'step':.00005},
                         'tran':{'step':'20n','stop':'50u'},
                         'noise':{'output':'out','source':'VP','start':1,'stop':'100Meg','points':60}}[analysis]
                deck=configure(deck,analysis,options,f'.temp {case["temperature_c"]:.12g}\n.save all')
                driver='* OTA characterization actual ngspice\n.control\nset filetype=ascii\nsource af_scripts/circuit.cir\nrun\n'
                if analysis=='noise':driver+='setplot noise1\n'
                driver+='write ../output/waveforms.raw all\nquit\n.endc\n.end\n'
                scenarios.append({'index':len(scenarios),'source':source_path,
                                  'conditions':{**case,'view':view,'analysis':analysis,'simulator_identity':simulator,'measurement_definitions':{},'characterization_definitions':{name:{'units':METRICS[name][0],'definition':METRICS[name][1]} for name in {'ac':['gain_db','bandwidth_hz'],'op':['power_w','output_dc_v'],'dc':['offset_v'],'tran':['settling_s'],'noise':['noise_v_rms']}[analysis]},'classification':'deterministic_PVT_not_Monte_Carlo'},
                                  'generated_files':{source_path:deck,'af_scripts/simulate.cir':driver}})
    return {'spec':spec,'scenarios':scenarios,'source_version':workspace.operations.version(pid),
            'config_digest':_config_digest(workspace,pid),'model_dependencies':dependencies,'simulator_identity':simulator}


def execute(workspace,pid,plan,job,cancel,update):
    if cancel.is_set():return {'status':'cancelled','termination':'cancelled','iterations':[]}
    # Execute the same supervised immutable runs as measurement batches, preserving
    # per-scenario source selection. Grouping is unnecessary: one batch engine run
    # per scenario shares a global deadline and stops on stale inputs/cancellation.
    started=time.monotonic();iterations=[];failure=None;error=None;termination='completed'
    for scenario in plan['scenarios']:
        remaining=plan['spec']['limits']['runtime_seconds']-(time.monotonic()-started)
        if remaining<=0:failure='timed_out';termination='runtime_limit';break
        sub={**plan,'scenarios':[scenario],'spec':{**plan['spec'],'source':scenario['source'],
                 'limits':{**plan['spec']['limits'],'runtime_seconds':remaining}}}
        result=execute_batch(workspace,pid,sub,job,cancel,lambda **v:update(active_run=v.get('active_run'),completed_runs=len(iterations)))
        if result['iterations']:
            item=result['iterations'][0];item['metrics']={};item['measurements']={};item['measurement_error']=None
            if item['status']=='succeeded' and not item['stale']:
                try:
                    path=workspace.artifact(pid,item['run_id'],'waveforms.raw')
                    record=invoke_engine(workspace,pid,'characterization','run.get',{'run_id':item['run_id']})
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=record['artifact_hashes']['waveforms.raw']:raise ValueError('Raw waveform artifact changed after execution')
                    metrics=measure(scenario['conditions']['analysis'],parse_raw(path),scenario['conditions'])
                    item['metrics']=metrics
                    item['measurements']={name:{'value':value,'units':METRICS[name][0],'definition':METRICS[name][1]} for name,value in metrics.items()}
                except (ValueError,KeyError,OSError,ZeroDivisionError) as exc:
                    item['measurement_error']=str(exc)
            iterations.append(item);update(iterations=iterations,completed_runs=len(iterations))
        if result['status']!='succeeded':failure=result['status'];error=result.get('error');termination=result['termination'];break
        if workspace.operations.version(pid)!=plan['source_version'] or _config_digest(workspace,pid)!=plan['config_digest']:
            failure='failed';error='Project sources or configuration changed during measurement';termination='source_conflict';break
    groups=[]
    for view in plan['spec']['views']:
        for case in plan['spec']['cases']:
            relevant=[i for i in iterations if i['conditions']['view']==view and all(i['conditions'][k]==v for k,v in case.items())]
            metrics={k:v for item in relevant for k,v in item['metrics'].items()}
            groups.append({'view':view,'conditions':case,'metrics':metrics,'assessment':assess(metrics,plan['spec']['targets']),
                           'run_ids':[i['run_id'] for i in relevant]})
    measurement_failures=any(i['measurement_error'] for i in iterations)
    return {'status':failure or ('failed' if measurement_failures else 'succeeded'),
            'termination':termination if failure else 'measurement_failure' if measurement_failures else 'completed',
            'error':error or ('One or more analyses lack valid measurements; inspect iteration errors and actual run logs' if measurement_failures else None),
            'iterations':iterations,'completed_runs':len(iterations),'scenario_count':len(plan['scenarios']),
            'groups':groups,'simulator_identity':plan.get('simulator_identity'),'spec':plan['spec'],'duration_seconds':time.monotonic()-started,'active_run':None,
            'electrical_status':'fail' if any(g['assessment']['status']=='fail' for g in groups) else
                                'unknown' if failure or measurement_failures or any(g['assessment']['status']=='unknown' for g in groups) else
                                'pass' if plan['spec']['targets'] else 'observed'}


def start(workspace,pid,spec):
    plan=prepare(workspace,pid,spec);root=workspace.projects.path(pid)
    with workspace.projects.lock:
        if any(job['status'] in ('queued','running') for job in workspace.jobs.list(root)):raise ValueError('Another orchestration job is active in this project')
        return workspace.jobs.start(root,'ota-characterization',lambda job,cancel,update:execute(workspace,pid,plan,job,cancel,update),
                                    {'spec':plan['spec'],'source_version':plan['source_version'],'scenario_count':len(plan['scenarios']),'simulator_identity':plan['simulator_identity'],'iterations':[]})
