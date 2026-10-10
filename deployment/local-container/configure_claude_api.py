"""Copy the selected local provider connection into private Claude settings."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from analog_factory.credentials import CredentialStore
from analog_factory.providers import provider_binding

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--project-id')
args = parser.parse_args()
provider = json.loads((ROOT / '.workspace/settings.json').read_text())['provider']
if args.project_id:
    if not args.project_id.isalnum():
        raise SystemExit('Invalid project ID')
    project = json.loads((ROOT / '.workspace/projects' / args.project_id / 'project.json').read_text())
    provider = {**provider, **project.get('config', {}).get('provider', {})}
binding = provider_binding(provider)
secret = CredentialStore(ROOT / '.workspace').load(binding)
if not secret:
    raise SystemExit('No stored credential matches the selected endpoint')
base_url = binding[1]
if base_url.endswith('/v1'):
    base_url = base_url[:-3]
if urllib.parse.urlsplit(base_url).scheme != 'https':
    raise SystemExit('Claude gateway configuration requires an HTTPS endpoint')
if not isinstance(provider.get('model'), str) or not provider['model']:
    raise SystemExit('Select a provider model first')
directory = ROOT / '.tools/deployment/claude-home'
if any(path.is_symlink() for path in (ROOT / '.tools', ROOT / '.tools/deployment', directory)):
    raise SystemExit('Claude configuration directory must not be a symlink')
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
directory.chmod(0o700)
path = directory / 'settings.json'
if path.is_symlink():
    raise SystemExit('Claude settings must not be a symlink')
settings = json.loads(path.read_text()) if path.exists() else {}
environment = settings.setdefault('env', {})
for name in ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_MODEL'):
    environment.pop(name, None)
environment.update(ANTHROPIC_BASE_URL=base_url, ANTHROPIC_AUTH_TOKEN=secret)
settings['model'] = provider['model']
descriptor, temporary = tempfile.mkstemp(prefix='.settings-', dir=directory)
try:
    with os.fdopen(descriptor, 'w') as output:
        json.dump(settings, output, indent=2)
        output.write('\n')
    os.replace(temporary, path)
finally:
    Path(temporary).unlink(missing_ok=True)
if path.stat().st_mode & 0o777 != 0o600:
    raise SystemExit('Claude settings permissions must be 0600')
report = {'base_url': base_url, 'model': provider['model'], 'credential_source': 'workspace credential store', 'auth_variable': 'ANTHROPIC_AUTH_TOKEN', 'project_id': args.project_id, 'settings_mode': '0600'}
(ROOT / '.validation').mkdir(exist_ok=True)
(ROOT / '.validation/claude-api-configuration.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
