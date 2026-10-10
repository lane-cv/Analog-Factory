"""Shared workspace use cases for manual UI, CLI, and workflow execution."""
import copy
import json
import os
import re
import threading
import uuid
from pathlib import Path

from . import __version__, eda, waveforms
from .credentials import CredentialStore
from .agents import AgentManager
from .execution import RunManager
from .jobs import Jobs
from .projects import ProjectStore
from .providers import Provider, provider_binding, public_config
from .storage import atomic_json, confined, read_json


DEFAULT_UI = {'language': 'en', 'theme': 'dark'}
DEFAULT_CLI_UPDATES = {'automatic': False, 'interval_hours': 24}
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
        self._operations = None

    @property
    def operations(self):
        """One registry per workspace, shared by transports and internal agents."""
        with self.lock:
            if self._operations is None:
                from .operations import OperationRegistry
                from .operation_catalog import register_operations
                from .operation_lifecycle import register_lifecycle
                from .operation_simulation_batches import register_simulation_batches
                from .operation_terminals import register_terminals
                from .operation_transfers import register_transfers
                from .operation_verification import register_verification
                from .operation_characterization import register_characterization
                from .operation_native_canvas import register_native_canvas
                from .operation_training import register_operations as register_training
                from .operation_cli_updates import register_cli_updates
                from .operation_ui import register_operations as register_ui
                registry = OperationRegistry(self)
                register_operations(registry)
                register_lifecycle(registry)
                register_simulation_batches(registry)
                register_terminals(registry)
                register_transfers(registry)
                register_verification(registry)
                register_characterization(registry)
                register_native_canvas(registry)
                register_training(registry)
                register_cli_updates(registry)
                register_ui(registry)
                self._operations = registry
            return self._operations

    def settings(self):
        with self.lock:
            settings = public_config(copy.deepcopy(self._settings))
            settings['ui'] = {**DEFAULT_UI, **settings.get('ui', {})}
            settings['cli_updates'] = {**DEFAULT_CLI_UPDATES, **settings.get('cli_updates', {})}
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
        allowed = {'tools', 'pdk_root', 'pdk_variant', 'pdk_version', 'provider', 'ui', 'cli_updates'}
        with self.lock:
            updated = {**self._settings, **{k: v for k, v in settings.items() if k in allowed}}
            provider_patch = settings.get('provider', {})
            if not isinstance(provider_patch, dict):
                raise ValueError('Provider configuration must be an object')
            if 'provider' in settings:
                updated['provider'] = {**self._settings.get('provider', {}), **provider_patch}
            if 'cli_updates' in settings:
                if not isinstance(settings['cli_updates'], dict):
                    raise ValueError('CLI update preferences must be an object')
                updated['cli_updates'] = {**DEFAULT_CLI_UPDATES, **self._settings.get('cli_updates', {}), **settings['cli_updates']}
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
        manager = getattr(self, 'cli_updates', None)
        if manager is not None and 'cli_updates' in settings:
            manager.reconfigure()
        return self.settings()

    @staticmethod
    def validate_config(config):
        if not isinstance(config, dict):
            raise ValueError('Configuration must be an object')
        if 'cli_updates' in config:
            preferences = config['cli_updates']
            if not isinstance(preferences, dict) or set(preferences) - set(DEFAULT_CLI_UPDATES):
                raise ValueError('Unknown CLI update preferences')
            if 'automatic' in preferences and type(preferences['automatic']) is not bool:
                raise ValueError('Automatic CLI checking must be a boolean')
            if 'interval_hours' in preferences and (type(preferences['interval_hours']) is not int or not 6 <= preferences['interval_hours'] <= 168):
                raise ValueError('CLI check interval must be an integer from 6 to 168 hours')
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
        if not isinstance(config, dict):
            self.validate_config(config)
        if 'cli_updates' in config:
            raise ValueError('CLI update checking is a workspace preference')
        # Cross-field model capabilities must be checked against inherited
        # defaults while only the explicit project override is persisted.
        effective = self._merge_config(config)
        self.validate_config({**config, 'provider': effective.get('provider', {})})
        return self.projects.configure(project_id, persisted_config(config))

    def config(self, project_id=None):
        overrides = self.projects.get(project_id).get('config', {}) if project_id else {}
        return self._merge_config(overrides)

    def _merge_config(self, overrides):
        result = copy.deepcopy(self._settings)
        result.pop('cli_updates', None)
        if overrides:
            result.update({k: v for k, v in overrides.items() if k not in ('tools', 'provider')})
            for key in ('tools', 'provider'):
                result[key] = {**result.get(key, {}), **overrides.get(key, {})}
            changed_endpoint = self._provider_binding(result.get('provider', {})) != self._provider_binding(self._settings.get('provider', {}))
            changed_model = result.get('provider', {}).get('model', '') != self._settings.get('provider', {}).get('model', '')
            declared = overrides.get('provider', {})
            if changed_endpoint or changed_model:
                if 'capabilities' not in declared:
                    result['provider'].pop('capabilities', None)
            if changed_endpoint:
                if 'model_capabilities' not in declared:
                    result['provider'].pop('model_capabilities', None)
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

    def run_availability(self, project_id, kind, options=None, *, detected=None):
        """Shared capability predicates for discovery, UI controls and launch.

        Source paths and saved input files are validated by ``eda.prepare`` at
        launch; availability describes the selected project's environment.
        """
        if not project_id:
            return {'available': False, 'reasons': ['Select a project to inspect EDA availability']}
        project = self.projects.get(project_id)
        detected = detected if detected is not None else eda.detect(self.config(project_id))
        tool_name = {'simulate': 'ngspice', 'drc': 'magic', 'extract': 'magic', 'lvs': 'netgen',
                     'magic': 'magic', 'xschem': 'xschem'}.get(kind)
        tool = detected['tools'].get(tool_name)
        if tool is None:
            return {'available': False, 'reasons': ['Unsupported EDA task or unavailable tool detection']}
        reasons = [] if tool['available'] else [tool.get('diagnostic') or f'{tool_name} is unavailable']
        pdk = detected['pdk']
        if kind == 'simulate' and project.get('template') in ('sky130', 'ota5') and not pdk['available']:
            reasons.append('The SKY130 template requires prepared PDK assets: ' + pdk['diagnostic'])
        elif kind in ('magic', 'drc', 'extract', 'lvs'):
            setup = pdk['paths'].get('netgen' if kind == 'lvs' else 'magic')
            if not pdk['available'] or not setup or not Path(setup).is_file():
                reasons.append(pdk['diagnostic'])
        if kind == 'magic' or (kind == 'xschem' and not (options or {}).get('netlist_only')):
            desktop = detected['desktop']
            if (desktop.get('available') is False or desktop.get('gui_available') is False or
                    (desktop.get('display') is False and desktop.get('wayland_display') is False)):
                reasons.append(desktop.get('diagnostic') or 'No graphical desktop is available')
        return {'available': not reasons, 'reasons': reasons}

    def status(self, project_id=None):
        from .training.manager import resources
        config = self.config(project_id)
        detected = eda.detect(config)
        trainer = resources()['frameworks']['trl_cpu']
        return {'version': __version__, 'project_id': project_id, 'toolchain': detected,
                'operation_availability': {kind: self.run_availability(project_id, kind, detected=detected)
                                           for kind in ('simulate', 'xschem', 'magic', 'drc', 'lvs', 'extract')},
                'capabilities': {
                    'manual': {'available': True, 'status': 'implemented'},
                    'simulation': {'available': detected['tools']['ngspice']['available'], 'status': 'requires ngspice'},
                    'assistant': {'available': bool(config.get('provider', {}).get('base_url')),
                                  'status': 'API sessions with confirmations or human-approved bounded scope'},
                    'workflows': {'available': True, 'status': 'v1 tuning and v2 typed graph execution'},
                    'evaluation': {'available': True, 'status': 'isolated EDA evaluation and fixed held-out tasksets'},
                    'training': {'available': trainer['available'], 'status': 'optional local CPU TRL weight training',
                                 'reason': trainer['reason']},
                    'cli_agents': {'available': any(item['available'] for item in self.agents.backends(project_id)['backends'] if item['id'] != 'api'),
                                   'status': 'embedded sessions; CLI login required'},
                }}

    def detail(self, project_id):
        from .design_progress import build
        root = self.projects.path(project_id)
        project = self.projects.get(project_id)
        files = self.projects.files(project_id)
        runs = self.runs(project_id)
        return {'project': project, 'configuration': public_config(self.config(project_id)),
                'files': files, 'runs': runs, 'jobs': self.jobs.list(root),
                'design_progress': build(root, project, files, runs, config=self.config(project_id))}

    def _episode_roots(self, project_id):
        root = self.projects.path(project_id)
        episodes = confined(root, '.af/episodes', hidden=True)
        for path in episodes.glob('*/*'):
            if path.is_dir() and re.fullmatch('[0-9]+', path.name):
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
        availability = self.run_availability(project_id, kind, options)
        try:
            specification = eda.prepare(root, kind, options or {}, self.config(project_id))
            if not availability['available']:
                raise RuntimeError('; '.join(availability['reasons']))
        except RuntimeError as error:
            reason = '; '.join(availability['reasons']) or str(error)
            unavailable = self.runner.start(root, kind, [], metadata={'unavailable': reason})
            # Capability failure is already known; return the real terminal
            # evidence rather than racing an empty worker's queued status.
            return self.runner.wait(root, unavailable['id'], timeout=5)
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
        if flow['schema_version'] == 2:
            from .operation_graph import start_graph
            return start_graph(self, project_id, flow)
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
