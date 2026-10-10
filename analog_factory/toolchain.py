"""Detect tool executables and prepared SKY130 assets without installing anything."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

_CACHE = {}
_PROBES = {'ngspice': ['--version'], 'xschem': ['--version'], 'magic': ['--version'],
           'netgen': ['-batch'], 'codex': ['--version'], 'claude': ['--version']}


def _tool(name, configured):
    candidate = str(configured) if configured else name
    path = shutil.which(candidate)
    if not path:
        return {'available': False, 'path': candidate if configured else None, 'version': None,
                'diagnostic': f'{name} executable not found; configure tools.{name} or add it to PATH'}
    stat = Path(path).stat()
    key = (name, path, stat.st_mtime_ns)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        return dict(cached[1])
    result = {'available': False, 'path': str(Path(path).absolute()), 'version': None, 'diagnostic': ''}
    try:
        completed = subprocess.run([path, *_PROBES[name]], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=3, text=True, errors='replace')
        output = completed.stdout.strip()
        version = next((line.strip(' *\t') for line in output.splitlines()
                        if re.search(r'\d+\.\d+|ngspice-\d+', line)), output.splitlines()[0] if output else '')
        recognized = completed.returncode == 0 and bool(output)
        if name == 'netgen':
            recognized = recognized and not re.search(r'mesh|ngsolve', output, re.I) and bool(
                re.search(r'netgen.*(?:1\.[45]|circuit)|(?:^|\n)\s*1\.[45]\.\d+|circuit.*compar|LVS', output, re.I))
        result.update(available=recognized, version=version or None,
                      diagnostic='Detected executable and version' if recognized else
                      ('Executable is not a recognized Netgen LVS comparator (the mesh generator is a different program)'
                       if name == 'netgen' else f'Version probe failed (exit {completed.returncode}): {output[:500]}'))
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['diagnostic'] = f'Version probe failed: {exc}'
    _CACHE[key] = (time.monotonic(), result)
    return dict(result)


def detect(config: dict | None = None) -> dict:
    """Return tools, PDK asset paths/identity, and graphical-session diagnostics.

    Configuration accepts ``tools: {name: executable}``, ``pdk_root`` (the
    directory containing sky130A), and ``pdk_variant`` (default sky130A).
    PDK availability requires xschem, Magic, Netgen, and model configuration
    files; an existing empty directory does not count as a prepared PDK.
    """
    from .ota5_reference import check_prepared

    config = config or {}
    configured = config.get('tools', {})
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {name: pool.submit(_tool, name, configured.get(name)) for name in _PROBES}
        tools = {name: future.result() for name, future in futures.items()}
    variant = config.get('pdk_variant') or os.environ.get('PDK') or 'sky130A'
    if not re.fullmatch(r'[A-Za-z0-9_-]+', variant):
        raise ValueError('Invalid PDK variant')
    root_value = config.get('pdk_root') or os.environ.get('PDK_ROOT')
    root = Path(root_value).expanduser().absolute() if root_value else None
    base = root / variant if root else None
    expected = {'xschem': f'libs.tech/xschem/xschemrc',
                'magic': f'libs.tech/magic/{variant}.magicrc',
                'netgen': f'libs.tech/netgen/{variant}_setup.tcl',
                'models': 'libs.tech/ngspice/sky130.lib.spice'}
    paths = {key: str(base / relative) if base else None for key, relative in expected.items()}
    missing = [key for key, path in paths.items() if not path or not Path(path).is_file()]
    version = None
    hashes = {}
    if base and base.is_dir():
        for name in ['.config/nodeinfo.json', 'SOURCES', 'VERSION']:
            file = base / name
            if file.is_file():
                version = file.read_text(errors='replace').strip()[:2000]
                break
        for name, path in paths.items():
            if Path(path).is_file():
                hashes[name] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    display = os.environ.get('DISPLAY')
    wayland = os.environ.get('WAYLAND_DISPLAY')
    desktop = {'available': bool(display), 'display': display, 'wayland_display': wayland,
               'diagnostic': 'X11/XWayland DISPLAY is configured; GUI launch still needs validation' if display else
               'No X11 DISPLAY is configured; xschem and Magic GUI need X11 or XWayland'}
    return {'tools': tools, 'pdk': {'available': not missing, 'root': str(root) if root else None,
                                  'variant': variant, 'version': version, 'paths': paths,
                                  'configuration_hashes': hashes, 'ota5': check_prepared(config),
                                  'diagnostic': 'Prepared PDK assets detected; rule execution still needs validation' if not missing else
                                  'Prepared SKY130 assets missing: ' + ', '.join(missing)}, 'desktop': desktop}
