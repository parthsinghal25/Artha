from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sdbackend.models.base import GenRequest, Message, Part, ProviderUnavailable, ToolResult


def test_vertex_adc_native_schema_and_no_hidden_retries(monkeypatch, tmp_path):
    import google.auth
    from google import genai
    from google.genai import types
    from sdbackend.models.vertex import VertexProvider
    credentials = object()
    monkeypatch.setattr(google.auth, 'default', lambda **kw: (credentials, 'ignored-project'))
    captured = {}
    native = types.Content(role='model', parts=[types.Part(text='{"ok":true}', thought_signature=b'opaque')])
    client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=SimpleNamespace(
        candidates=[SimpleNamespace(content=native, finish_reason='STOP')], usage_metadata=None, model_version='real-revision'))))
    def make(**kw):
        captured.update(kw)
        return client
    monkeypatch.setattr(genai, 'Client', make)
    p = VertexProvider('project-test', 'global', 'gemini-3.8-flash', budget_path=tmp_path/'budget.json')
    req = GenRequest('test', 'system', [Message('user',[Part.t('hi')])], response_schema={'type':'object'}, max_output_tokens=8000)
    res = p.generate(req)
    assert captured['vertexai'] is True and captured['credentials'] is credentials
    assert captured['project'] == 'project-test' and 'api_key' not in captured
    assert captured['http_options'].retry_options.attempts == 1
    assert captured['http_options'].timeout == 60000
    assert res.provider == 'vertex-ai' and res.model_version == 'real-revision'
    assert res.native_message.native is native and res.data == {'ok': True}
    sent = client.models.generate_content.call_args.kwargs
    assert sent['config'].max_output_tokens == 4096
    assert sent['config'].automatic_continuation is False
    assert sent['config'].automatic_function_calling.disable is True
    converted = p._contents([res.native_message, Message('tool', tool_results=[ToolResult('opaque-id','read',{'ok':True})]),
                             Message('user',[Part('image', data=b'png', mime='image/png')])])
    assert converted[0] is native
    assert converted[1].parts[0].function_response.id == 'opaque-id'
    assert converted[2].parts[0].inline_data.data == b'png'


def test_budget_survives_restart_and_reserves_before_send(tmp_path):
    from sdbackend.models.vertex import QualificationBudget
    req = GenRequest('test','system',[Message('user',[Part.t('hello')])])
    path = tmp_path/'b.json'
    receipts = []
    b = QualificationBudget(path, attempts=1, input_tokens=160000, receipt=lambda x:receipts.append(x))
    b.reserve(req)
    with pytest.raises(ProviderUnavailable):
        QualificationBudget(path, attempts=1).reserve(req)
    assert len(receipts)==1 and receipts[0]['state']=='reserved'


def test_budget_rejects_media_and_large_text_before_dispatch(tmp_path):
    from sdbackend.models.vertex import QualificationBudget
    b = QualificationBudget(tmp_path/'b.json', input_tokens=10)
    with pytest.raises(ProviderUnavailable):
        b.reserve(GenRequest('x','system',[Message('user',[Part.t('x'*100)])]))
    with pytest.raises(ProviderUnavailable):
        b.reserve(GenRequest('x','system',[Message('user',[Part('image',data=b'png',mime='image/png')])]))
    assert not (tmp_path/'b.json').exists()


def test_explicit_vertex_never_uses_developer_key(monkeypatch, tmp_path):
    from sdbackend.config import load_settings
    from sdbackend.models.gateway import choose_provider, make_provider
    monkeypatch.setenv('SDB_NO_DOTENV','1')
    monkeypatch.setenv('SDB_VERTEX_PROJECT','project-test')
    monkeypatch.setenv('SDB_VERTEX_LOCATION','global')
    monkeypatch.setenv('SDB_VERTEX_MODEL','gemini-3.8-flash')
    monkeypatch.delenv('GOOGLE_API_KEY',raising=False)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    settings=replace(load_settings(),data_dir=tmp_path)
    assert choose_provider(settings,'vertex')[0]=='vertex'
    with pytest.raises(ProviderUnavailable):
        choose_provider(replace(settings,vertex_project=''),'vertex')


def test_vertex_runspec_discloses_credit_funded_underlying_cost(rt):
    from sdbackend.pipeline.control import seal_runspec
    run=seal_runspec(rt,purpose='qualification',release_id='qualification-source',source_set_version='qualification-source-set',
        boundary={},worklist=['one-case'],provider={'provider':'vertex-ai','model':'gemini-3.8-flash'},
        versions={},budget={'attempts':12},parent_run_id=None,selection_reason='explicit qualification')
    ceiling=run['monetary_ceiling']
    assert ceiling['currency']=='USD' and ceiling['amount_minor']==200
    assert 'not a billing cap' in ceiling['basis']
