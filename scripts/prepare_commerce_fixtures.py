"""Build ten small test uploads from actual local drone footage; originals untouched."""
import subprocess
from pathlib import Path
root=Path('/media/bfam/5C2B-86B2/DCIM/100MEDIA')
out=Path(__file__).resolve().parents[1]/'data/commerce-fixtures'
out.mkdir(parents=True,exist_ok=True)
files=[]
for p in sorted(root.glob('DJI_03*.MP4')):
 if p.name < 'DJI_0318.MP4': continue
 duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(p)]))
 if duration >= 18: files.append(p)
 if len(files)==10: break
for i,p in enumerate(files,1):
 dest=out/f'drone-{i:02}.mp4'

 subprocess.run(['ffmpeg','-v','error','-ss','5','-i',str(p),'-t','12','-vf','scale=640:-2','-r','30','-an','-c:v','libx264','-preset','fast','-crf','23',str(dest),'-y'],check=True,timeout=120)
 print(dest.name,flush=True)
