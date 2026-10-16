import os,re,json,subprocess,hashlib
from pathlib import Path
import gdown
fid=os.environ['FILE_ID'];job=os.environ['JOB_ID'];repo=os.environ['GH_REPO']
if not re.fullmatch(r'[A-Za-z0-9_-]{10,100}',fid) or not re.fullmatch(r'[A-Za-z0-9_-]{8,160}',job):raise ValueError('Invalid source or job ID')
tag='crp-mf-prep-'+job
# Completed extraction is reused; no overwriting or public release.
releases=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/releases?per_page=100']))
existing=next((r for r in releases if r['tag_name']==tag),None)
if existing:
 names={a['name'] for a in existing['assets']}
 if {'source.mp3','source-manifest.json'}<=names:
  print('Reusing verified extraction');raise SystemExit(0)
 raise RuntimeError('Incomplete prior extraction; inspect before retrying')
asset_url=os.environ.get('SOURCE_ASSET_URL','')
if asset_url:
 if not re.fullmatch(r'https://api\.github\.com/repos/'+re.escape(repo)+r'/releases/assets/[0-9]+',asset_url):raise ValueError('Unexpected private source URL')
 with open('source-video.mp4','wb') as out:
  subprocess.run(['gh','api',asset_url,'-H','Accept: application/octet-stream'],stdout=out,check=True)
else:
 gdown.download(id=fid,output='source-video.mp4',quiet=True)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json','source-video.mp4']))
duration=float(probe['format']['duration'])
if not 10<=duration<=1800 or not any(x['codec_type']=='audio' for x in probe['streams']):raise ValueError('Unsupported source duration or missing audio')
subprocess.run(['ffmpeg','-v','error','-i','source-video.mp4','-map','0:a:0','-vn','-ac','1','-ar','24000','-b:a','64k','source.mp3'],check=True)
b=Path('source.mp3').read_bytes()
if not 10000<len(b)<24000000:raise ValueError('Unexpected audio size')
Path('source-manifest.json').write_text(json.dumps({'sourceDriveId':fid,'jobId':job,'durationSeconds':duration,'audioSha256':hashlib.sha256(b).hexdigest(),'sourceSha256':hashlib.file_digest(open('source-video.mp4','rb'),'sha256').hexdigest(),'bytes':len(b)},indent=2))
subprocess.run(['gh','release','create',tag,'source.mp3','source-manifest.json','--draft','--title',job,'--notes','Private intermediate MF audio extraction. Not a podcast publication.'],check=True)

