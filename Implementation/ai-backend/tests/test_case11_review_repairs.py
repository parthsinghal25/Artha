"""Focused resolver regressions; synthetic source text, never runtime answers."""
from types import SimpleNamespace
from sdbackend.canonical import sha256_text
from sdbackend.contracts import load_contracts
from sdbackend.engine.core import parse_instant
from sdbackend.pipeline.case_assess import materialise

CASE='C-REVIEW-SPEC'

def assess(final, texts):
    sources={sid:{'data':{'source_id':sid,'text':text,'content_sha256':sha256_text(text)},
                  'envelope':{'available_at':at,'occurred_at':at}} for sid,(text,at) in texts.items()}
    host=SimpleNamespace(repo=SimpleNamespace(source_record=sources.get,cutoff=parse_instant('2026-10-01T00:00:00+10:00')),
                         _passage=lambda h:{'source_id':h.split('~')[0],'start_codepoint':0,'text':sources[h.split('~')[0]]['data']['text']})
    return materialise(final,host,CASE,{s+'~P1' for s in sources},load_contracts(),'SSV-SPEC')

def cite(s,text): return {'passage_handle':s+'~P1','quote':text}

def test_receipt_without_target_or_assertion_cannot_confirm_service():
    text='The upload receipt lists DC-X as received. That tells us the files arrived, nothing more.'
    out=assess({'service':[{'target_key':None,'value':'confirmed_complete','reason':'target unresolved',
                           'assertion_citations':[],'confirmation_citations':[cite('SRC-R',text)],'contradictory_citations':[]}]},
               {'SRC-R':(text,'2026-01-12T16:00:00+11:00')})
    assert out['state_projection_candidate']['service'][0]['value']=='unknown'

def test_receipt_for_different_proposition_cannot_confirm_identified_action():
    a='The transfer was executed.'
    c='The upload receipt lists the documents as received.'
    out=assess({'targets':[{'target_key':'T','kind':'service_action','label':'transfer','citations':[cite('SRC-A',a)]}],
                'service':[{'target_key':'T','value':'confirmed_complete','reason':'',
                            'assertion_citations':[cite('SRC-A',a)],'confirmation_citations':[cite('SRC-C',c)],'contradictory_citations':[]}]},
               {'SRC-A':(a,'2026-01-12T16:00:00+11:00'),'SRC-C':(c,'2026-01-13T16:00:00+11:00')})
    assert out['state_projection_candidate']['service'][0]['value']!='confirmed_complete'

def test_explicit_closure_drives_status_history_and_episode():
    opening='Administrative case status: open.'
    closing='Administrative case status: closed, effective 2026-03-13. Underlying service remains unresolved.'
    out=assess({'administrative':{'value':'closed','reason':'','citations':[cite('SRC-C',closing)]},
                'administrative_history':[{'state':'open','date':'2025-12-31','citations':[cite('SRC-O',opening)]}]},
               {'SRC-O':(opening,'2025-12-31T16:00:00+11:00'),'SRC-C':(closing,'2026-03-13T16:00:00+11:00')})
    from sdbackend.engine.results import _state_at
    from sdbackend.engine.producers import episodes
    out['case_id']=CASE
    assert _state_at(out['administrative_transitions'],parse_instant('2026-10-01T00:00:00+10:00'))[0]=='closed'
    assert _state_at(out['administrative_transitions'],parse_instant('2026-03-01T00:00:00+11:00'))[0]=='open'
    assert out['state_projection_candidate']['administrative']['value']=='closed'
    assert episodes([out])[0][0]['data']['end_at_exclusive']=='2026-03-14T00:00:00+11:00'

def test_final_response_subject_does_not_establish_administrative_closure():
    text='Subject: Final response to your complaint'
    out=assess({'administrative':{'value':'closed','reason':'final response','citations':[cite('SRC-F',text)]},
                'administrative_history':[{'state':'closed','date':None,'citations':[cite('SRC-F',text)]}]},
               {'SRC-F':(text,'2026-01-21T16:00:00+11:00')})
    assert out['state_projection_candidate']['administrative']['value']=='unknown'
    assert out['administrative_transitions']==[]

def test_approval_condition_is_not_an_undertaking():
    from sdbackend.engine import producers
    from test_producers_e07 import _src, _fact, _groups
    text='Condition: The approval is conditional on a received certificate not automatically being accepted.'
    src=_src('SRC-COND',text,subtype='note')
    groups=_groups((src,'E',[_fact(src,'commitment',text)]))
    unresolved=[]
    assert producers.commitments(groups,{'SRC-COND':src},set(),unresolved)==[]
    assert any('undertaking' in r['reason'] for r in unresolved)

def test_same_document_unrelated_promise_does_not_validate_condition():
    from sdbackend.engine import producers
    from test_producers_e07 import _src, _fact, _groups
    condition='The approval is conditional on a received certificate not automatically being accepted.'
    src=_src('SRC-COND',condition+'\n\nI will send a separate update tomorrow.',subtype='note')
    f=_fact(src,'commitment',condition)
    f['data']['value']['evidence_refs'][0]['anchor']['end_codepoint']=len(condition)
    unresolved=[]
    assert producers.commitments(_groups((src,'E',[f])),{'SRC-COND':src},set(),unresolved)==[]

def test_receipt_of_same_item_does_not_confirm_its_assessment():
    a='DC-X has been assessed and accepted.'
    c='DC-X was received. This only confirms upload receipt.'
    out=assess({'targets':[{'target_key':'T','kind':'service_action','label':'DC-X assessment','citations':[cite('SRC-A',a)]}],
                'service':[{'target_key':'T','value':'confirmed_complete','reason':'',
                            'assertion_citations':[cite('SRC-A',a)],'confirmation_citations':[cite('SRC-C',c)],'contradictory_citations':[]}]},
               {'SRC-A':(a,'2026-01-12T16:00:00+11:00'),'SRC-C':(c,'2026-01-13T16:00:00+11:00')})
    assert out['state_projection_candidate']['service'][0]['value']!='confirmed_complete'

def test_future_assessment_words_are_not_confirmed_execution():
    a='DC-X has been assessed and accepted.'
    c='DC-X will be assessed and accepted if the missing witness signs.'
    out=assess({'targets':[{'target_key':'T','kind':'service_action','label':'DC-X assessment','citations':[cite('SRC-A',a)]}],
                'service':[{'target_key':'T','value':'confirmed_complete','reason':'',
                            'assertion_citations':[cite('SRC-A',a)],'confirmation_citations':[cite('SRC-C',c)],'contradictory_citations':[]}]},
               {'SRC-A':(a,'2026-01-12T16:00:00+11:00'),'SRC-C':(c,'2026-01-13T16:00:00+11:00')})
    assert out['state_projection_candidate']['service'][0]['value']!='confirmed_complete'

def test_explicit_source_status_fields_form_temporal_history_without_model_invention():
    from sdbackend.pipeline.case_assess import source_administrative_transitions
    from test_producers_e07 import _src
    sources=[_src('SRC-OPEN','We have received your complaint, reference C-SPEC.',occurred='2025-12-31T10:00:00+11:00'),
             _src('SRC-CLOSE','Administrative case status: closed, effective 2026-03-13 at 9:55 am AEDT',occurred='2026-03-13T10:00:00+11:00'),
             _src('SRC-FINAL','Subject: Final response to your complaint',occurred='2026-01-21T10:00:00+11:00'),
             _src('SRC-EXT','Administrative case status: closed, effective 2026-02-01',author=None,role='external dispute correspondence')]
    transitions=source_administrative_transitions(sources)
    assert [(t['state'],t['effective_date']) for t in transitions]==[('open','2025-12-31'),('closed','2026-03-13')]
    assert {r['source_id'] for t in transitions for r in t['evidence_refs']}=={'SRC-OPEN','SRC-CLOSE'}

def test_proposal_cannot_survive_as_supported_approval_timeline():
    text='On money: I propose goodwill compensation. It would need its own approval.'
    out=assess({'timeline':[{'event_type':'approval','proposition':'Remedy approved','date':None,'state':'supported',
                             'citations':[cite('SRC-P',text)]}]},{'SRC-P':(text,'2026-01-12T16:00:00+11:00')})
    event=out['timeline_events'][0]
    assert event['event_type']=='other'
    assert event['proposition']==text

def test_explicit_effective_closure_instant_is_preserved_in_episode():
    from sdbackend.pipeline.case_assess import source_administrative_transitions
    from sdbackend.engine.producers import episodes
    from test_producers_e07 import _src
    sources=[_src('SRC-OPEN','We have received your complaint.',at='2025-12-31T16:00:00+11:00',occurred='2025-12-31T10:00:00+11:00'),
             _src('SRC-CLOSE','Administrative case status: closed, effective 2026-03-13 at 9:55 am AEDT',
                  at='2026-03-13T16:00:00+11:00',occurred='2026-03-13T09:55:00+11:00')]
    transitions=source_administrative_transitions(sources)
    assert transitions[-1]['effective_at']=='2026-03-13T09:55:00+11:00'
    record=episodes([{'case_id':CASE,'administrative_transitions':transitions}])[0][0]
    assert record['data']['end_at_exclusive']=='2026-03-13T09:55:00+11:00'
    assert record['envelope']['available_at']=='2026-03-13T16:00:00+11:00'

def test_quoted_observation_uses_source_date_not_described_action_date():
    text='The action was proposed on 13 January 2026.'
    out=assess({'timeline':[{'event_type':'approval','proposition':'approved','date':'2026-01-13','state':'supported',
                             'citations':[cite('SRC-P',text)]}]},{'SRC-P':(text,'2026-02-01T16:00:00+11:00')})
    assert out['timeline_events'][0]['occurrence']=={'precision':'day','date':'2026-02-01'}
