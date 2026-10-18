#!/usr/bin/env python3
"""Reconcile saved actual Case 11 model output using current deterministic host rules. No inference/decisions."""
import argparse
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sdbackend.runtime import make_runtime
from sdbackend.canonical import canonical_sha256,derived_id,utc_now_iso
from sdbackend.pipeline.case_assess import RESOLVER_VERSION,reconcile_case
from sdbackend.pipeline.runner import resolve_release
from sdbackend.api.repo import Repo
from sdbackend.policy import pipeline_authority
from sdbackend.engine.resolvers import resolve,native_candidates
from sdbackend.engine.publish import prepare_closure
from sdbackend.pipeline.control import record_dependencies

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True)
args=parser.parse_args();rt=make_runtime();rv=resolve_release(rt,'current')
tasks={t['task_kind']:t['output_object_id'] for t in rt.db.connect().execute(
    "select task_kind,output_object_id from tasks where run_id=? and state='staged' and task_kind not like 's02%'",(args.run,))}
old=rt.db.get_object('stage_s03',tasks['s03_case_assess']);case=old['case_id']
cutoff=old['state_projection_candidate']['assessed_evidence_as_of']
repo=Repo(rt,rv,pipeline_authority(),cutoff,'inclusive_cutoff')
sources=[repo.source_record(sid) for sid in repo.case_source_ids(case)]
new=reconcile_case(old,sources,case,cutoff)
new['stage_id']=derived_id('STAGE','s03-host-reconciliation',old['stage_id'],RESOLVER_VERSION,
                            canonical_sha256(new['host_reconciliation']))
new['host_reconciliation'].update(parent_model_stage_id=old['stage_id'],parent_model_stage_sha256=rt.db.object_sha('stage_s03',old['stage_id']),
                                code_sha256=__import__('hashlib').sha256(Path('sdbackend/pipeline/case_assess.py').read_bytes()).hexdigest())
new['produced_at']=utc_now_iso();new['draft_status']='staged_not_accepted'
existing=rt.db.get_object('stage_s03',new['stage_id'])
if existing:new=existing
else:
    rt.contracts.check('state_projection',new['state_projection_candidate'])
    for e in new['timeline_events']:rt.contracts.check('timeline_event',e)
    rt.db.put_object('stage_s03',new['stage_id'],new,links={'case':[case],'run':[args.run]})
record_dependencies(rt,'stage_s03',new['stage_id'],
                    [('stage_s03',old['stage_id'],rt.db.object_sha('stage_s03',old['stage_id']),None)] +
                    [('source',s['data']['source_id'],rt.db.object_sha('source',s['data']['source_id']),None) for s in sources])
old4=rt.db.get_object('stage_s04',tasks['s04_ledger_resolve'])
inputs=[e['immutable_id'] for e in rv.release['entries'] if e['kind']=='input']
source_ids=[i for i in inputs if rt.db.get_object('source',i)]
s4=resolve(rt,old4['consumed_s02_stages'],native_source_ids=native_candidates(rt,inputs),
           release_source_ids=source_ids,s03_stage_ids=[new['stage_id']],run_id=args.run)
from sdbackend.engine.resolvers import dependency_edges
record_dependencies(rt,'stage_s04',s4['stage_id'],dependency_edges(s4))
# S05's original model provenance and immutable consumed-stage dependency stay intact for independent review.
s5=rt.db.get_object('stage_s05',tasks['s05_collective'])
stages=[new['stage_id'],s4['stage_id'],s5['stage_id']]
prepared=prepare_closure(rt,release_id=rv.release_id,assessed_evidence_as_of=cutoff,stage_ids=stages)
report={'kind':'case11_corrected_stage_subjects','model_run_id':args.run,'no_new_inference':True,
 'release_id':rv.release_id,'source_set_version':rv.control['source_set_version'],
 'stages':[{'subject_kind':k,'subject_id':s,'subject_sha256':rt.db.object_sha(k,s)} for k,s in zip(('stage_s03','stage_s04','stage_s05'),stages)],
 'retained_s05_provenance':{'consumed_s03_stages':s5['consumed_s03_stages'],'note':'Original model stage kept unchanged; its finding cites unchanged source evidence. No successor model call or dependency relabelling is claimed.'},
 'closure':prepared}
Path('reports/integration/CORRECTED-SUBJECTS.json').write_text(json.dumps(report,indent=2)+'\n')
for k,s in zip(('stage_s03','stage_s04','stage_s05'),stages):
 Path('reports/integration/'+k+'-corrected.json').write_text(json.dumps(rt.db.get_object(k,s),indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('stages','release_id','source_set_version')},indent=2))
