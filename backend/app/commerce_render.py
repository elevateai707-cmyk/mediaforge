"""Real Reel Pack renderer, reusing MediaForge's timeline and video builder.
No paid TTS/LLM calls: sandbox pack uses validated preset copy and mastered SFX.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/reel_builder.py'
spec = importlib.util.spec_from_file_location('commerce_reel_builder', SCRIPT)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)

def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, timeout=900, **kwargs)

def probe(path):
    data = json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_streams','-show_format','-of','json',str(path)]).stdout)
    video = next(s for s in data['streams'] if s['codec_type'] == 'video')
    return {'format':data['format']['format_name'], 'duration': float(data['format']['duration']), 'width': video['width'], 'height': video['height'], 'fps': video['r_frame_rate'], 'audio': any(s['codec_type']=='audio' for s in data['streams'])}

def score_segment(path, start):
    # Comparable motion metric across files; use the same 64x36 / 4fps basis.
    raw = run(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),'-t','10.5','-vf','fps=4,scale=64:36,format=gray','-an','-f','rawvideo','-']).stdout
    import numpy as np
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 64*36).astype(float)
    motion = abs(frames[1:] - frames[:-1]).mean(axis=1)
    return float(motion.mean() - .25 * (motion.max() - motion.mean()))

def loudness(path):
    result = run(['ffmpeg','-hide_banner','-i',str(path),'-af','loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json','-f','null','-'])
    report = result.stderr.decode()
    return json.loads(report[report.rfind('{'):])

def master_audio(mixed, final, work):
    normalized = work / 'normalized.mp4'
    run(['ffmpeg','-v','error','-i',str(mixed),'-t','10','-c:v','copy','-af','loudnorm=I=-14:TP=-1.5:LRA=11','-c:a','aac','-b:a','192k',str(normalized),'-y'])
    measured = loudness(normalized)
    gain = -14 - float(measured['input_i'])
    if not -60 < gain < 60 or float(measured['input_tp']) + gain > -1.0:
        raise ValueError('Audio cannot meet loudness and peak targets')
    run(['ffmpeg','-v','error','-i',str(normalized),'-t','10','-c:v','copy','-af',f'volume={gain}dB','-c:a','aac','-b:a','192k','-movflags','+faststart',str(final),'-y'])
    verified = loudness(final)
    if abs(float(verified['input_i']) + 14) > .5 or float(verified['input_tp']) > -1.0:
        raise ValueError('Final audio failed loudness validation')
    return float(verified['input_i'])

def stamp(value):
    ms = round(value * 1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

def render_pack(inbox, output, files):
    if len(files) < 10: raise ValueError('Ten clips required')
    candidates = []
    seen = set()
    for f in files:
        raw_source = inbox / f['name']
        source = raw_source.resolve()
        if source.parent != inbox.resolve() or raw_source.is_symlink(): raise ValueError('Invalid source')
        with source.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if digest in seen: continue
        seen.add(digest)
        if probe(source)['duration'] < 10.5: continue
        start, end = builder.find_best_segment(str(source), want=10.5)
        candidates.append((score_segment(source, start), source, start, end, digest))
    if len(candidates) < 10: raise ValueError('Ten distinct eligible clips required')
    candidates.sort(key=lambda x: x[0], reverse=True)
    output.mkdir(parents=True, exist_ok=True)
    # All packaging occurs in a private staging directory; publish ZIP atomically.
    with tempfile.TemporaryDirectory(prefix='pack_', dir=output) as temp:
        work = Path(temp)
        package = work / 'package'
        package.mkdir()
        preset = builder.PRESETS['contractorPain']
        cards = builder.build_timeline(preset)
        if builder.validate_timeline(cards): raise ValueError('Invalid timeline')
        ass = builder.write_ass(cards, preset, work / 'cards.ass')
        whoosh = builder.make_whoosh(work / 'whoosh.wav')
        manifest = {'sandbox': True, 'product':'ai-reel-pack', 'audio':'Preset sound effects; no synthetic voiceover', 'reels': []}
        for i, (score, source, start, end, digest) in enumerate(candidates[:10], 1):
            name = f'reel-{i:02}'
            silent, mixed = work/'silent.mp4', work/'mixed.mp4'
            final = package / f'{name}.mp4'
            builder.build_video(str(source), start, ass, silent, 10, 1.05)
            builder.mux(silent, None, whoosh, cards, mixed, 10)
            measured_lufs = master_audio(mixed, final, work)
            info = probe(final)
            if abs(info['duration']-10) > 1/30 or (info['width'], info['height'], info['fps']) != (1080,1920,'30/1') or not info['audio']:
                raise ValueError('Output validation failed')
            tail = run(['ffmpeg','-v','error','-ss','7.6','-i',str(final),'-t','2.3','-vf','fps=4,scale=64:36','-f','framemd5','-']).stdout.decode()
            hashes = [line.rsplit(',',1)[-1].strip() for line in tail.splitlines() if not line.startswith('#')]
            if len(set(hashes)) < 3: raise ValueError('Tail is frozen')
            timeline = {'preset':preset.name,'cards':cards,'script':preset.script,'segment':{'start':start,'end':end},'output':{'width':1080,'height':1920,'fps':30,'text_seconds':7.5,'tail_seconds':2.5,'speed':1.05},'score':score}
            (package/f'{name}.timeline.json').write_text(json.dumps(timeline, indent=2))
            (package/f'{name}.srt').write_text('\n\n'.join(f"{j}\n{stamp(c['start'])} --> {stamp(c['end'])}\n" + c['text'].replace('\\N','\n') for j,c in enumerate(cards,1)))
            manifest['reels'].append({'file':final.name,'sha256':hashlib.file_digest(final.open('rb'),'sha256').hexdigest(),'source_sha256':digest,**info,'moving_tail':True,'integrated_lufs':measured_lufs})
        run(['ffmpeg','-v','error','-ss','1','-i',str(package/'reel-01.mp4'),'-frames:v','1',str(package/'thumbnail.jpg'),'-y'])
        (package/'manifest.json').write_text(json.dumps(manifest, indent=2))
        archive = work / 'pack.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as z:
            for p in sorted(package.iterdir()): z.write(p,p.name)
        archive.replace(output/'ai-reel-pack.zip')

if __name__ == '__main__':
    inbox, output, manifest = map(Path, sys.argv[1:])
    render_pack(inbox, output, json.loads(manifest.read_text()))
