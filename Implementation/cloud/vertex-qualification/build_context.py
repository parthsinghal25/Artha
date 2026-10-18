#!/usr/bin/env python3
"""Build a separate allowlisted qualification context, never the serving context."""
import hashlib,json,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent
BACKEND=HERE.parents[1]/'ai-backend'
SD=HERE.parents[2]
OUT=HERE/'dist'
assert not OUT.exists(),'context already exists; preserve it and use an explicit versioned output'
OUT.mkdir()
for p in sorted((BACKEND/'sdbackend').rglob('*')):
 if p.is_file() and p.suffix in ('.py','.json') and '__pycache__' not in p.parts:
  q=OUT/p.relative_to(BACKEND);q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
manifest=json.loads((SD/'Shared/contracts/v1/MANIFEST.json').read_text())
for group,folder,entries in [('shared',SD/'Shared/contracts/v1',['MANIFEST.json',*manifest['files']]),('data',SD/'Data/contracts',manifest['pinned_data_baseline'])]:
 for name in entries:
  q=OUT/'contracts'/group/name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(folder/name,q)
for name in ['Dockerfile','requirements.lock']:shutil.copyfile(HERE/name,OUT/name)
files={p.relative_to(OUT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.rglob('*')) if p.is_file()}
digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
info={'component':'artha-vertex-onecase-worker','context_sha256':digest,'files':files,
      'image':'us-central1-docker.pkg.dev/project-c2a6c144-4e0d-440b-918/superdashboard/artha-vertex-qual:'+digest[:12]}
(OUT/'BUILD-INFO.json').write_text(json.dumps(info,indent=2,sort_keys=True))
print(json.dumps({'context':str(OUT),'files':len(files),'image':info['image'],'sha256':digest},indent=2))
