Noto Sans SC — bundled Chinese interface subset

noto-sans-sc-ui.woff2 contains the Simplified Chinese characters and punctuation
authored in this application's HTML and JavaScript interface strings. It provides
Chinese interface glyphs on computers without a Chinese system font. Latin text
keeps using the existing system font stack. One regular weight is included;
browsers synthesize heavier weights when the interface requests them.

The font is served locally. Opening the application never contacts Google Fonts.
Its purpose is interface text; arbitrary Chinese user content may still need a
system font for characters outside the bundled subset.

Upstream family: Noto Sans SC
Upstream copyright: 2014–2021 Adobe, as retained in OFL.txt.
Source: https://github.com/google/fonts/tree/main/ofl/notosanssc
Subset API: https://developers.google.com/fonts/docs/css2
License: SIL Open Font License 1.1; see the accompanying OFL.txt.
source.json records exact download URLs, included characters, size and SHA-256.

To refresh after adding Chinese interface text, run from the repository root:
    python3 scripts/update_ui_font.py
This optional maintenance command needs network access. Runtime use does not.

cjk-ui-supplement.woff2 supplies UI characters added after the primary subset.
It is generated offline from Sarasa Mono SC, with the original copyright and
license retained in CJK-supplement-OFL.txt. cjk-supplement-source.json records
the source font hash, derivative hash, and covered characters. It is loaded
only when the primary subset does not contain a required UI character.

Offline maintenance (optional FontTools and Brotli build dependencies):
    python3 scripts/update_ui_font.py --local-font /path/to/SarasaMonoSC-Regular.woff2 --local-license /path/to/SarasaMono-LICENSE.txt
This mode sends no interface text or files to a network service.
