#!/usr/bin/env python3
"""Optionally install pinned Codex and Claude CLIs into this project's .tools.

Requires Node.js 22 or newer with npm. This does not install Node, authenticate,
change application settings, or install global npm packages. An existing target
is reused only when its manifest and real executable versions both verify.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / '.tools' / 'agent-clis'
PACKAGES = {'@openai/codex': '0.159.2', '@anthropic-ai/claude-code': '2.1.280'}
CLI_PACKAGES = {'codex': '@openai/codex', 'claude': '@anthropic-ai/claude-code'}
NODE_HELP = ('Node.js 22 or newer is required. Install a Node.js 22 LTS distribution '
             'with npm, then pass --node /path/to/node; this installer does not download Node.')


def run(command, *, environment, operation, directory=None, timeout=30):
    """Keep subprocess output private; version checks parse only known formats."""
    try:
        result = subprocess.run(command, cwd=directory, env=environment,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f'{operation} failed; check executable availability and retry.') from error
    if result.returncode:
        raise RuntimeError(f'{operation} failed (exit {result.returncode}); '
                           'tool output is omitted to avoid exposing configuration or credentials.')
    return result.stdout.strip()


def environment_for(node, directory):
    # npm otherwise reads ~/.npmrc and writes ~/.npm. Override both without
    # changing HOME or retaining the environment in installation metadata.
    environment = {key: value for key, value in os.environ.items()
                   if not key.lower().startswith('npm_config_')}
    environment.update({
        'PATH': str(node.parent) + os.pathsep + environment.get('PATH', ''),
        'npm_config_cache': str(directory / '.npm-cache'),
        'npm_config_userconfig': str(directory / '.npm-userconfig'),
        'npm_config_globalconfig': str(directory / '.npm-globalconfig'),
        'npm_config_update_notifier': 'false',
    })
    return environment


def runtime(node, directory):
    executable = shutil.which(str(node))
    if not executable:
        raise RuntimeError(NODE_HELP)
    node_path = Path(executable).resolve()
    environment = environment_for(node_path, directory)
    try:
        output = run([str(node_path), '--version'], environment=environment,
                     operation='Node.js version check')
    except RuntimeError as error:
        raise RuntimeError(NODE_HELP) from error
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', output)
    if not match or int(match[1]) < 22:
        raise RuntimeError(NODE_HELP)
    node_version = '.'.join(match.groups())
    sibling = node_path.with_name('npm')
    npm_executable = str(sibling) if sibling.is_file() else shutil.which('npm')
    if not npm_executable:
        raise RuntimeError('npm is required alongside Node.js 22; install a Node distribution that includes npm.')
    npm_path = Path(npm_executable).resolve()
    return node_path, node_version, npm_path, environment


def verify_clis(directory, environment):
    versions = {}
    for name, package in CLI_PACKAGES.items():
        entrypoint = directory / 'node_modules' / '.bin' / name
        executable = directory / 'bin' / name
        expected = PACKAGES[package]
        for path in (entrypoint, executable):
            if (not path.is_file() or not os.access(path, os.X_OK)
                    or not path.resolve().is_relative_to(directory)):
                raise RuntimeError(f'{name} {expected} executable is missing or outside the installation directory.')
        output = run([str(executable), '--version'], environment=environment,
                     operation=f'{name} {expected} version check', directory=directory)
        pattern = r'codex(?:-cli)? (\d+\.\d+\.\d+)' if name == 'codex' else r'(\d+\.\d+\.\d+)(?: \(Claude Code\))?'
        match = re.fullmatch(pattern, output)
        if not match or match[1] != expected:
            raise RuntimeError(f'{name} version verification failed; expected {expected}.')
        versions[name] = {'path': str(executable), 'version': expected}
    return versions


def write_launchers(directory, node):
    """Make direct invocation select the same Node used during installation."""
    (directory / 'bin').mkdir()
    for name in CLI_PACKAGES:
        entrypoint = directory / 'node_modules' / '.bin' / name
        launcher = directory / 'bin' / name
        launcher.write_text('#!/bin/sh\nset -eu\nPATH=' + shlex.quote(str(node.parent))
                            + '${PATH:+:"$PATH"}\nexport PATH\nexec '
                            + shlex.quote(str(entrypoint)) + ' "$@"\n')
        launcher.chmod(0o755)


def install(directory=DEFAULT_DIRECTORY, node='node'):
    """Return the verified installation manifest, installing only in a new target.

    Failures leave any partial installation available for inspection without a
    success marker. A later call refuses that unknown directory rather than
    deleting or overwriting it; choose a new directory after inspecting a failure.
    """
    requested_directory = Path(directory).expanduser().absolute()
    if requested_directory.is_symlink():
        raise RuntimeError('The installation directory must not be a symlink.')
    directory = requested_directory.resolve()
    node_path, node_version, npm_path, environment = runtime(node, directory)
    marker = directory / 'installation.json'
    manifest = {'schema_version': 1, 'packages': dict(PACKAGES),
                'node': {'path': str(node_path), 'version': node_version}}
    saved = None
    if directory.exists():
        if not marker.is_file() or marker.is_symlink():
            raise RuntimeError('Refusing an existing installation directory without a recognized manifest.')
        try:
            saved = json.loads(marker.read_text())
        except (OSError, ValueError) as error:
            raise RuntimeError('Existing installation manifest cannot be read or validated.') from error
        if not isinstance(saved, dict) or any(saved.get(key) != value for key, value in manifest.items()):
            raise RuntimeError('Existing installation manifest differs from the pinned packages or selected runtime.')
        if not isinstance(saved.get('npm'), dict) or saved['npm'].get('path') != str(npm_path):
            raise RuntimeError('Existing installation manifest differs from the selected npm runtime.')
        expected_clis = {name: {'path': str(directory / 'bin' / name),
                               'version': PACKAGES[package]} for name, package in CLI_PACKAGES.items()}
        if saved.get('clis') != expected_clis or set(saved) != {*manifest, 'npm', 'clis'}:
            raise RuntimeError('Existing installation manifest has unrecognized executable paths or fields.')
    else:
        directory.mkdir(parents=True, exist_ok=False)

    # npm rejects user/global config resolving to the same file, including
    # /dev/null. Distinct empty local files suppress user configuration safely.
    for name in ('.npm-userconfig', '.npm-globalconfig'):
        config = directory / name
        if saved is None:
            config.touch(exist_ok=False)
        elif config.is_symlink() or not config.is_file() or config.stat().st_size != 0:
            raise RuntimeError('Local npm configuration changed; refusing to read or overwrite it.')

    # Even npm --version can initialize its cache. Run npm only after validating
    # the target, with its cwd and cache there and user/global config disabled.
    # The chosen Node executes npm's JS entry point, avoiding ambient Node selection.
    npm_version = run([str(node_path), str(npm_path), '--version'], environment=environment,
                      operation='npm version check', directory=directory)
    if not re.fullmatch(r'\d+\.\d+\.\d+', npm_version):
        raise RuntimeError('npm version check returned an unsupported version format.')
    manifest['npm'] = {'path': str(npm_path), 'version': npm_version}
    if saved is not None:
        if saved['npm'] != manifest['npm']:
            raise RuntimeError('Existing installation manifest differs from the installed npm version.')
        manifest['clis'] = verify_clis(directory, environment)
        return manifest

    command = [str(node_path), str(npm_path), 'install', '--prefix', str(directory),
               '--no-audit', '--no-fund', '--save-exact',
               *(f'{package}@{version}' for package, version in PACKAGES.items())]
    run(command, environment=environment, operation='npm installation',
        directory=directory, timeout=600)
    write_launchers(directory, node_path)
    manifest['clis'] = verify_clis(directory, environment)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         prefix='.installation-', suffix='.json',
                                         dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, indent=2)
            stream.write('\n')
        temporary.replace(marker)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY,
                        help='New project-local installation directory')
    parser.add_argument('--node', default='node', help='Node.js 22+ executable (default: node on PATH)')
    args = parser.parse_args()
    try:
        manifest = install(directory=args.directory, node=args.node)
    except (RuntimeError, OSError) as error:
        parser.exit(1, f'Agent CLI setup failed: {error}\n')
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
