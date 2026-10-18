#!/usr/bin/env python3
"""Real corrected-stage handler probes on an isolated store copy with labelled specimen diagnostic decisions.
Never imports specimens into the served store. No model, intake, login-code access, or UI claim.
"""
import json,os,shutil,sqlite3,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
report_dir=ROOT/'reports/integration';subjects=json.loads((report_dir/'CORRECTED-SUBJECTS.json').read_text())
check=Path(tempfile.mkdtemp(prefix='case11-corrected-specimen-',dir='/private/tmp'))
(check/'data').mkdir();shutil.copytree(ROOT/'var/data/blobs',check/'data/blobs')
src=sqlite3.connect(ROOT/'var/data/control.sqlite3');dest=sqlite3.connect(check/'data/control.sqlite3');src.backup(dest);dest.close();src.close()
shutil.copy(ROOT/'config/policy.json',check/'policy.json')
registry=json.loads((ROOT/'config/reviewers.json').read_text());registry['reviewers']['specimen-reviewer']={'display_name':'ISOLATED SPECIMEN ONLY','specimen':True,'gates':['diagnostic_evaluation','result_integrity']};(check/'reviewers.json').write_text(json.dumps(registry))
os.environ.update(SDB_DATA_DIR=str(check/'data'),SDB_POLICY_PATH=str(check/'policy.json'),SDB_REVIEWER_REGISTRY=str(check/'reviewers.json'),SDB_ALLOW_SPECIMEN_ACCEPTANCE='1')
os.environ.pop('SDB_ALLOW_UNREVIEWED_SOURCES',None)
from sdbackend.runtime import make_runtime
from sdbackend.engine.publish import prepare_closure,publish_analysis_set
from sdbackend.api.ops import Operations
from specimens import decide
from test_ingest_and_api import FILTERS
rt=make_runtime();p=prepare_closure(rt,release_id=subjects['release_id'],assessed_evidence_as_of='2026-10-01T00:00:00+10:00',stage_ids=[s['subject_id'] for s in subjects['stages']])
for req in p['required_decisions']:
 if req['gate'] in ('diagnostic_evaluation','result_integrity'):
  decide(rt,req['gate'],[{k:req[k] for k in ('subject_kind','subject_id','subject_sha256')}])
aset=publish_analysis_set(rt,candidate_id=p['candidate_id'],notes=['ISOLATED SPECIMEN HANDLER CHECK, not real publication'])
ops=Operations(rt);case='C-e1da28538652e263887a'
def context(at,pop=None):
 r=ops.handle('leader.demo',dict(operation='context.resolve',preferred_view_id=None,release_id=subjects['release_id'],evidence_as_of=at,analysis_mode='reconstructed',analysis_as_of=None,population_reference=pop));assert r['status']=='ok',r;return r['context']
def query(ctx,m,registry='runtime-measures-0.2.0',version='0.2.0',pop=None,**f):
 r=ops.handle('leader.demo',dict(operation='analysis.query',context_id=ctx['context_id'],query_id='CORRECTED-SPECIMEN',definition=dict(registry_id=registry,measure_id=m,definition_version=version),filters={**FILTERS,'granularity':'none',**f},population_reference=pop));assert r['status']=='ok',r;return r
out={'isolated':True,'specimen_decisions':True,'no_served_mutation':True,'store':str(check),'actual_model_run':subjects['model_run_id'],'subjects':subjects['stages'],'analysis_set':aset,'queries':{},'case_by_cutoff':{}}
for label,at in [('final','2026-10-01T00:00:00+10:00'),('march1','2026-03-01T00:00:00+11:00'),('before_work','2026-03-27T23:00:00+11:00'),('after_work','2026-03-27T23:30:00+11:00')]:
 ctx=context(at);qs={}
 for m in ('cases.open','cash.remedies_paid','cash.expenses_paid','cash.expenses_and_remedies_paid','work.recorded_hours','obligations.outstanding'):
  qs[m]=query(ctx,m)
  cell=qs[m]['payload']['cells'][0]
  qs[m+'_contributors']=ops.handle('leader.demo',dict(operation='contributors.list',context_id=ctx['context_id'],result_id=qs[m]['payload']['result_id'],cell_key=cell['key'],cursor=None,page_size=100))
 out['queries'][label]=qs
 out['case_by_cutoff'][label]=ops.handle('leader.demo',dict(operation='case.get',context_id=ctx['context_id'],case_id=case,cursor=None,page_size=100))
ctx=context('2026-10-01T00:00:00+10:00');out['case']=out['case_by_cutoff']['final']
out['mx_member']=query(ctx,'mx.commitments.follow_through','shared-measures-1.0.0','1.0.0')
parent=out['queries']['final']['cash.remedies_paid'];cell=parent['payload']['cells'][0]
pop={'result_id':parent['payload']['result_id'],'cell_key':cell['key'],'population_id':cell['population_id'],'predicate_sha256':cell['population']['predicate_sha256']}
child=context('2026-10-01T00:00:00+10:00',pop);out['payment_requery']=query(child,'cash.remedies_paid',pop=pop)
(report_dir/'ACTUAL-CORRECTED-PROBES.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'store':str(check),'analysis_set':aset['analysis_set_version'],'case_status':out['case'].get('status'),'queries':{k:{m:v['payload']['cells'][0]['point'] for m,v in qs.items() if not m.endswith('_contributors')} for k,qs in out['queries'].items()}}))
