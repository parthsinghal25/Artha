import hashlib,json
from pathlib import Path
import pytest


def test_manifest_hash_and_single_case_guard(tmp_path):
    from sdbackend.cloud.vertex_job import validate_input
    cid='170ac13c-0c12-46e7-ae6e-0630b8372d06'
    member='7704d1fd-6aa5-4c79-b795-a96bb11f5f95'
    d={'case_id':cid,'members':[member],'narrative':[],'crosswalk':{'rows':[]},'records':[]}
    (tmp_path/'case-inputs.json').write_text(json.dumps(d))
    manifest={'case_id':cid,'member':member,'files':{'case-inputs.json':hashlib.sha256((tmp_path/'case-inputs.json').read_bytes()).hexdigest()},'allowed_evidence_case_ids':[cid]}
    assert validate_input(tmp_path,manifest)['case_id']==cid
    (tmp_path/'case-inputs.json').write_text('{}')
    with pytest.raises(ValueError,match='hash'):
        validate_input(tmp_path,manifest)


def test_manifest_rejects_extra_case_and_unsafe_paths(tmp_path):
    from sdbackend.cloud.vertex_job import validate_input
    with pytest.raises(ValueError):
        validate_input(tmp_path,{'case_id':'other','files':{}})
    with pytest.raises(ValueError):
        validate_input(tmp_path,{'case_id':'170ac13c-0c12-46e7-ae6e-0630b8372d06','files':{'../escape':'x'}})


def test_real_packet_runs_existing_fenced_memory_phase_with_mock(tmp_path,monkeypatch):
    import shutil
    from types import SimpleNamespace
    from sdbackend.cloud.vertex_job import execute
    from sdbackend.models import gateway
    from sdbackend.intel import worker
    from intel_mock import MockProvider
    from sdbackend.canonical import canonical_sha256
    from test_intel_mechanics import _make_demo
    from sdbackend.intel import memory
    from sdbackend.cloud.vertex_job import CASE_ID, MEMBER, sha
    from dataclasses import asdict
    demo=_make_demo(tmp_path/'fixture-source',monkeypatch)
    ctx=worker.Context(demo['rt'],demo['corpus'],'2026-09-30')
    inp=ctx.inputs(CASE_ID)
    raw=asdict(inp);raw['knowledge_end']=inp.knowledge_end.isoformat()
    (tmp_path/'case-inputs.json').write_text(json.dumps(raw))
    shutil.copyfile(Path(__file__).resolve().parents[1]/'config/demo/policy.json',tmp_path/'policy.json')
    manifest={'case_id':CASE_ID,'member':MEMBER,'allowed_evidence_case_ids':[CASE_ID],
              'source_release_id':ctx.rv.release_id,'source_set_version':ctx.rv.control['source_set_version'],
              'input_digest':inp.input_digest(),
              'issue_subset':[{'issue_id':f'I{i}','definition_id':f'ID{i}','label':'Mock issue',
                               'level':'handling','definition':'mock definition','include_if':'supported',
                               'exclude_if':'unsupported'} for i in range(3)],
              'files':{n:sha((tmp_path/n).read_bytes()) for n in ['case-inputs.json','policy.json']}}
    prov=MockProvider()
    prov.budget=SimpleNamespace(receipt=None)
    monkeypatch.setattr(gateway,'make_provider',lambda *a:prov)
    monkeypatch.setattr(gateway.ModelGateway,'fingerprint',lambda s:{'provider':'mock','model':prov.model,'sha256':'f'*64})
    monkeypatch.setenv('SDB_NO_DOTENV','1')
    monkeypatch.setenv('SDB_VERTEX_PROJECT','project-test')
    writes={}
    class Objects:
        def write(self,name,body):
            assert name not in writes
            writes[name]=body
            return {'uri':'test://'+name,'sha256':canonical_sha256(body)}
    result=execute(tmp_path,manifest,Objects())
    assert result['state']=='complete',result
    assert result['claims']>0 and result['actual_attempts']>0
    assert 'case-memory.json' in writes and 'result.json' in writes
    assert result['issue_subset']['assessed']==3
    assert not (tmp_path/'runtime'/'publication.json').exists()
