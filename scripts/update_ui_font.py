#!/usr/bin/env python3
"""Refresh the bundled Simplified Chinese UI font subset (network required).

Downloads a regular-weight Noto Sans SC WOFF2 containing the Chinese characters
and punctuation authored in static HTML/JS. The application serves the resulting
font locally; this optional maintenance command is its only network dependency.
Run after adding Chinese translations: python3 scripts/update_ui_font.py
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'analog_factory' / 'static'
DESTINATION = STATIC / 'fonts'
FONT_FILE = 'noto-sans-sc-ui.woff2'
LICENSE_URL = 'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt'
SOURCE_URL = 'https://github.com/google/fonts/tree/main/ofl/notosanssc'
# The API selects WOFF2 when presented with a modern browser user agent.
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


def interface_characters() -> str:
    text = ''.join(path.read_text(encoding='utf-8') for path in sorted(STATIC.iterdir())
                   if path.suffix in {'.html', '.js'})
    return ''.join(sorted({char for char in text if
                           '\u2e80' <= char <= '\u9fff'
                           or '\uf900' <= char <= '\ufaff'
                           or '\uff00' <= char <= '\uffef'}))


def fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'Download exceeds {limit} bytes: {urllib.parse.urlsplit(url).hostname}')
    return data


def main() -> None:
    characters = interface_characters()
    if not characters:
        raise ValueError('No Chinese interface text found')
    css_url = 'https://fonts.googleapis.com/css2?' + urllib.parse.urlencode({
        'family': 'Noto Sans SC:wght@400', 'display': 'swap', 'text': characters,
    })
    css = fetch(css_url, 256_000).decode('utf-8')
    matches = re.findall(r'url\(([^)]+)\)\s*format\([\'\"]woff2[\'\"]\)', css)
    if len(matches) != 1:
        raise ValueError('Expected one WOFF2 subset in the Google Fonts response')
    font_url = matches[0].strip('\'\"')
    source = urllib.parse.urlsplit(font_url)
    if source.scheme != 'https' or source.hostname != 'fonts.gstatic.com':
        raise ValueError('Font URL is not from the expected Google Fonts origin')
    font = fetch(font_url, 500_000)
    if font[:4] != b'wOF2':
        raise ValueError('Downloaded font is not WOFF2')
    license_text = fetch(LICENSE_URL, 32_000).decode('utf-8')
    if 'SIL OPEN FONT LICENSE Version 1.1' not in license_text:
        raise ValueError('Expected the upstream SIL Open Font License')
    metadata = {
        'family': 'Noto Sans SC', 'style': 'Regular', 'weight': 400,
        'license': 'SIL Open Font License 1.1', 'upstream': SOURCE_URL,
        'license_url': LICENSE_URL, 'subset_api': 'https://developers.google.com/fonts/docs/css2',
        'css_url': css_url, 'font_url': font_url,
        'downloaded_at': datetime.now(timezone.utc).isoformat(),
        'file': FONT_FILE, 'bytes': len(font),
        'sha256': hashlib.sha256(font).hexdigest(),
        'characters': characters, 'character_count': len(characters),
    }
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / FONT_FILE).write_bytes(font)
    (DESTINATION / 'OFL.txt').write_text(license_text, encoding='utf-8')
    (DESTINATION / 'source.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Bundled {len(characters)} interface characters in {len(font):,} bytes ({FONT_FILE})')


if __name__ == '__main__':
    main()
