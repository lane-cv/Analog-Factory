"""Anonymous observations of fixed official CLI release sources; never upgrades."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import copy
import unicodedata
import json
import re
import threading
import time
import urllib.request

from . import toolchain
from .operations import OperationError, InvocationContext
from .storage import atomic_json, confined
from .provider_transport import _interruptible

MAX_RESPONSE=2*1024*1024
TIMEOUT=8
SEMVER=r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
SOURCES={'codex':('openai/codex','rust-v','https://developers.openai.com/codex/cli/'),
         'claude':('anthropics/claude-code','v','https://code.claude.com/docs/en/overview')}


def timestamp():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def epoch(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value):raise ValueError('Invalid release timestamp')
    return datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()

def installed_version(backend,value):
    if not isinstance(value,str):return None
    pattern=(r'(?:codex-cli\s+)?('+SEMVER+r')' if backend=='codex' else r'('+SEMVER+r')(?:\s+\(Claude Code\))?')
    match=re.fullmatch(pattern,value.strip())
    return match.group(1) if match else None

def parse_release(backend,value):
    repo,prefix,_=SOURCES[backend]
    if not isinstance(value,dict) or value.get('prerelease') is not False or value.get('draft') is not False:raise ValueError('Expected a published non-prerelease release')
    tag=value.get('tag_name');match=re.fullmatch(re.escape(prefix)+'('+SEMVER+')',tag) if isinstance(tag,str) else None
    if not match:raise ValueError('Invalid canonical release tag')
    url='https://github.com/'+repo+'/releases/tag/'+tag
    if value.get('html_url')!=url:raise ValueError('Release URL does not match its fixed official repository and tag')
    epoch(value.get('published_at'))
    notes=value.get('body')
    if notes is None:notes=''
    if not isinstance(notes,str):raise ValueError('Invalid release notes')
    notes=notes.replace('\r\n','\n').replace('\r','\n')
    notes=''.join(c for c in notes if c in ('\n','\t') or unicodedata.category(c)!='Cc')
    return {'version':match.group(1),'tag':tag,'url':url,'published_at':value['published_at'],
            'checked_at':timestamp(),'notes':notes[:8000],'notes_truncated':len(notes)>8000}

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class CliUpdates:
    def __init__(self,workspace):
        self.workspace=workspace;self.lock=threading.RLock();self.flight=threading.Lock()
        self.condition=threading.Condition();self.generation=0;self.closed=threading.Event();self.registry=None;self.thread=None
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='af-cli-release')
        self._cache={}
        path=confined(workspace.root,'.cli-updates.json',hidden=True)
        if path.exists():
            if path.stat().st_size>MAX_RESPONSE:raise ValueError('CLI release cache exceeds its bound')
            try:
                contents=json.loads(path.read_text())
                if not isinstance(contents,dict) or contents.get('schema_version')!=1:raise ValueError()
                rows=contents.get('backends',{})
                if not isinstance(rows,dict):raise ValueError()
                for backend,row in rows.items():
                    if backend not in SOURCES or not isinstance(row,dict):raise ValueError()
                    success=row.get('latest_success');attempt=row.get('latest_attempt')
                    if success is not None:
                        expected=parse_release(backend,{'tag_name':success['tag'],'html_url':success['url'],'published_at':success['published_at'],'prerelease':False,'draft':False,'body':''})
                        if success['version']!=expected['version'] or not isinstance(success['notes'],str) or len(success['notes'])>8000 or type(success['notes_truncated']) is not bool:raise ValueError()
                        epoch(success['checked_at'])
                    if attempt is not None:
                        epoch(attempt['checked_at'])
                        if attempt['status'] not in ('succeeded','failed') or attempt.get('error') not in (None,'Official release check failed'):raise ValueError()
                    self._cache[backend]={'latest_success':({key:success[key] for key in ('version','tag','url','published_at','checked_at','notes','notes_truncated')} if success is not None else None),
                                          'latest_attempt':({key:attempt[key] for key in ('checked_at','status','error')} if attempt is not None else None)}
            except (ValueError,KeyError,TypeError,RecursionError):
                self._cache={}

    def _configuration(self):
        value=self.workspace.settings().get('cli_updates',{})
        if not isinstance(value,dict):value={}
        interval=value.get('interval_hours',24)
        if type(interval) is not int or not 6<=interval<=168:interval=24
        return {'enabled':value.get('automatic',False) is True,'interval_hours':interval,'server_running_only':True}

    def releases(self):
        now=time.time();rows=[]
        with self.lock:
            for backend,(repo,_,docs) in SOURCES.items():
                cache=copy.deepcopy(self._cache.get(backend,{}));success=cache.get('latest_success');attempt=cache.get('latest_attempt')
                stale=not success or not attempt or attempt['status']!='succeeded' or not 0<=now-epoch(success['checked_at'])<86400
                rows.append({'backend':backend,'source_url':'https://api.github.com/repos/'+repo+'/releases/latest','documentation_url':docs,
                             'label':'latest published release','latest_success':success,'latest_attempt':attempt,'stale':bool(stale)})
        return {'backends':rows,'automatic':self._configuration()}

    def status(self,project_id=None):
        config=self.workspace.config(project_id);project=self.workspace.projects.get(project_id).get('config',{}) if project_id else {}
        release_rows=self.releases();tools=[]
        for backend in SOURCES:
            configured=config.get('tools',{}).get(backend);probe=toolchain._tool(backend,configured)
            version=installed_version(backend,probe.get('version')) if probe.get('available') else None
            row=next(r for r in release_rows['backends'] if r['backend']==backend);update='unknown'
            if version and not row['stale']:
                current=tuple(map(int,version.split('.')));latest=tuple(map(int,row['latest_success']['version'].split('.')))
                update='current' if current==latest else 'update_available' if current<latest else 'ahead'
            source='PATH' if not configured else 'project' if project.get('tools',{}).get(backend) else 'workspace'
            tools.append({'backend':backend,'available':bool(probe.get('available')),'path':probe.get('path'),
                          'configured_path':configured or None,'config_source':source,'version':version,'login':'not_checked',
                          'diagnostic':'Executable version detected' if version else 'Executable version unavailable; authentication was not checked', 'update_status':update})
        return {'project_id':project_id,'tools':tools,'releases':release_rows}

    def _fetch(self,backend):
        url='https://api.github.com/repos/'+SOURCES[backend][0]+'/releases/latest'
        request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'Analog-Factory-CLI-release-check','X-GitHub-Api-Version':'2022-11-28'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler(),_NoRedirect())
        deadline=time.monotonic()+TIMEOUT
        with opener.open(request,timeout=TIMEOUT) as response:
            if response.status!=200 or response.geturl()!=url:raise ValueError('Unexpected official response')
            with _interruptible(response,self.closed,deadline):
                data=response.read(MAX_RESPONSE+1)
            if time.monotonic()>deadline:raise TimeoutError()
            if len(data)>MAX_RESPONSE:raise ValueError('Official response exceeds its bound')
            value=json.loads(data.decode('utf-8'))
            parse_release(backend,value)
            return value

    def _persist(self):
        if self.closed.is_set():raise OperationError('UNAVAILABLE','CLI release checker is closed')
        path=confined(self.workspace.root,'.cli-updates.json',hidden=True)
        atomic_json(path,{'schema_version':1,'backends':self._cache})

    def check_updates(self,backends=None,force=False,on_change=None):
        backends=list(SOURCES) if backends is None else backends
        if not isinstance(backends,list) or not backends or len(backends)>2 or any(not isinstance(v,str) or v not in SOURCES for v in backends) or len(set(backends))!=len(backends) or type(force) is not bool:raise ValueError('Choose codex/claude and a boolean force preference')
        if self.closed.is_set():raise OperationError('UNAVAILABLE','CLI release checker is closed')
        deadline=time.monotonic()+TIMEOUT
        if not self.flight.acquire(timeout=TIMEOUT):raise OperationError('UNAVAILABLE','Official release check is already in progress')
        checked=[];hits=[];failures=[]
        try:
            pending={};now=time.time()
            with self.lock:
                if self.closed.is_set():raise OperationError('UNAVAILABLE','CLI release checker is closed')
                previous_attempts={b:copy.deepcopy(row.get('latest_attempt')) for b,row in self._cache.items()}
                for backend in backends:
                    cache=self._cache.get(backend,{});attempt=cache.get('latest_attempt');success=cache.get('latest_success')
                    cooldown=attempt and 0<=now-epoch(attempt['checked_at'])<60
                    fresh=success and attempt and attempt['status']=='succeeded' and 0<=now-epoch(success['checked_at'])<86400
                    if cooldown or (fresh and not force):
                        hits.append(backend)
                        if attempt and attempt['status']=='failed':failures.append(backend)
                    else:pending[backend]=self.executor.submit(self._fetch,backend)
            for backend,future in pending.items():
                checked.append(backend)
                try:
                    if self.closed.is_set():raise InterruptedError()
                    while True:
                        if self.closed.is_set():raise InterruptedError()
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise TimeoutError()
                        try:value=future.result(timeout=min(.05,remaining));break
                        except FutureTimeout:continue
                    success=parse_release(backend,value)
                    with self.lock:
                        if self.closed.is_set():raise OperationError('UNAVAILABLE','CLI release checker is closed')
                        self._cache[backend]={'latest_success':success,'latest_attempt':{'checked_at':timestamp(),'status':'succeeded','error':None}}
                except Exception:
                    future.cancel();failures.append(backend)
                    with self.lock:
                        if self.closed.is_set():raise OperationError('UNAVAILABLE','CLI release checker is closed')
                        previous=self._cache.get(backend,{})
                        self._cache[backend]={'latest_success':previous.get('latest_success'),'latest_attempt':{'checked_at':timestamp(),'status':'failed','error':'Official release check failed'}}
            with self.lock:
                self._persist()
                changed=previous_attempts!={b:row.get('latest_attempt') for b,row in self._cache.items()}
            if changed and on_change is not None:
                try:on_change()
                except OSError:pass  # Registry records journal failure; preserve the actual check result.
            if failures:raise OperationError('UNAVAILABLE','Official release check failed; retained observations may be stale')
            return {**self.releases(),'checked_backends':checked,'cache_hits':hits}
        finally:self.flight.release()

    def reconfigure(self):
        with self.condition:self.generation+=1;self.condition.notify_all()

    def attach_registry(self,registry):
        self.registry=registry
        if self.thread is None:
            self.thread=threading.Thread(target=self._schedule,name='af-cli-update-scheduler',daemon=True);self.thread.start()

    def _schedule(self):
        context=InvocationContext(principal='cli-update-scheduler',permissions=frozenset({'read'}),source='scheduler')
        while not self.closed.is_set():
            with self.condition:generation=self.generation
            config=self._configuration();delay=3600
            if config['enabled']:
                with self.lock:
                    attempts=[epoch((self._cache.get(b,{}).get('latest_attempt') or {}).get('checked_at','1970-01-01T00:00:00Z')) for b in SOURCES]
                delay=max(0,min(attempts)+config['interval_hours']*3600-time.time())
                if delay<=0:
                    self.registry.invoke('agent.cli.check_updates',{'backends':list(SOURCES),'force':True},context)
                    delay=60
            with self.condition:
                if not self.closed.is_set() and generation==self.generation:self.condition.wait(max(.1,min(delay,3600)))

    def close(self):
        with self.lock:self.closed.set()
        self.reconfigure();self.executor.shutdown(wait=False,cancel_futures=True)
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=1)
