#!/usr/bin/env python3
"""Export one case's held inputs from a read-only snapshot; no inference, private answers or source writes."""
import argparse,dataclasses,hashlib,json,os,sqlite3,sys,tempfile,shutil
from pathlib import Path
CLOUD=Path(__file__).resolve().parents[1]
BACKEND=CLOUD.parent/'ai-backend'
sys.path.insert(0,str(BACKEND))
os.environ['SDB_NO_DOTENV']='1'
os.environ['SDB_POLICY_PATH']=str(BACKEND/'config/demo/policy.json')
os.environ['SDB_PUBLICATION_POLICY']='synthetic-demo-v1'
os.environ['SDB_DEMO_CORPUS']=str(BACKEND/'config/demo/p4-pilot-v1.corpus.json')
from sdbackend.cloud.vertex_job import CASE_ID,MEMBER,sha,validate_input
from sdbackend.config import load_settings
from sdbackend.runtime import make_runtime
from sdbackend.intel.corpus import load_corpus
from sdbackend.intel import worker,memory
from sdbackend.canonical import canonical_sha256

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
 out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
 source=BACKEND/'var/demo/p4-pilot-v1'
 corpus=load_corpus(BACKEND/'config/demo/p4-pilot-v1.corpus.json')
 assert MEMBER in corpus.eligible_members and MEMBER not in corpus.early_evaluation_members
 with tempfile.TemporaryDirectory(prefix='artha-qual-read-') as td:
  snapshot=Path(td)/'control.sqlite3'
  original=sqlite3.connect('file:'+str(source/'control.sqlite3')+'?mode=ro',uri=True)
  dest=sqlite3.connect(snapshot);original.backup(dest);dest.close();original.close()
  rt=make_runtime(dataclasses.replace(load_settings(),data_dir=Path(td)))
  ctx=worker.Context(rt,corpus,'2026-09-30')
  assert CASE_ID in ctx.eligible and ctx.members[CASE_ID]==[MEMBER]
  inp=ctx.inputs(CASE_ID)
  safe={c for c in ctx.eligible if not set(ctx.members[c]) & set(corpus.early_evaluation_members)}
  source_ids={s['data']['source_id'] for s in inp.narrative}|set(inp.table_sources)
  rows=[r for r in inp.crosswalk['rows'] if set(r['source_ids'])&source_ids or r in inp.withheld_failed]
  evidence_cases={c for r in rows for c in r['case_ids']}
  if not evidence_cases<=safe:
   print('Unsafe shared rows:',[(r['semantic_type'],r['original_id'],r['case_ids']) for r in rows if not set(r['case_ids'])<=safe])
   raise ValueError('shared evidence includes holdout/reserved cases')
  actors={a for r in rows for a in [r.get('author'),*r.get('recipients',[]),*r.get('cc',[])] if a}
  inp.crosswalk={k:v for k,v in inp.crosswalk.items() if k not in ('rows','participants')}
  inp.crosswalk.update(rows=rows,participants={a:inp.participants[a] for a in actors if a in inp.participants})
  inp.participants=inp.crosswalk['participants']
  inp.sidecar={s:v for s,v in inp.sidecar.items() if s in source_ids}
  d=dataclasses.asdict(inp);d['knowledge_end']=inp.knowledge_end.isoformat()
  (out/'case-inputs.json').write_text(json.dumps(d,ensure_ascii=False,sort_keys=True))
  shutil.copyfile(BACKEND/'config/demo/policy.json',out/'policy.json')
  assets=[]
  for row in rows:
   aid=row.get('asset_version_id')
   assert aid, 'missing original asset'
   asset=rt.db.get_object('asset',aid)
   blob=rt.db.connect().execute('select rel_path from blobs where sha256=?',(asset['bytes_sha256'],)).fetchone()
   assert blob
   raw=(source/'blobs'/blob['rel_path']).read_bytes()
   assert sha(raw)==asset['bytes_sha256']==row['original_sha256']
   name='originals/'+asset['bytes_sha256']+'.bin'
   (out/'originals').mkdir(exist_ok=True);(out/name).write_bytes(raw)
   extractions=[rt.db.get_object('extraction',eid) for eid in asset['extraction_ids']]
   for ext in extractions:
    assert ext['bytes_sha256']==asset['bytes_sha256']
   assets.append({'path':name,'asset':asset,'extractions':extractions,'crosswalk':row})
  (out/'original-extraction-provenance.json').write_text(json.dumps(assets,ensure_ascii=False,sort_keys=True))
  # Definitions only; never import prior case memories or previous membership judgements.
  fixed=sqlite3.connect('file:'+str(BACKEND/'var/demo/fixed500-agy-v1/control.sqlite3')+'?mode=ro',uri=True)
  reg=json.loads(fixed.execute("select body from objects where kind='issue_registry' order by created_at desc limit 1").fetchone()[0])
  from sdbackend.intel.discovery import active_issues
  issues=sorted(active_issues(reg),key=lambda x:x['issue_id'])[:3]
  fixed.close()
  m={'qualification_version':'vertex-onecase-v1','case_id':CASE_ID,'member':MEMBER,'view_date':'2026-09-30',
     'source_release_id':ctx.rv.release_id,'source_set_version':ctx.rv.control['source_set_version'],
     'source_delivery_manifest_sha256':corpus.body['delivery_manifest_sha256'],
     'input_digest':inp.input_digest(),'allowed_evidence_case_ids':sorted(evidence_cases|{CASE_ID}),
     'association_note':'Shared originals retain runtime case associations; only the named case receives inference.',
     'extraction':'existing validated cached text; no Linux OCR claim','narrative_sources':len(inp.narrative),
     'originals':len(assets),'source_quality_basis':corpus.body['source_quality_basis'],
     'reserved_cases_included':False,'issue_subset':issues,'issue_subset_registry_sha256':canonical_sha256(reg),
     'files':{p.relative_to(out).as_posix():sha(p.read_bytes()) for p in sorted(out.rglob('*')) if p.is_file()}}
  validate_input(out,m)
  (out/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2,sort_keys=True))
  print(json.dumps({'case_id':CASE_ID,'narrative_sources':len(inp.narrative),'originals':len(assets),
                    'manifest_sha256':sha((out/'manifest.json').read_bytes()),'estimated_model_calls':len(memory.packets(inp,inp.narrative))+2,
                    'out':str(out)},indent=2))
if __name__=='__main__':main()
