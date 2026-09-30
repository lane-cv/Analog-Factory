#!/usr/bin/env python3
"""Refresh the bundled Simplified Chinese UI font subsets.

Downloads a regular-weight Noto Sans SC WOFF2 containing the Chinese characters
and punctuation authored in static HTML/JS. The application serves the resulting
font locally; this optional maintenance command is its only network dependency.
Run after adding Chinese translations: python3 scripts/update_ui_font.py
For an offline supplement, provide --local-font and --local-license. This mode
requires optional FontTools/Brotli maintenance dependencies and makes no requests.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import re
import urllib.parse
import urllib.request
import shutil
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


def local_supplement(source: Path, license_path: Path) -> None:
    """Subset an already available OFL font locally, without any network requests."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    primary = json.loads((DESTINATION / 'source.json').read_text(encoding='utf-8'))
    characters = ''.join(sorted(set(interface_characters()) - set(primary['characters'])))
    if not characters:
        print('The primary font already covers every authored interface character')
        return
    license_text = license_path.read_text(encoding='utf-8')
    if 'SIL Open Font License' not in license_text and 'SIL OPEN FONT LICENSE' not in license_text:
        raise ValueError('Supply the source font’s SIL Open Font License')
    font = TTFont(source)
    family = font['name'].getDebugName(1)
    missing = set(map(ord, characters)) - font.getBestCmap().keys()
    if missing:
        raise ValueError(f'The supplied font lacks {len(missing)} required characters')
    options = subset.Options()
    options.flavor = 'woff2'
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=characters)
    subsetter.subset(font)
    # Name the derivative explicitly; retain the upstream copyright/license records.
    names = {1:'Analog Factory CJK Supplement', 2:'Regular', 3:'AnalogFactoryCJKSupplement-Regular',
             4:'Analog Factory CJK Supplement Regular', 6:'AnalogFactoryCJKSupplement-Regular',
             16:'Analog Factory CJK Supplement', 17:'Regular'}
    for record in font['name'].names:
        if record.nameID in names:
            record.string = names[record.nameID].encode(record.getEncoding())
    filename = 'cjk-ui-supplement.woff2'
    font.flavor = 'woff2'
    font.save(DESTINATION / filename)
    data = (DESTINATION / filename).read_bytes()
    shutil.copyfile(license_path, DESTINATION / 'CJK-supplement-OFL.txt')
    metadata = {'family':family, 'source_file':source.name,
                'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'license':'SIL Open Font License 1.1', 'file':filename,
                'sha256':hashlib.sha256(data).hexdigest(), 'bytes':len(data),
                'characters':characters, 'character_count':len(characters)}
    (DESTINATION / 'cjk-supplement-source.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Locally bundled {len(characters)} missing characters in {len(data):,} bytes')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-font', type=Path, help='Build a local supplement from an existing OFL font')
    parser.add_argument('--local-license', type=Path, help='License distributed with the local source font')
    args = parser.parse_args()
    if args.local_font or args.local_license:
        if not (args.local_font and args.local_license):
            parser.error('--local-font and --local-license must be supplied together')
        local_supplement(args.local_font, args.local_license)
        return
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
