#!/usr/bin/env python3
"""Offline repository hygiene and immutable contract verification; no model calls."""
import ast
import hashlib
import json
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', 'dist', '__pycache__', '.pytest_cache', 'node_modules', 'var'}
def main():
    problems=[]; checked=0; python_files=0
    manifest=json.loads((ROOT/'Shared/contracts/v1/MANIFEST.json').read_text())
    for folder,entries in [('Shared/contracts/v1',manifest['files']),('Data/contracts',manifest['pinned_data_baseline'])]:
        for name,expected in entries.items():
            p=ROOT/folder/name
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
                problems.append({'path':str(p.relative_to(ROOT)), 'issue':'frozen_contract_mismatch'})
    body={k:v for k,v in manifest.items() if k!='manifest_content_sha256'}
    canonical=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    if hashlib.sha256(canonical).hexdigest()!=manifest['manifest_content_sha256']:
        problems.append({'path':'Shared/contracts/v1/MANIFEST.json','issue':'manifest_digest_mismatch'})
    patterns=[re.compile(r'AIza[A-Za-z0-9_-]{35}'),re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
    for p in ROOT.rglob('*'):
        if not p.is_file() or any(part in EXCLUDED or part.startswith('.venv') or part.startswith('dist-') for part in p.relative_to(ROOT).parts):continue
        checked+=1; rel=str(p.relative_to(ROOT))
        if p.is_symlink():problems.append({'path':rel,'issue':'symlink'})
        if p.name=='.env' or (p.name.startswith('.env.') and p.name!='.env.example'):
            problems.append({'path':rel,'issue':'environment_file'})
        try:text=p.read_text()
        except UnicodeError:continue
        if any(pattern.search(text) for pattern in patterns):problems.append({'path':rel,'issue':'credential_pattern_review_required'})
        if p.suffix=='.py':
            try:ast.parse(text,filename=rel);python_files+=1
            except SyntaxError:problems.append({'path':rel,'issue':'python_syntax'})
    result={'status':'pass' if not problems else 'fail','files_checked':checked,'python_files_parsed':python_files,'contract_files_verified':len(manifest['files'])+len(manifest['pinned_data_baseline']),'problems':problems,'scope':'offline hygiene and contract checks only; not a comprehensive security audit, runtime or model-quality acceptance'}
    print(json.dumps(result,indent=2));return int(bool(problems))
if __name__=='__main__':raise SystemExit(main())
