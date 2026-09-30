"""Shared workspace use cases for manual UI, CLI, and workflow execution."""
import copy
import json
import os
import re
import threading
import uuid
from pathlib import Path

from . import __version__, eda, toolchain, waveforms
from .credentials import CredentialStore
from .agents import AgentManager
from .execution import RunManager
from .jobs import Jobs
from .projects import ProjectStore
from .providers import Provider, provider_binding, public_config
from .storage import atomic_json, confined, read_json


DEFAULT_UI = {'language': 'en', 'theme': 'dark'}
PROVIDER_CONTROLS = {'api_key', 'api_key_storage', 'api_key_configured', 'clear_api_key'}


def persisted_config(config):
    result = public_config(config)
    if 'provider' in result:
        result['provider'] = {key: value for key, value in result['provider'].items() if key not in PROVIDER_CONTROLS}
    return result


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.projects = ProjectStore(self.root)
        self.runner = RunManager()
        self.jobs = Jobs()
        self.lock = threading.RLock()
        self._settings = persisted_config(read_json(self.root / 'settings.json', {}))
        self._secret_binding = self._provider_binding(self._settings.get('provider', {}))
        self.credentials = CredentialStore(self.root)
        self._secret = self.credentials.load(self._secret_binding)
        self._secret_storage = 'local' if self._secret else 'none'
        self.agents = AgentManager(self)

    def settings(self):
        with self.lock:
            settings = public_config(copy.deepcopy(self._settings))
            settings['ui'] = {**DEFAULT_UI, **settings.get('ui', {})}
            provider = settings.setdefault('provider', {})
            storage = self._secret_storage if self._secret else 'none'
            if storage == 'none' and os.environ.get(provider.get('api_key_env', '')):
                storage = 'environment'
            provider['api_key_configured'] = storage != 'none'
            provider['api_key_storage'] = storage
            return settings

    def save_settings(self, settings):
        if not isinstance(settings, dict):
            raise ValueError('Settings must be an object')
        allowed = {'tools', 'pdk_root', 'pdk_variant', 'pdk_version', 'provider', 'ui'}
        with self.lock:
            updated = {**self._settings, **{k: v for k, v in settings.items() if k in allowed}}
            provider_patch = settings.get('provider', {})
            if not isinstance(provider_patch, dict):
                raise ValueError('Provider configuration must be an object')
            if 'provider' in settings:
                updated['provider'] = {**self._settings.get('provider', {}), **provider_patch}
            binding = self._provider_binding(updated.get('provider', {}))
            if binding != self._secret_binding and 'api_key_env' not in provider_patch:
                updated.get('provider', {}).pop('api_key_env', None)
            self.validate_config(updated)
            updated['ui'] = {**DEFAULT_UI, **self._settings.get('ui', {}), **settings.get('ui', {})}
            persisted = persisted_config(updated)
            json.dumps(persisted, allow_nan=False)
            secret, storage = self._secret, self._secret_storage
            if binding != self._secret_binding:
                secret, storage = None, 'none'
            if provider_patch.get('clear_api_key') or provider_patch.get('api_key') == '':
                secret, storage = None, 'none'
            elif 'api_key' in provider_patch:
                secret = provider_patch['api_key']
                storage = provider_patch.get('api_key_storage', 'session')
            elif secret and 'api_key_storage' in provider_patch:
                storage = provider_patch['api_key_storage']
            with self.credentials.transaction():
                if secret and storage == 'local':
                    self.credentials.save(binding, secret)
                else:
                    self.credentials.clear()
                atomic_json(self.root / 'settings.json', persisted)
            self._secret, self._secret_storage = secret, storage
            self._secret_binding = binding
            self._settings = persisted
        return self.settings()

    @staticmethod
    def validate_config(config):
        if not isinstance(config, dict):
            raise ValueError('Configuration must be an object')
        paths = config.get('tools', {})
        if not isinstance(paths, dict) or any(not isinstance(v, str) or '\x00' in v for v in paths.values()):
            raise ValueError('Tool paths must map names to strings')
        if 'pdk_root' in config and not isinstance(config['pdk_root'], str):
            raise ValueError('PDK root must be a path string')
        variant = config.get('pdk_variant') or 'sky130A'
        if not isinstance(variant, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', variant):
            raise ValueError('Invalid PDK variant')
        if 'provider' in config:
            if not isinstance(config['provider'], dict):
                raise ValueError('Provider configuration must be an object')
            Provider(config['provider'])
        if 'ui' in config:
            ui = config['ui']
            if not isinstance(ui, dict) or set(ui) - {'language', 'theme'}:
                raise ValueError('UI settings must contain only language and theme')
            if 'language' in ui and ui['language'] not in ('en', 'zh-CN'):
                raise ValueError('UI language must be en or zh-CN')
            if 'theme' in ui and ui['theme'] not in ('dark', 'light'):
                raise ValueError('UI theme must be dark or light')

    def configure_project(self, project_id, config):
        self.validate_config(config)
        return self.projects.configure(project_id, persisted_config(config))

    def config(self, project_id=None):
        result = copy.deepcopy(self._settings)
        if project_id:
            overrides = self.projects.get(project_id).get('config', {})
            result.update({k: v for k, v in overrides.items() if k not in ('tools', 'provider')})
            for key in ('tools', 'provider'):
                result[key] = {**result.get(key, {}), **overrides.get(key, {})}
            if self._provider_binding(result.get('provider', {})) != self._provider_binding(self._settings.get('provider', {})):
                if 'api_key_env' not in overrides.get('provider', {}):
                    result['provider'].pop('api_key_env', None)
        return result

    @staticmethod
    def _provider_binding(config):
        return provider_binding(config)

    def provider(self, project_id=None):
        with self.lock:
            config = self.config(project_id).get('provider', {})
            secret = self._secret if self._provider_binding(config) == self._secret_binding else None
            return Provider(config, secret)

    def status(self):
        return {'version': __version__, 'toolchain': toolchain.detect(self.config()),
                'capabilities': {
                    'manual': {'available': True, 'status': 'implemented'},
                    'simulation': {'available': True, 'status': 'requires ngspice'},
                    'assistant': {'available': bool(self._settings.get('provider', {}).get('base_url')),
                                  'status': 'embedded API sessions; read-only or confirmed actions'},
                    'workflows': {'available': True, 'status': 'experimental: bounded scalar tuning chain'},
                    'evaluation': {'available': True, 'status': 'experimental: isolated random-search baseline'},
                    'training': {'available': False, 'status': 'planned', 'reason': 'No training framework/model configured or validated'},
                    'cli_agents': {'available': any(item['available'] for item in self.agents.backends()['backends'] if item['id'] != 'api'),
                                   'status': 'embedded sessions; CLI login required'},
                }}

    def detail(self, project_id):
        root = self.projects.path(project_id)
        return {'project': self.projects.get(project_id), 'files': self.projects.files(project_id),
                'runs': self.runs(project_id), 'jobs': self.jobs.list(root)}

    def _episode_roots(self, project_id):
        root = self.projects.path(project_id)
        episodes = confined(root, '.af/episodes', hidden=True)
        for path in episodes.glob('*/*'):
            if path.is_dir() and path.name.isdigit():
                yield confined(root, path.relative_to(root).as_posix(), hidden=True)

    def runs(self, project_id):
        roots = [self.projects.path(project_id), *self._episode_roots(project_id)]
        result = [record for root in roots for record in self.runner.list(root)]
        return sorted(result, key=lambda r: r['created_at'], reverse=True)

    def run_root(self, project_id, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise ValueError('Invalid run identifier')
        for root in [self.projects.path(project_id), *self._episode_roots(project_id)]:
            path = confined(root, f'.af/runs/{run_id}/manifest.json', hidden=True)
            if path.is_file():
                return root
        raise FileNotFoundError('Run does not exist in this project')

    def run(self, project_id, kind, options=None, timeout=None, metadata=None):
        root = self.projects.path(project_id)
        try:
            specification = eda.prepare(root, kind, options or {}, self.config(project_id))
        except RuntimeError as error:
            return self.runner.start(root, kind, [], metadata={'unavailable': str(error)})
        if timeout is not None:
            value = float(timeout)
            if not .1 <= value <= 3600:
                raise ValueError('Task timeout must be 0.1–3600 seconds')
            specification['timeout'] = value
        details = specification.setdefault('metadata', {})
        initial_source = details.pop('initial_editor_source', None)
        generated = {**details.get('generated_files', {}), **(metadata or {}).get('generated_files', {})}
        conditions = {**details.get('conditions', {}), **(metadata or {}).get('conditions', {})}
        details.update(metadata or {})
        details.update(generated_files=generated, conditions=conditions)
        if initial_source:
            details['editor_source_created'] = self.projects.create_file_if_missing(
                project_id, initial_source['path'], initial_source['content'])
        return self.runner.start(root, kind, **specification)

    def run_path(self, project_id, run_id):
        root = self.run_root(project_id, run_id)
        self.runner.get(root, run_id)
        return root / '.af/runs' / run_id

    def log(self, project_id, run_id):
        directory = self.run_path(project_id, run_id) / 'output'
        contents = []
        for filename in ('process.log', 'ngspice.log', 'drc.txt', 'lvs.txt', 'extraction.txt'):
            path = directory / filename
            if path.exists() and not path.is_symlink():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 256000))
                    contents.append(f'--- {filename} ---\n' + stream.read().decode(errors='replace'))
        return {'content': '\n'.join(contents), 'limit': 'last 256 KiB per log'}

    def artifact(self, project_id, run_id, relative):
        path = confined(self.run_path(project_id, run_id) / 'output', relative)
        if not path.is_file():
            raise FileNotFoundError('Artifact does not exist')
        return path

    def waveforms(self, project_id, run_id):
        path = self.artifact(project_id, run_id, 'waveforms.raw')
        return waveforms.parse_raw(path)

    def configure_simulation(self, project_id, config):
        from .simulation import configure
        source_path = config.get('source', 'testbenches/pre.cir')
        source = self.projects.read(project_id, source_path)['content']
        output = configure(source, config.get('analysis', 'tran'), config.get('parameters', {}), config.get('directives', ''))
        return self.projects.write(project_id, config.get('netlist', 'testbenches/configured.cir'), output)

    def workflows(self, project_id):
        root = self.projects.path(project_id)
        result = []
        for path in sorted((root / 'workflows').glob('*.json')):
            if not path.is_symlink():
                try:
                    result.append(read_json(path))
                except (ValueError, OSError):
                    continue
        return result

    def save_workflow(self, project_id, workflow):
        from .workflows import validate_workflow
        validated = validate_workflow(workflow)
        name = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(validated.get('name', 'workflow'))).strip('-')[:80] or 'workflow'
        return self.projects.write(project_id, f'workflows/{name}.json', json.dumps(validated, indent=2))

    def start_workflow(self, project_id, workflow):
        from .workflows import validate_workflow, execute_tuning
        flow = validate_workflow(workflow)
        root = self.projects.path(project_id)
        with self.lock:
            if any(j['status'] in ('running', 'queued') for j in self.jobs.list(root)):
                raise ValueError('A workflow or evaluation is already active for this project')
            return self.jobs.start(root, 'tuning', lambda job, event, update: execute_tuning(self, project_id, flow, job, event, update), {'workflow': flow})

    def evaluate(self, project_id, options):
        from .evaluation import run_evaluation
        root = self.projects.path(project_id)
        with self.lock:
            if any(j['status'] in ('running', 'queued') for j in self.jobs.list(root)):
                raise ValueError('A workflow or evaluation is already active for this project')
            return self.jobs.start(root, 'evaluation', lambda job, event, update: run_evaluation(self, project_id, options, job, event, update))

    def chat(self, project_id, message, selected):
        if not isinstance(selected, list) or len(selected) > 20:
            raise ValueError('Select up to 20 context files')
        files = [self.projects.read(project_id, name) for name in selected]
        response = self.provider(project_id).chat(message, files)
        record = {'id': uuid.uuid4().hex, 'message': message, **response}
        atomic_json(self.projects.path(project_id) / '.af/conversations' / f'{record["id"]}.json', record)
        return response

    def provider_test(self):
        return self.provider().models()
