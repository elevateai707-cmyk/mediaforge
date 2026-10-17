"""Read-only install identity; never exposes secrets or machine media paths."""
from __future__ import annotations
import hashlib
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from . import config
from .edits import style_pack

ROOT = Path(__file__).resolve().parents[2]

@lru_cache(maxsize=1)
def build_info():
    digest = hashlib.sha256()
    for folder in ('backend/app', 'frontend/src'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and path.suffix in {'.py', '.json', '.tsx', '.ts', '.css'}:
                digest.update(path.relative_to(ROOT).as_posix().encode())
                digest.update(b'\0' + path.read_bytes() + b'\0')
    try:
        revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short=12', 'HEAD'], capture_output=True, text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = 'unavailable'
    profile = json.loads((ROOT / 'backend/app/edits/creative_profile.json').read_text())
    try:
        families = subprocess.run(['fc-list', '--format=%{family}\n'], capture_output=True, text=True, timeout=3, check=True).stdout
        installed = {f.strip() for line in families.splitlines() for f in line.split(',')}
        missing = [f for f in style_pack.FONTS if f not in installed]
    except (OSError, subprocess.SubprocessError):
        missing = None
    return {'version': config.VERSION, 'revision': revision, 'source_fingerprint': digest.hexdigest()[:16],
            'creative_profile': profile['version'], 'caption_presets': list(style_pack.PRESETS),
            'features': ['library-upload', 'watched-folders', 'creator-analysis', 'filming-briefs', 'daily-broll', 'speech-captions', 'style-recipes', 'resolve-handoff'],
            'ffmpeg_available': bool(shutil.which('ffmpeg')), 'missing_fonts': missing,
            'note': 'Matching source fingerprints confirm matching app source. Models, fonts, provider accounts, media and saved projects are configured separately on each machine.'}
