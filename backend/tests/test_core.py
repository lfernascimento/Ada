import asyncio
import sqlite3
import pytest
from pydantic import ValidationError
from app.models import AgentSpec
from app import db, repo
from app.model_gateway import ModelGateway, ModelResult, generate_structured, StructuredOutputError

@pytest.fixture
def spec():
    return AgentSpec.model_validate(dict(name='Nina',purpose='Pesquisar',instructions=['Ser clara'],tools=['ler_url','salvar_relatorio'],autonomy_policy={'ler_url':'allowed','salvar_relatorio':'approval_required'},limits={'max_steps':6},style={'color':'sage'}))
@pytest.fixture(autouse=True)
def database(tmp_path,monkeypatch):
    from app import security,config
    monkeypatch.setattr(config,"MEMORY_AUTOMATIC",False)
    monkeypatch.setattr(security,'TRUSTED_HOSTS',security.TRUSTED_HOSTS|{'testserver'})
    monkeypatch.setattr(security,'TRUSTED_PEERS',security.TRUSTED_PEERS|{'testclient'})
    monkeypatch.setattr(db,'DB_BACKEND','sqlite'); monkeypatch.setattr(db,'DB_PATH',tmp_path/'test.db'); db.init_db()
def test_spec_security(spec):
    d=spec.model_dump(); d['autonomy_policy']['salvar_relatorio']='allowed'
    with pytest.raises(ValidationError): AgentSpec.model_validate(d)
    d['autonomy_policy']={}
    with pytest.raises(ValidationError): AgentSpec.model_validate(d)
def test_versions_rollback_diff(spec):
    id=repo.create_agent(spec); newer=spec.model_copy(update={'name':'Lia'})
    n=repo.add_version(id,newer,'Mudança','teste'); repo.activate_version(id,n)
    assert repo.get_active_spec(id).name=='Lia'
    assert repo.diff_specs(spec,newer)==[{'field':'name','before':'Nina','after':'Lia'}]
    with db.connect() as c:
        with pytest.raises(sqlite3.IntegrityError): c.execute('UPDATE agent_versions SET reason=? WHERE agent_id=?',('x',id))
    repo.activate_version(id,1); assert repo.get_active_spec(id).name=='Nina'
class FakeGateway(ModelGateway):
    def __init__(self,responses): self.responses=iter(responses)
    async def chat(self,messages,**kwargs): return ModelResult(next(self.responses),0,0,False)
def test_structured_retry(spec):
    result=asyncio.run(generate_structured(FakeGateway(['invalid',spec.model_dump_json()]),[],AgentSpec)); assert result==spec
    with pytest.raises(StructuredOutputError): asyncio.run(generate_structured(FakeGateway(['x','y','z']),[],AgentSpec))
def test_fake_stream():
    async def run(): return ''.join([chunk async for chunk in FakeGateway(['abc']).chat_stream([])])
    assert asyncio.run(run())=='abc'

def test_create_and_chat_endpoints(spec,monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    import json
    proposal=json.dumps({'type':'proposal','text':None,'spec':spec.model_dump(),'rationale':'Adequado ao objetivo.'})
    monkeypatch.setattr(main,'gateway',FakeGateway([proposal,'Resposta do agente.']))
    with TestClient(main.app) as client:
        response=client.post('/create/messages',json={'messages':[{'role':'user','content':'Quero pesquisar.'}]})
        assert response.status_code==200, response.text
        agent=client.post('/agents',json=response.json()['spec']); assert agent.status_code==200
        aid=agent.json()['id']; cid=client.post(f'/agents/{aid}/conversations').json()['id']
        response=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Explique seu objetivo.'})
        assert response.json()=={'text':'Resposta do agente.'}
        assert len(client.get(f'/agents/{aid}/conversations/{cid}/messages').json())==2
        assert client.get(f'/agents/{aid+1}/conversations/{cid}/messages').status_code==404


def test_delegation_plan_and_catalog(spec):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        rid=client.post(f'/agents/{aid}/runs',json={'conversation_id':cid}).json()['id']
        task={'name':'Revisor','objective':'Revisar o texto'}
        plan={'objective':'Preparar uma resposta','subagents':[task,task]}
        response=client.post(f'/runs/{rid}/delegation-plan',json=plan)
        assert response.status_code==200 and response.json()['execution_started'] is False
        assert client.get(f'/runs/{rid}').json()['events'][-1]['type']=='delegation_planned'
        assert client.post(f'/runs/{rid}/delegation-plan',json={**plan,'subagents':[task]*3}).status_code==422
        assert client.post(f'/runs/{rid}/delegation-plan',json={**plan,'max_budget_usd':2}).status_code==422
        assert len(client.get('/cookbooks').json())==3
        assert client.get('/orchestration/capabilities').json()['execution'] is True
        client.post(f'/runs/{rid}/cancel')
        assert client.post(f'/runs/{rid}/delegation-plan',json=plan).status_code==409


def test_claude_sdk_gateway_in_chat_flow(spec, monkeypatch):
    mock_sdk_client_for_query(monkeypatch)
    import json
    import claude_agent_sdk
    from fastapi.testclient import TestClient
    from app import main, config
    from app.model_gateway import ClaudeAgentGateway
    from claude_agent_sdk import ResultMessage
    calls=[]
    proposal=json.dumps({'type':'proposal','text':None,'spec':spec.model_dump(),'rationale':'Adequado.'})
    async def fake_query(*, prompt, options):
        calls.append((prompt,options))
        text = proposal if len(calls)==1 else ('{\"tasks\":[{\"capability\":\"code_review\",\"assignment\":\"Revisar trecho enviado\"}]}' if len(calls)==2 else 'Resposta pelo SDK.')
        yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='test',result=text,usage={'input_tokens':10,'output_tokens':5},total_cost_usd=0.001)
    monkeypatch.setattr(claude_agent_sdk,'query',fake_query)
    monkeypatch.setattr(config,'MODEL_API_KEY','test-key')
    monkeypatch.setattr(config,'MODEL_NAME','test-model')
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway())
    with TestClient(main.app) as client:
        result=client.post('/create/messages',json={'messages':[{'role':'user','content':'Crie um agente'}]})
        assert result.status_code==200, result.text
        aid=client.post('/agents',json=result.json()['spec']).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        result=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Oi'})
        assert result.json()=={'text':'Resposta pelo SDK.'}
        assert len(client.get('/cookbooks/source-files').json())==4
        runs=client.get(f'/agents/{aid}/runs').json()
        assert runs[0]['status']=='completed' and runs[0]['steps']==2
        assert abs(runs[0]['cost_estimate']-0.002)<0.000001
        events=client.get(f"/runs/{runs[0]['id']}").json()['events']
        assert [event['type'] for event in events] == ['started','plan','mcp_enabled','native_execution','completed']
        assert client.get(f'/agents/{aid}/conversations').json()[0]['title']=='Oi'
    assert all(opts.tools==[] and opts.allowed_tools==[] and opts.setting_sources==[] for _,opts in calls[:2])
    assert calls[2][1].tools==['Agent','WebSearch'] and 'code_review_1' in calls[2][1].agents
    assert set(calls[2][1].agents['code_review_1'].tools)=={'mcp__ada__buscar_documentos','mcp__ada__ler_pagina','mcp__ada__consultar_notas','mcp__ada__consultar_historico'}
    assert all(opts.max_budget_usd>0 for _,opts in calls)
    assert 'Crie um agente' in calls[0][0] and 'Oi' in calls[1][0] and len(calls)==3

def test_claude_sdk_error_is_not_success(monkeypatch):
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from app.model_gateway import ClaudeAgentGateway
    from app import config
    monkeypatch.setattr(config,'MODEL_API_KEY','test-key'); monkeypatch.setattr(config,'MODEL_NAME','test-model')
    async def fake_query(*, prompt, options):
        yield ResultMessage(subtype='error_max_budget_usd',duration_ms=1,duration_api_ms=1,is_error=True,num_turns=1,session_id='test',result='erro')
    monkeypatch.setattr(claude_agent_sdk,'query',fake_query)
    with pytest.raises(RuntimeError):
        asyncio.run(ClaudeAgentGateway().chat([{'role':'user','content':'Oi'}]))


def test_shared_budget_stops_before_next_call(spec,monkeypatch):
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from fastapi.testclient import TestClient
    from app import main, config
    from app.model_gateway import ClaudeAgentGateway
    calls=[]
    async def fake_query(*,prompt,options):
        calls.append(options.max_budget_usd)
        yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='t',result='{\"tasks\":[]}',total_cost_usd=0.05,usage={})
    monkeypatch.setattr(claude_agent_sdk,'query',fake_query)
    monkeypatch.setattr(config,'MODEL_API_KEY','test'); monkeypatch.setattr(config,'MODEL_NAME','test')
    monkeypatch.setattr(config,'TASK_BUDGET_USD',0.05)
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway())
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        r=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Teste'})
        assert r.status_code==502 and len(calls)==1
        runs=client.get(f'/agents/{aid}/runs').json()
        assert runs[0]['status']=='failed' and runs[0]['cost_estimate']==0.05
        assert client.get(f'/agents/{aid}/conversations/{cid}/messages').json()==[]


def test_product_research_documents_and_exports(spec,monkeypatch):
    mock_sdk_client_for_query(monkeypatch)
    import json
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from fastapi.testclient import TestClient
    from app import main,config
    from app.model_gateway import ClaudeAgentGateway
    outputs=iter([json.dumps({'tasks':[{'capability':'research','assignment':'Busque referências'}]}),'## Proposta\nFazer MVP.'])
    calls=[]
    async def fake_query(*,prompt,options):
        calls.append((prompt,options))
        yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='test',result=next(outputs),usage={'input_tokens':10,'output_tokens':5},total_cost_usd=0.001)
    monkeypatch.setattr(claude_agent_sdk,'query',fake_query)
    monkeypatch.setattr(config,'MODEL_API_KEY','test');monkeypatch.setattr(config,'MODEL_NAME','test')
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway())
    d=spec.model_dump(); d['tools']+=['pesquisar_web','consultar_documentos'];d['autonomy_policy'].update(pesquisar_web='allowed',consultar_documentos='allowed')
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=d).json()['id']
        up=client.post(f'/agents/{aid}/documents?name=briefing.txt',content=b'O objetivo do cliente e reduzir abandono no checkout.',headers={'content-type':'application/octet-stream'})
        assert up.status_code==200,up.text
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        response=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Use o briefing e pesquise ideias.'})
        assert response.status_code==200,response.text
        assert 'checkout' in calls[0][0]
        assert calls[1][1].tools==['Agent','WebSearch'] and set(calls[1][1].agents['research_1'].tools)=={'WebSearch','mcp__ada__buscar_documentos','mcp__ada__ler_pagina','mcp__ada__consultar_notas','mcp__ada__consultar_historico'}
        assert calls[1][1].hooks['PreToolUse'][0].on_failure=='block'
        hook=calls[1][1].hooks['PreToolUse'][0].hooks[0]
        async def check_hook():
            results=[await hook({'tool_name':'WebSearch','hook_event_name':'PreToolUse'},None,None) for _ in range(4)]
            assert results[-1]['hookSpecificOutput']['permissionDecision']=='deny'
        asyncio.run(check_hook())
        assert calls[0][1].tools==[] and len(calls)==2
        for kind,signature in [('md',b'## Proposta'),('docx',b'PK'),('pdf',b'%PDF'),('pptx',b'PK'),('xlsx',b'PK')]:
            exported=client.get(f'/agents/{aid}/conversations/{cid}/export?format={kind}')
            assert exported.status_code==200 and exported.content.startswith(signature)
        assert client.get(f'/agents/{aid+1}/conversations/{cid}/export?format=md').status_code==404


def test_session_rename_and_fork(spec):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        with db.connect() as c: c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(cid,'user','Briefing'),(cid,'assistant','Proposta')])
        assert client.patch(f'/agents/{aid}/conversations/{cid}',json={'title':'Roadmap'}).json()['title']=='Roadmap'
        fork=client.post(f'/agents/{aid}/conversations/{cid}/fork').json()['id']
        assert len(client.get(f'/agents/{aid}/conversations/{fork}/messages').json())==2
        titles=[c['title'] for c in client.get(f'/agents/{aid}/conversations').json()]
        assert titles==['Roadmap','Cópia da conversa']
        assert client.post(f'/agents/{aid+1}/conversations/{cid}/fork').status_code==404


def test_oauth_return_to_frontend(spec,monkeypatch):
    from urllib.parse import urlsplit, parse_qs
    from fastapi.testclient import TestClient
    from app import connections
    from app.main import app
    monkeypatch.setenv('GITHUB_CLIENT_ID','test-id');monkeypatch.setenv('GITHUB_CLIENT_SECRET','test-secret')
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {'access_token':'test-token','expires_in':300}
    class FakeAsyncClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): return FakeResponse()
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        invalid=client.post('/connections/github/start',json={'frontend_origin':'https://example.com'})
        assert invalid.status_code==400
        mismatch=client.post('/connections/github/start',json={'frontend_origin':'http://127.0.0.1:5174'},headers={'Origin':'http://127.0.0.1:5173'})
        assert mismatch.status_code==400
        result=client.post('/connections/github/start',json={'frontend_origin':'http://127.0.0.1:5174'},headers={'Origin':'http://127.0.0.1:5174'})
        assert result.status_code==200
        state=parse_qs(urlsplit(result.json()['url']).query)['state'][0]
        monkeypatch.setattr(connections.httpx,'AsyncClient',FakeAsyncClient)
        response=client.get('/connections/github/callback',params={'state':state,'code':'test-code'},headers={'sec-fetch-site':'cross-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'},follow_redirects=False)
        assert response.status_code==303
        assert response.headers['location']=='http://127.0.0.1:5174/?connection=github&status=connected'
        assert 'test-code' not in response.headers['location']
        assert response.headers['referrer-policy']=='no-referrer'
        assert next(x for x in client.get('/connections').json() if x['id']=='github')['connected']


def test_public_repository_review_and_approved_pr(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from app.main import app
    from app import repository,connections
    base='a'*40; tree='b'*40; result_tree='c'*40; result_commit='d'*40
    calls=[]
    async def fake_gh(method,path,token,**kwargs):
        calls.append((method,path,kwargs.get('json')))
        if path=='/repos/owner/demo': return {'private':False,'default_branch':'main','permissions':{'push':True}}
        if path.endswith('/git/ref/heads/main'): return {'object':{'sha':base}}
        if '/contents/' in path: return {'type':'file','encoding':'base64','size':6,'content':base64.b64encode(b'hello\n').decode()}
        if path.endswith('/git/commits/'+base): return {'tree':{'sha':tree}}
        if path.endswith('/git/trees/'+tree): return {'tree':[{'path':'app.py','type':'blob','mode':'100755','sha':'e'*40}],'truncated':False}
        if method=='POST' and path.endswith('/git/trees'): return {'sha':result_tree}
        if method=='POST' and path.endswith('/git/commits'): return {'sha':result_commit}
        if method=='POST' and path.endswith('/git/refs'): return {'ref':'ok'}
        if method=='POST' and path.endswith('/pulls'): return {'html_url':'https://github.com/owner/demo/pull/1','number':1}
        raise AssertionError((method,path))
    monkeypatch.setattr(repository,'gh',fake_gh)
    monkeypatch.setitem(connections._tokens,'github',{'access_token':'example','expires_at':None,'scopes':'read:user'})
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        response=client.get('/repository/file',params={'repository':'https://github.com/owner/demo','path':'app.py'})
        assert response.status_code==200 and response.json()['content']=='hello\n'
        payload={'repository':'owner/demo','path':'app.py','title':'Melhorar tela','content':'  novo\n','description':'Mudança'}
        draft=client.post('/repository/drafts',json=payload).json()
        assert '-hello' in draft['diff'] and '+  novo' in draft['diff']
        assert not any(method=='POST' for method,_,_ in calls)
        assert client.post('/repository/drafts/'+draft['id']+'/publish').status_code==403
        connections._tokens['github']['scopes']='public_repo,read:user'
        response=client.post('/repository/drafts/'+draft['id']+'/publish')
        assert response.status_code==200 and response.json()['number']==1
        tree_payload=next(data for method,path,data in calls if method=='POST' and path.endswith('/git/trees'))
        assert tree_payload['tree'][0]['mode']=='100755' and tree_payload['tree'][0]['content']=='  novo\n'
        assert client.post('/repository/drafts/'+draft['id']+'/publish').status_code==409
        assert client.get('/repository/file',params={'repository':'evil.example/x','path':'app.py'}).status_code==422
        assert client.get('/repository/file',params={'repository':'owner/demo','path':'../secrets'}).status_code==422

def test_repository_detects_base_changes(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import repository,connections
    monkeypatch.setitem(connections._tokens,'github',{'access_token':'example','expires_at':None,'scopes':'public_repo'})
    count=0
    async def base(repo,token):
        nonlocal count
        count+=1
        return 'owner/demo','main',('a' if count==1 else 'b')*40,True
    async def file(*args): return 'old\n','100644'
    monkeypatch.setattr(repository,'base_info',base);monkeypatch.setattr(repository,'read_file',file)
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        draft=client.post('/repository/drafts',json={'repository':'owner/demo','path':'app.py','title':'Alteração','content':'new\n'}).json()
        assert client.post('/repository/drafts/'+draft['id']+'/publish').status_code==409


def test_repository_model_suggestion_is_review_only(monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app.main import app
    from app import repository,connections,main
    monkeypatch.setitem(connections._tokens,'github',{'access_token':'example','expires_at':None,'scopes':'read:user'})
    async def base(repo,token): return 'owner/demo','main','a'*40,True
    async def file(*args): return 'antigo\n','100644'
    monkeypatch.setattr(repository,'base_info',base);monkeypatch.setattr(repository,'read_file',file)
    monkeypatch.setattr(main,'gateway',FakeGateway([json.dumps({'content':'novo\n','summary':'Melhoria visual.'})]))
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        result=client.post('/repository/suggest',json={'repository':'owner/demo','path':'app.py','request':'Melhore a interface','title':'Melhorar tela'})
        assert result.status_code==200,result.text
        draft=result.json(); assert draft['content']=='novo\n' and '+novo' in draft['diff']
        assert draft['summary']=='Melhoria visual.' and repository._drafts[draft['id']]['status']=='ready'
        assert client.post('/repository/drafts/'+draft['id']+'/publish').status_code==403


def test_repository_does_not_retry_uncertain_publication(monkeypatch):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from app.main import app
    from app import repository,connections
    monkeypatch.setitem(connections._tokens,'github',{'access_token':'example','expires_at':None,'scopes':'public_repo'})
    async def base(repo,token): return 'owner/demo','main','a'*40,True
    async def file(*args): return 'old\n','100644'
    calls=[]
    async def gh(method,path,token,**kwargs):
        calls.append((method,path))
        if path.endswith('/git/commits/'+'a'*40): return {'tree':{'sha':'b'*40}}
        if path.endswith('/git/trees'): return {'sha':'c'*40}
        if path.endswith('/git/commits'): return {'sha':'d'*40}
        if path.endswith('/git/refs'): return {}
        if path.endswith('/pulls'): raise HTTPException(502,'timeout')
        raise AssertionError(path)
    monkeypatch.setattr(repository,'base_info',base);monkeypatch.setattr(repository,'read_file',file);monkeypatch.setattr(repository,'gh',gh)
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        draft=client.post('/repository/drafts',json={'repository':'owner/demo','path':'app.py','title':'Melhorar','content':'new\n'}).json()
        result=client.post('/repository/drafts/'+draft['id']+'/publish')
        assert result.status_code==502 and 'branch ada/proposta-' in result.json()['detail']
        before=len(calls)
        assert client.post('/repository/drafts/'+draft['id']+'/publish').status_code==409
        assert len(calls)==before


def test_google_separate_oauth_scopes_and_shared_callback(monkeypatch):
    from urllib.parse import urlsplit,parse_qs
    from fastapi.testclient import TestClient
    from app.main import app
    from app import connections
    monkeypatch.setenv('GOOGLE_CLIENT_ID','test-google-id');monkeypatch.setenv('GOOGLE_CLIENT_SECRET','test-secret')
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {'access_token':'docs-token','expires_in':60,'scope':'https://www.googleapis.com/auth/documents.readonly'}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):
            assert kwargs['data']['redirect_uri']=='http://127.0.0.1:8000/connections/drive/callback'
            assert kwargs['data'].get('code_verifier')
            return Resp()
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        for service in ('docs','sheets','gmail','calendar','classroom'):
            start=client.post('/connections/'+service+'/start',json={'frontend_origin':'http://127.0.0.1:5174'})
            assert start.status_code==200
            q=parse_qs(urlsplit(start.json()['url']).query)
            assert q['redirect_uri']==['http://127.0.0.1:8000/connections/drive/callback']
            assert q['scope']==[connections.PROVIDERS[service]['scope']]
        state=q['state'][0]
        monkeypatch.setattr(connections.httpx,'AsyncClient',FakeClient)
        r=client.get('/connections/drive/callback',params={'state':state,'code':'test'},headers={'sec-fetch-site':'cross-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'},follow_redirects=False)
        assert r.status_code==303 and r.headers['location'].endswith('?connection=classroom&status=connected')
        assert connections._tokens['classroom']['access_token']=='docs-token'

def test_google_workspace_read_only_and_explicit_context(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import connections,google_workspace
    for service in ('docs','sheets','gmail','calendar','classroom','drive'):
        monkeypatch.setitem(connections._tokens,service,{'access_token':'test','expires_at':None})
    calls=[]
    class R:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,params=None):
            calls.append((url,params))
            if 'docs.googleapis.com' in url:return R({'title':'Briefing','body':{'content':[{'paragraph':{'elements':[{'textRun':{'content':'Objetivo do produto'}}]}}]}})
            if 'sheets.googleapis.com' in url:return R({'range':'A1:B2','values':[['Métrica','Valor'],['Ativos','10']]})
            if url.endswith('/messages'):return R({'messages':[{'id':'123'}]})
            if '/messages/' in url:return R({'payload':{'headers':[{'name':'Subject','value':'Assunto'}]}})
            if 'calendar' in url:return R({'items':[{'summary':'Reunião','start':{'date':'2026-11-17'}}]})
            if 'classroom' in url:return R({'courses':[{'id':'1','name':'Curso'}]})
            return R({'files':[{'id':'1','name':'Documento'}]})
    monkeypatch.setattr(google_workspace.httpx,'AsyncClient',Client)
    with TestClient(app,base_url='http://127.0.0.1:8000') as c:
        r=c.get('/workspace/docs/browse',params={'id':'https://docs.google.com/document/d/abc123/edit'})
        assert r.status_code==200 and r.json()['text']=='Objetivo do produto' and r.headers['cache-control']=='no-store'
        assert c.get('/workspace/docs/browse',params={'id':'https://evil.example/d/abc'}).status_code==422
        assert c.get('/workspace/sheets/browse',params={'id':'abc','range':'A1:B2'}).json()['values'][1][0]=='Ativos'
        assert c.get('/workspace/sheets/browse',params={'id':'abc','range':'A1:Z999999'}).status_code==422
        assert c.get('/workspace/gmail/browse').json()['items'][0]['headers']['Subject']=='Assunto'
        assert all('format' not in (params or {}) or params['format']=='metadata' for _,params in calls)
        assert c.get('/workspace/calendar/browse').json()['items'][0]['summary']=='Reunião'
        assert c.get('/workspace/classroom/browse').json()['items'][0]['name']=='Curso'
        assert c.get('/workspace/drive/browse').json()['items'][0]['name']=='Documento'


def test_mcp_only_enables_authorized_tools(monkeypatch):
    from app.mcp_tools import build_mcp
    s,n=build_mcp(7,{'mcp_documentos'})
    assert n==['mcp__ada__buscar_documentos'] and s['ada']['type']=='sdk'
    s,n=build_mcp(7,{'mcp_paginas'})
    assert n==['mcp__ada__ler_pagina']
    assert build_mcp(7,set())==({},[])

def test_sdk_uses_mcp_client_when_explicitly_enabled(monkeypatch):
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from app import config
    from app.model_gateway import ClaudeAgentGateway,active_mcp
    seen=[]
    class FakeClient:
        def __init__(self,options): seen.append(options)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def query(self,prompt):pass
        async def receive_response(self):
            yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='test',result='Resposta',total_cost_usd=0.001,usage={})
    monkeypatch.setattr(claude_agent_sdk,'ClaudeSDKClient',FakeClient)
    monkeypatch.setattr(config,'MODEL_API_KEY','test');monkeypatch.setattr(config,'MODEL_NAME','test')
    async def run():
        token=active_mcp.set((7,{'mcp_documentos'}))
        try:return await ClaudeAgentGateway().chat([{'role':'user','content':'Oi'}])
        finally:active_mcp.reset(token)
    assert asyncio.run(run()).text=='Resposta'
    assert 'ada' in seen[0].mcp_servers and seen[0].allowed_tools==['mcp__ada__buscar_documentos']
    assert seen[0].tools==[] and seen[0].permission_mode=='dontAsk'

def test_sandbox_manual_execution_requires_explicit_post(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import sandbox_api
    calls=[]
    monkeypatch.setattr(sandbox_api,'inspect_docker',lambda:{'docker':True,'engine':True,'image':True,'message':'Docker disponível.'})
    def fake_run(args):calls.append(args.codigo);return {'saida':'ok','codigo_saida':0}
    monkeypatch.setattr(sandbox_api,'execute_python',fake_run)
    with TestClient(app,base_url='http://127.0.0.1:8000') as c:
        assert c.get('/sandbox/status').json()['image'] and calls==[]
        assert c.post('/sandbox/execute',json={'codigo':'print(1)'}).json()['saida']=='ok'
        assert calls==['print(1)']
        assert c.post('/sandbox/execute',json={'codigo':''}).status_code==422


def test_mcp_protocol_document_search_is_scoped(spec):
    import anyio,json
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from app.mcp_tools import build_mcp
    d=spec.model_dump();d['tools'].append('mcp_documentos');d['autonomy_policy']['mcp_documentos']='allowed'
    aid=repo.create_agent(type(spec).model_validate(d));other=repo.create_agent(spec)
    with db.connect() as c:
        doc=db.insert_id(c,'INSERT INTO documents(agent_id,name) VALUES(?,?)',(aid,'briefing.txt'))
        c.execute('INSERT INTO chunks(document_id,agent_id,position,content) VALUES(?,?,?,?)',(doc,aid,0,'Produto busca reduzir abandono no checkout.'))
        other_doc=db.insert_id(c,'INSERT INTO documents(agent_id,name) VALUES(?,?)',(other,'privado.txt'))
        c.execute('INSERT INTO chunks(document_id,agent_id,position,content) VALUES(?,?,?,?)',(other_doc,other,0,'Segredo: checkout alternativo.'))
    server=build_mcp(aid,{'mcp_documentos'})[0]['ada']['instance']
    async def run():
        async with create_client_server_memory_streams() as (client_streams,server_streams):
            async with anyio.create_task_group() as tg:
                tg.start_soon(server.run,server_streams[0],server_streams[1],server.create_initialization_options())
                async with ClientSession(client_streams[0],client_streams[1]) as client:
                    await client.initialize()
                    names=[x.name for x in (await client.list_tools()).tools]
                    assert names==['buscar_documentos']
                    result=await client.call_tool('buscar_documentos',{'pergunta':'checkout'})
                    data=json.loads(result.content[0].text)
                    assert len(data['trechos'])==1 and data['trechos'][0]['name']=='briefing.txt'
                tg.cancel_scope.cancel()
    anyio.run(run)


def test_workspace_creation_is_explicit_and_scope_checked(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import connections,google_workspace
    import base64
    calls=[]
    class R:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append((url,kwargs))
            if 'documents' in url:return R({'documentId':'document123'})
            if 'spreadsheets' in url:return R({'spreadsheetId':'sheet123'})
            if '/drafts' in url:return R({'id':'draft123'})
            if 'calendar' in url:return R({'id':'event123','summary':'Evento'})
            if 'classroom' in url:return R({'id':'course123','name':'Curso'})
            return R({'id':'file123','name':'brief.txt'})
    monkeypatch.setattr(google_workspace.httpx,'AsyncClient',Client)
    for service in ('drive','docs','sheets','gmail','calendar','classroom'):
        monkeypatch.setitem(connections._tokens,service,{'access_token':'test','expires_at':None,'scopes':''})
    with TestClient(app,base_url='http://127.0.0.1:8000') as c:
        assert c.post('/workspace/docs/create',json={'title':'Doc','content':'Texto'}).status_code==403
        assert calls==[]
        for service in ('drive','docs','sheets','gmail','calendar','classroom'):
            connections._tokens[service]['scopes']=connections.GOOGLE_WRITE_SCOPES[service]
        d=c.post('/workspace/drive/upload?name=brief.txt',content=b'briefing',headers={'content-type':'application/octet-stream'})
        assert d.status_code==200 and d.json()['id']=='file123'
        assert b'briefing' in calls[-1][1]['content']
        doc=c.post('/workspace/docs/create',json={'title':'Doc','content':'Texto'})
        assert doc.status_code==200 and doc.json()['id']=='document123'
        assert calls[-1][0].endswith(':batchUpdate')
        sh=c.post('/workspace/sheets/create',json={'title':'Plano','content':'Nome\tValor\nA\t10'})
        assert sh.status_code==200 and sh.json()['id']=='sheet123'
        cells=calls[-1][1]['json']['sheets'][0]['data'][0]['rowData']
        assert cells[0]['values'][0]['userEnteredValue']['stringValue']=='Nome'
        draft=c.post('/workspace/gmail/create',json={'title':'Assunto','recipient':'ada@example.com','content':'Mensagem'})
        assert draft.status_code==200 and draft.json()['status']=='rascunho'
        assert b'Mensagem' in base64.urlsafe_b64decode(calls[-1][1]['json']['message']['raw'])
        assert all('/messages/send' not in u for u,_ in calls)
        invalid=c.post('/workspace/calendar/create',json={'title':'Evento','start':'2026-11-17T10:00:00','end':'2026-11-17T11:00:00'})
        assert invalid.status_code==422
        event=c.post('/workspace/calendar/create',json={'title':'Evento','start':'2026-11-17T10:00:00-03:00','end':'2026-11-17T11:00:00-03:00'})
        assert event.status_code==200 and calls[-1][1]['params']['sendUpdates']=='none'
        course=c.post('/workspace/classroom/create',json={'title':'Curso'})
        assert course.status_code==200 and calls[-1][1]['json']['courseState']=='PROVISIONED'


def test_figma_render_checks_ids_and_returns_preview(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import connections
    monkeypatch.setitem(connections._tokens,'figma',{'access_token':'test','expires_at':None})
    class R:
        def raise_for_status(self):pass
        def json(self):return {'images':{'12:34':'https://example.com/preview.png'}}
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,params,headers):
            assert url=='https://api.figma.com/v1/images/abc123'
            assert params['ids']=='12:34' and params['format']=='png'
            return R()
    monkeypatch.setattr(connections.httpx,'AsyncClient',Client)
    with TestClient(app,base_url='http://127.0.0.1:8000') as c:
        r=c.get('/connections/figma/files/abc123/render',params={'node_id':'12-34'})
        assert r.status_code==200 and r.json()['image_url']=='https://example.com/preview.png'
        assert c.get('/connections/figma/files/abc123/render',params={'node_id':'../../bad'}).status_code==422

def test_google_write_authorization_is_opt_in(monkeypatch):
    from urllib.parse import urlsplit,parse_qs
    from fastapi.testclient import TestClient
    from app import connections
    from app.main import app
    monkeypatch.setenv('GOOGLE_CLIENT_ID','test-id');monkeypatch.setenv('GOOGLE_CLIENT_SECRET','test-secret')
    with TestClient(app,base_url='http://127.0.0.1:8000') as c:
        for name in ('docs','sheets','gmail','calendar','classroom'):
            read=c.post(f'/connections/{name}/start',json={}).json()
            write=c.post(f'/connections/{name}/start',json={'google_write':True}).json()
            rs=parse_qs(urlsplit(read['url']).query)['scope'][0].split()
            ws=parse_qs(urlsplit(write['url']).query)['scope'][0].split()
            assert connections.PROVIDERS[name]['scope'] in rs
            assert connections.GOOGLE_WRITE_SCOPES[name] not in rs
            assert connections.GOOGLE_WRITE_SCOPES[name] in ws
def test_product_code_preparation_and_unread_file_guard(spec,monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app import main, repository, code_workflow
    sha='a'*40
    monkeypatch.setattr(repository,'session',lambda: {'access_token':'test'})
    async def info(*args): return ('user/project','main',sha,True)
    async def tree(*args): return ({'app.py','large.py'},['app.py'])
    async def read(*args): return ('print("old")\n','100644')
    monkeypatch.setattr(repository,'base_info',info)
    monkeypatch.setattr(code_workflow,'tree_candidates',tree)
    monkeypatch.setattr(repository,'read_file',read)
    async def archive(*args): return b'example'
    monkeypatch.setattr(code_workflow,'download_archive',archive)
    class FakeWorkspace:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def validate(self,files):return {'status':'passed','message':'Sintaxe Python verificada.'}
    monkeypatch.setattr(code_workflow,'open_workspace',lambda archive:FakeWorkspace())
    proposal={'summary':'Alteração preparada','title':'Atualizar app','files':[{'path':'app.py','content':'print("new")\n'},{'path':'new.py','content':'print(1)\n'}]}
    monkeypatch.setattr(main,'gateway',FakeGateway([json.dumps({'paths':['app.py']}),json.dumps(proposal)]))
    from app.connections import local_only
    monkeypatch.setitem(main.app.dependency_overrides,local_only,lambda: None)
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        route=f'/agents/{aid}/conversations/{cid}/code'
        response=client.post(route,json={'repository':'user/project','task':'Atualizar aplicativo'})
        assert response.status_code==200,response.text
        result=response.json(); assert result['files']==['app.py','new.py']
        assert '-print("old")' in result['diff'] and '+print("new")' in result['diff']
        assert [x['path'] for x in repository._drafts[result['draft_id']]['files']]==['app.py','new.py']
        messages=client.get(f'/agents/{aid}/conversations/{cid}/messages').json()
        assert len(messages)==2 and 'app.py' in messages[-1]['content']
        proposal['files']=[{'path':'large.py','content':'substituir'}]
        monkeypatch.setattr(main,'gateway',FakeGateway([json.dumps({'paths':['app.py']}),json.dumps(proposal)]))
        assert client.post(route,json={'repository':'user/project','task':'Atualizar aplicativo'}).status_code==502
        assert len(client.get(f'/agents/{aid}/conversations/{cid}/messages').json())==2

def test_workspace_container_restrictions(monkeypatch):
    monkeypatch.setenv('ADA_SANDBOX_BACKEND','docker')
    import json
    from types import SimpleNamespace
    from app.tools import workspace_sandbox
    commands=[]
    monkeypatch.setattr(workspace_sandbox.shutil,'which',lambda _: 'docker')
    def fake_run(cmd,**kwargs):
        commands.append((cmd,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'passed','message':'ok','checks':[]}).encode())
    monkeypatch.setattr(workspace_sandbox.subprocess,'run',fake_run)
    result=workspace_sandbox.validate_workspace([{'path':'app.py','content':'x=1'}],b'archive')
    assert result['status']=='passed'
    cmd=commands[0][0]
    assert '--network=none' in cmd and '--read-only' in cmd and '--cap-drop=ALL' in cmd
    assert '--security-opt=no-new-privileges' in cmd and '--pull=never' in cmd
    assert not any('docker.sock' in part for part in cmd)
    assert commands[-1][0][1:3]==['rm','-f']

def test_workspace_archive_path_validation(tmp_path,monkeypatch):
    import importlib.util,io,tarfile,types,sys
    from pathlib import Path
    monkeypatch.setitem(sys.modules,'resource',types.SimpleNamespace())
    path=Path(__file__).resolve().parents[2]/'sandbox'/'runner.py'
    spec=importlib.util.spec_from_file_location('ada_sandbox_runner',path)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    runner.ROOT=tmp_path/'workspace';runner.ROOT.mkdir()
    def archive(name,content=b'test'):
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
            entry=tarfile.TarInfo(name);entry.size=len(content);tar.addfile(entry,io.BytesIO(content))
        return buffer.getvalue()
    runner.unpack(archive('root/src/app.py',b'print(1)'))
    assert (runner.ROOT/'src'/'app.py').read_bytes()==b'print(1)'
    with pytest.raises(ValueError):runner.unpack(archive('root/../../escape'))
    assert not (tmp_path/'escape').exists()

def test_multipass_workspace_lifecycle(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.tools import workspace_sandbox
    commands=[]
    monkeypatch.setenv('ADA_SANDBOX_BACKEND','multipass')
    monkeypatch.setattr(workspace_sandbox.shutil,'which',lambda _: 'multipass')
    def fake_run(cmd,**kwargs):
        commands.append((cmd,kwargs))
        result={'status':'passed','message':'ok','checks':[]}
        return SimpleNamespace(returncode=0,stdout=json.dumps(result).encode())
    monkeypatch.setattr(workspace_sandbox.subprocess,'run',fake_run)
    assert workspace_sandbox.validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')['status']=='passed'
    launch=commands[0][0];name=launch[launch.index('--name')+1]
    assert name.startswith('ada-task-') and '--cloud-init' in launch
    assert any('iptables' in c[0] for c in commands)
    assert any('ip6tables' in c[0] for c in commands)
    assert any('ada-sandbox' in c[0] and 'python3' in c[0] for c in commands)
    assert commands[-1][0]==['multipass','delete','--purge',name]
    transfer=next(c[0] for c in commands if len(c[0])>1 and c[0][1]=='transfer')
    from pathlib import Path
    assert transfer[3].startswith(name+':/tmp/')
    assert not Path(transfer[2]).exists()

def test_multipass_failed_firewall_does_not_run_code(monkeypatch):
    from types import SimpleNamespace
    from app.tools import workspace_sandbox
    commands=[]
    monkeypatch.setenv('ADA_SANDBOX_BACKEND','multipass')
    monkeypatch.setattr(workspace_sandbox.shutil,'which',lambda _: 'multipass')
    def fake_run(cmd,**kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=1 if 'iptables' in cmd else 0,stdout=b'')
    monkeypatch.setattr(workspace_sandbox.subprocess,'run',fake_run)
    assert workspace_sandbox.validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')['status']=='unavailable'
    assert not any('python3' in cmd and 'ada-sandbox' in cmd for cmd in commands)
    assert commands[-1][1:3]==['delete','--purge']

def test_code_agent_corrects_failed_validation(spec,monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app import main,repository,code_workflow
    from app.connections import local_only
    events=[]
    monkeypatch.setitem(main.app.dependency_overrides,local_only,lambda:None)
    monkeypatch.setattr(repository,'session',lambda:{'access_token':'example'})
    async def info(*args):return ('user/project','main','a'*40,True)
    async def tree(*args):return ({'app.py'},['app.py'])
    async def read(*args):return ('x=0\n','100644')
    async def archive(*args):return b'archive'
    monkeypatch.setattr(repository,'base_info',info);monkeypatch.setattr(repository,'read_file',read)
    monkeypatch.setattr(code_workflow,'tree_candidates',tree);monkeypatch.setattr(code_workflow,'download_archive',archive)
    class Workspace:
        def __enter__(self):events.append('open');return self
        def __exit__(self,*args):events.append('close')
        def validate(self,files):
            events.append(files[0]['content'])
            if files[0]['content']=='x=\n':return {'status':'failed','message':'Erro de sintaxe','checks':[{'command':'python','status':'failed','output':'SyntaxError'}]}
            return {'status':'passed','message':'Sintaxe passou','checks':[]}
    monkeypatch.setattr(code_workflow,'open_workspace',lambda _:Workspace())
    def proposal(code):return json.dumps({'summary':'Atualizado','title':'Atualizar','files':[{'path':'app.py','content':code}]})
    monkeypatch.setattr(main,'gateway',FakeGateway([json.dumps({'paths':['app.py']}),proposal('x=\n'),proposal('x=1\n')]))
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        response=client.post(f'/agents/{aid}/conversations/{cid}/code',json={'repository':'user/project','task':'Altere x'})
        assert response.status_code==200,response.text
        result=response.json()
        assert result['attempts']==2 and result['validation']['status']=='passed'
        assert events==['open','x=\n','x=1\n','close']
        assert repository._drafts[result['draft_id']]['files'][0]['content']=='x=1\n'

def test_failed_code_proposal_cannot_be_published(monkeypatch):
    import asyncio,time
    from app import repository
    from fastapi import HTTPException
    key='failed-check-test'
    monkeypatch.setitem(repository._drafts,key,{'status':'ready','validation_status':'failed','created':time.time()})
    monkeypatch.setitem(repository._locks,key,asyncio.Lock())
    with pytest.raises(HTTPException) as exc:asyncio.run(repository.publish(key))
    assert exc.value.status_code==409


def test_multipass_cloud_init_warning_checks_tools_and_reports(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.tools import workspace_sandbox
    commands=[]
    monkeypatch.setenv('ADA_SANDBOX_BACKEND','multipass')
    monkeypatch.setattr(workspace_sandbox.shutil,'which',lambda _: 'multipass')
    def fake_run(cmd,**kwargs):
        commands.append(cmd)
        if 'cloud-init' in cmd:return SimpleNamespace(returncode=2,stdout=b'status: done\n')
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'passed','message':'ok','checks':[]}).encode())
    monkeypatch.setattr(workspace_sandbox.subprocess,'run',fake_run)
    result=workspace_sandbox.validate_workspace([{'path':'x.py','content':'x=1'}],b'archive')
    assert result['status']=='passed' and result['warnings']
    assert any('node' in c for c in commands) and any('npm' in c for c in commands)
    assert any('ip6tables' in c for c in commands)
def test_global_boundary_blocks_untrusted_host_and_origin(spec):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app,base_url='http://evil.example') as c:
        assert c.get('/agents').status_code==403
        assert c.post('/agents',json=spec.model_dump()).status_code==403
    with TestClient(app,base_url='http://127.0.0.1:8000') as c:
        assert c.post('/agents',json=spec.model_dump(),headers={'Origin':'https://evil.example'}).status_code==403
        assert c.get('/agents',headers={'sec-fetch-site':'cross-site'}).status_code==403

def test_atomic_step_reservation(spec,monkeypatch):
    from app import infrastructure
    from fastapi import HTTPException
    import time
    limited=spec.model_copy(update={'tools':['consultar_documentos'],'autonomy_policy':{'consultar_documentos':'allowed'},'limits':spec.limits.model_copy(update={'max_steps':1})})
    aid=repo.create_agent(limited)
    with db.connect() as c:
        cid=db.insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
        rid=db.insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'draft')",(aid,cid))
    calls=[]
    def search(*args):calls.append(1);time.sleep(0.05);return {'trechos':[]}
    monkeypatch.setattr(infrastructure,'search_documents',search)
    async def invoke():return await asyncio.gather(*[infrastructure.request_tool(rid,infrastructure.ToolRequest(tool='consultar_documentos',arguments={'pergunta':'test'})) for _ in range(2)],return_exceptions=True)
    results=asyncio.run(invoke())
    assert len(calls)==1 and sum(isinstance(r,HTTPException) and r.status_code==409 for r in results)==1

def test_notes_scope_crud_and_recall(spec):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.notes import memory_context,add_note
    with TestClient(app) as c:
        a=c.post('/agents',json=spec.model_dump()).json()['id'];b=c.post('/agents',json=spec.model_dump()).json()['id']
        n=c.post(f'/agents/{a}/notes',json={'content':'Prefiro Inter.'}).json()['id']
        assert 'Inter' in memory_context(a) and 'Inter' not in memory_context(b)
        assert c.patch(f'/agents/{b}/notes/{n}',json={'content':'Não'}).status_code==404
        pending=add_note(a,'Prefiro verde.','agent')['id']
        assert 'verde' not in memory_context(a)
        assert c.post(f'/agents/{a}/notes/{pending}/activate').status_code==200
        assert 'verde' in memory_context(a)
        assert c.put(f'/agents/{a}/memory-settings',json={'enabled':False,'automatic':False}).status_code==200
        assert memory_context(a)==''
        assert c.delete(f'/agents/{a}/notes/{n}').status_code==200
        assert c.post(f'/agents/{a}/notes',json={'content':'sk-ant-'+('x'*30)}).status_code==422

def test_automatic_memory_evidence_recall_and_manual_protection(spec,monkeypatch):
    import json
    from app import config
    from app.notes import auto_remember,memory_context,add_note,list_notes
    monkeypatch.setattr(config,'MEMORY_AUTOMATIC',True)
    aid=repo.create_agent(spec)
    with db.connect() as c:cid=db.insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
    manual=add_note(aid,'Prefiro verde.')['id']
    response={'facts':[{'content':'Usa Inter.','evidence':'Prefiro Inter.','replace_id':None},{'content':'Memória inventada','evidence':'frase não dita','replace_id':None},{'content':'Alterar manual','evidence':'Prefiro Inter.','replace_id':manual}]}
    asyncio.run(auto_remember(FakeGateway([json.dumps(response)]),aid,cid,'Prefiro Inter.'))
    notes=list_notes(aid)
    assert len(notes)==2 and 'Usa Inter.' in memory_context(aid,'fonte')
    assert next(n for n in notes if n['id']==manual)['content']=='Prefiro verde.'
    assert next(n for n in notes if n['content']=='Usa Inter.')['evidence']=='Prefiro Inter.'

def test_create_uses_task_budget(spec,monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app import main
    from app.model_gateway import active_budget
    class Checked(FakeGateway):
        async def chat(self,*args,**kwargs):
            assert active_budget.get() is not None
            return await super().chat(*args,**kwargs)
    monkeypatch.setattr(main,'gateway',Checked([json.dumps({'type':'proposal','spec':spec.model_dump(),'rationale':'ok'})]))
    with TestClient(main.app) as c:assert c.post('/create/messages',json={'messages':[{'role':'user','content':'Crie'}]}).status_code==200
def test_memory_disabled_skips_extraction(spec,monkeypatch):
    from app import config
    from app.notes import set_memory_settings,MemorySettings,auto_remember,list_notes
    monkeypatch.setattr(config,'MEMORY_AUTOMATIC',True)
    aid=repo.create_agent(spec)
    with db.connect() as c:cid=db.insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
    set_memory_settings(aid,MemorySettings(enabled=False,automatic=False))
    asyncio.run(auto_remember(FakeGateway([]),aid,cid,'Prefiro Inter.'))
    assert list_notes(aid)==[]

def test_vm_cleanup_failure_is_reported(monkeypatch):
    from types import SimpleNamespace
    from app.tools import workspace_sandbox as ws
    workspace=ws.MultipassWorkspace(b'test');workspace.binary='multipass'
    assert ws._vm_slot.acquire(blocking=False)
    workspace.acquired=True
    monkeypatch.setattr(ws.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=1))
    try:
        with pytest.raises(RuntimeError,match='Limpeza'):workspace.close()
        assert workspace.acquired
    finally:
        workspace.acquired=False;ws._vm_slot.release()

def test_sqlite_context_closes_connection():
    connection=db.connect()
    with connection:connection.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError):connection.execute('SELECT 1')

def test_proposed_memory_tool_requires_permission_and_review(spec):
    from app.mcp_tools import build_mcp
    _,names=build_mcp(repo.create_agent(spec),set())
    assert 'mcp__ada__propor_nota' not in names
    _,names=build_mcp(repo.create_agent(spec),{'mcp_memoria'})
    assert 'mcp__ada__propor_nota' in names
def test_memory_recalled_in_new_conversation(spec,monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app import main,config
    monkeypatch.setattr(config,'MEMORY_AUTOMATIC',True)
    extracted=json.dumps({'facts':[{'content':'O usuário prefere Inter.','evidence':'Eu prefiro a fonte Inter.','replace_id':None}]})
    prompts=[]
    class Capture(FakeGateway):
        async def chat(self,messages,**kwargs):
            prompts.append(messages)
            return await super().chat(messages,**kwargs)
    monkeypatch.setattr(main,'gateway',Capture(['Entendido.',extracted,'Sua preferência é Inter.',json.dumps({'facts':[]})]))
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        c1=client.post(f'/agents/{aid}/conversations').json()['id']
        assert client.post(f'/agents/{aid}/conversations/{c1}/messages',json={'content':'Eu prefiro a fonte Inter.'}).status_code==200
        c2=client.post(f'/agents/{aid}/conversations').json()['id']
        assert client.post(f'/agents/{aid}/conversations/{c2}/messages',json={'content':'Qual fonte usar?'}).status_code==200
        assert 'O usuário prefere Inter.' in prompts[2][0]['content']


def test_oauth_navigation_exception_is_narrow(monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from app.main import app
    from app import connections
    nav={'sec-fetch-site':'cross-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'}
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        for provider in ('github','figma','drive'):
            # A callback navigation reaches state validation, not an exchange.
            r=client.get(f'/connections/{provider}/callback',params={'state':'invalid','code':'dummy'},headers=nav)
            assert r.status_code==400 and 'expirada' in r.json()['detail']
        monkeypatch.setitem(connections._pending,'expired-state',{'provider':'github','expires':time.time()-60})
        assert client.get('/connections/github/callback',params={'state':'expired-state','code':'dummy'},headers=nav).status_code==400
        assert client.get('/agents',headers=nav).status_code==403
        assert client.get('/connections/github/callback',headers={**nav,'sec-fetch-mode':'cors'}).status_code==403
        assert client.get('/connections/github/callback',headers={**nav,'sec-fetch-dest':'iframe'}).status_code==403
        assert client.post('/connections/github/start',json={},headers=nav).status_code==403
        assert client.get('/connections/unknown/callback',headers=nav).status_code==403
    with TestClient(app,base_url='http://evil.example') as client:
        assert client.get('/connections/github/callback',headers=nav).status_code==403


def test_figma_oauth_cross_site_return_and_state_replay(monkeypatch):
    from urllib.parse import parse_qs,urlsplit
    from fastapi.testclient import TestClient
    from app.main import app
    from app import connections
    monkeypatch.setenv('FIGMA_CLIENT_ID','test');monkeypatch.setenv('FIGMA_CLIENT_SECRET','test')
    nav={'sec-fetch-site':'cross-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'}
    calls=[]
    class R:
        def raise_for_status(self):pass
        def json(self):return {'access_token':'dummy-token','scope':'current_user:read'}
    class C:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):calls.append(1);return R()
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        start=client.post('/connections/figma/start',json={'frontend_origin':'http://127.0.0.1:5173'})
        state=parse_qs(urlsplit(start.json()['url']).query)['state'][0]
        monkeypatch.setattr(connections.httpx,'AsyncClient',C)
        result=client.get('/connections/figma/callback',params={'state':state,'code':'dummy'},headers=nav,follow_redirects=False)
        assert result.status_code==303 and 'status=connected' in result.headers['location']
        assert client.get('/connections/figma/callback',params={'state':state,'code':'dummy'},headers=nav).status_code==400
        assert len(calls)==1

def test_resource_grants_are_versioned_and_revocable(spec,monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import HTTPException
    from app.main import app
    from app.mcp_connections import authorize
    with TestClient(app) as c:
        aid=c.post('/agents',json=spec.model_dump()).json()['id']
        response=c.put(f'/agents/{aid}/resources',json={'resources':{'github':['https://github.com/user/project'],'docs':['doc1']}})
        assert response.status_code==200 and response.json()['version']==2
        authorize(aid,'mcp_github','github','user/project')
        with pytest.raises(HTTPException):authorize(aid,'mcp_github','github','user/private')
        c.put(f'/agents/{aid}/resources',json={'resources':{}})
        with pytest.raises(HTTPException):authorize(aid,'mcp_github','github','user/project')


def test_native_delegation_hooks_limit_and_audit(spec,monkeypatch):
    mock_sdk_client_for_query(monkeypatch)
    import json,claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from fastapi.testclient import TestClient
    from app import main,config
    from app.model_gateway import ClaudeAgentGateway
    captured=[]
    async def fake_query(*,prompt,options):
        captured.append(options)
        if options.agents:
            definition=options.agents['observability_1']
            assert 'Agent' in definition.disallowedTools and definition.background is False
            hook=next(h.hooks[0] for h in options.hooks['PreToolUse'] if h.matcher=='^(Agent|Task)$')
            bad=await hook({'tool_input':{'subagent_type':'unknown'}},'bad',None)
            assert bad['hookSpecificOutput']['permissionDecision']=='deny'
            for i in range(config.MAX_SUBAGENTS):assert await hook({'tool_input':{'subagent_type':'observability_1'}},str(i),None)=={}
            denied=await hook({'tool_input':{'subagent_type':'observability_1'}},'extra',None)
            assert denied['hookSpecificOutput']['permissionDecision']=='deny'
            await options.hooks['PostToolUse'][0].hooks[0]({},'0',None)
            text='Análise concluída.'
        else:text=json.dumps({'tasks':[{'capability':'observability','assignment':'Analise CI'}]})
        yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='x',result=text,total_cost_usd=0.001,usage={})
    monkeypatch.setattr(config,'MODEL_API_KEY','test');monkeypatch.setattr(config,'MODEL_NAME','test')
    monkeypatch.setattr(claude_agent_sdk,'query',fake_query);monkeypatch.setattr(main,'gateway',ClaudeAgentGateway())
    with TestClient(main.app) as c:
        aid=c.post('/agents',json=spec.model_dump()).json()['id'];cid=c.post(f'/agents/{aid}/conversations').json()['id']
        result=c.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Investigue CI'})
        assert result.status_code==200,result.text
        rid=c.get(f'/agents/{aid}/runs').json()[0]['id']
        events=c.get(f'/runs/{rid}').json()['events']
        assert sum(e['type']=='subagent_started' for e in events)==config.MAX_SUBAGENTS
        assert any(e['type']=='subagent_completed' for e in events)
        assert captured[1].env['CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS']=='1'


def test_scheduled_review_continuity_and_unchanged_skip(spec,monkeypatch):
    from app import reviews,main
    from app.model_gateway import ClaudeAgentGateway
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway())
    from fastapi.testclient import TestClient
    import json
    state={'sha':'a'*40,'calls':0}
    async def info(*args):return ('user/project','main',state['sha'],True)
    async def send(aid,cid,body):
        state['calls']+=1
        with db.connect() as conn:prev=conn.execute("SELECT id FROM repository_reviews WHERE status='completed' ORDER BY id DESC LIMIT 1").fetchone()
        return {'text':json.dumps({'commit':state['sha'],'previous_review_id':prev['id'] if prev else None,'summary':'Revisado','findings':[]})}
    monkeypatch.setattr(reviews,'base_info',info);monkeypatch.setattr(reviews,'session',lambda:{'access_token':'test'})
    monkeypatch.setattr(main,'send_message',send)
    with TestClient(main.app) as c:
        aid=c.post('/agents',json=spec.model_dump()).json()['id']
        assert c.put(f'/agents/{aid}/resources',json={'resources':{'github':['user/project']}}).status_code==200
        sid=c.post(f'/agents/{aid}/review-schedules',json={'repository':'user/project','max_daily_runs':3}).json()['id']
        assert c.get(f'/agents/{aid}/review-schedules').json()[0]['enabled']==0
        first=c.post(f'/agents/{aid}/review-schedules/{sid}/run');assert first.status_code==200,first.text
        firstid=first.json()['id']
        second=c.post(f'/agents/{aid}/review-schedules/{sid}/run')
        assert second.json()['status']=='unchanged' and state['calls']==1
        state['sha']='b'*40
        third=c.post(f'/agents/{aid}/review-schedules/{sid}/run')
        assert third.json()['report']['previous_review_id']==firstid and state['calls']==2
        assert c.get(f'/agents/{aid}/reviews').json()[0]['status']=='completed'


def test_review_failure_pauses_schedule(spec,monkeypatch):
    from app import reviews,main
    from app.model_gateway import ClaudeAgentGateway
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway())
    from fastapi.testclient import TestClient
    async def info(*args):return ('user/project','main','a'*40,True)
    async def send(*args):return {'text':'not json'}
    monkeypatch.setattr(reviews,'base_info',info);monkeypatch.setattr(reviews,'session',lambda:{'access_token':'test'});monkeypatch.setattr(main,'send_message',send)
    with TestClient(main.app) as c:
        aid=c.post('/agents',json=spec.model_dump()).json()['id']
        c.put(f'/agents/{aid}/resources',json={'resources':{'github':['user/project']}})
        sid=c.post(f'/agents/{aid}/review-schedules',json={'repository':'user/project','enabled':True}).json()['id']
        assert c.post(f'/agents/{aid}/review-schedules/{sid}/run').status_code==502
        state=c.get(f'/agents/{aid}/review-schedules').json()[0]
        assert state['enabled']==0 and state['status']=='idle'

def test_chat_code_uses_current_user_task_and_no_duplicate(spec,monkeypatch):
    from types import SimpleNamespace
    from app import conversation_tools as ct,code_workflow
    from app.models import AgentSpec
    d=spec.model_dump();d['tools']+=['mcp_github','mcp_codigo'];d['autonomy_policy'].update(mcp_github='allowed',mcp_codigo='allowed');d['resources']={'github':['user/project']}
    aid=repo.create_agent(AgentSpec.model_validate(d));calls=[]
    async def prepare(a,c,body):
        calls.append(body.task)
        assert ct.code_in_chat.get()
        return {'draft_id':'draft-test','text':'Proposta preparada','validation':{'status':'passed'}}
    monkeypatch.setattr(code_workflow,'code_task',SimpleNamespace(__wrapped__=prepare))
    async def run():
        t=ct.chat_task.set((aid,123,'Implemente minha tela'));a=ct.chat_artifacts.set([])
        try:
            await ct.prepare_chat_code(aid,'user/project');await ct.prepare_chat_code(aid,'user/project')
            assert ct.chat_artifacts.get()==['draft-test']
        finally:ct.chat_task.reset(t);ct.chat_artifacts.reset(a)
    asyncio.run(run());assert calls==['Implemente minha tela']


def test_nested_sdk_calls_reserve_parent_budget(monkeypatch):
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from app import config
    from app.model_gateway import ClaudeAgentGateway,TaskBudget,active_budget
    monkeypatch.setattr(config,'MODEL_API_KEY','test');monkeypatch.setattr(config,'MODEL_NAME','test');monkeypatch.setattr(config,'SDK_MAX_BUDGET_USD',0.25)
    budgets=[];gateway=ClaudeAgentGateway()
    async def fake_query(*,prompt,options):
        budgets.append(options.max_budget_usd)
        if len(budgets)==1:
            assert active_budget.get().reserved==0.25
            await gateway.chat([{'role':'user','content':'nested'}])
        yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='x',result='ok',total_cost_usd=0.01,usage={})
    monkeypatch.setattr(claude_agent_sdk,'query',fake_query)
    async def run():
        budget=TaskBudget(limit=0.4);t=active_budget.set(budget)
        try:
            await gateway.chat([{'role':'user','content':'outer'}])
            assert abs(budgets[1]-0.15)<0.00001 and budget.reserved==0
            assert abs(budget.spent-0.02)<0.00001
        finally:active_budget.reset(t)
    asyncio.run(run())


def test_scheduler_only_runs_enabled_due_reviews(spec,monkeypatch):
    import time
    from app import reviews
    aid=repo.create_agent(spec);calls=[]
    with db.connect() as c:
        sid=db.insert_id(c,'INSERT INTO review_schedules(agent_id,repository,interval_hours,enabled,next_at) VALUES(?,?,24,0,?)',(aid,'user/project',int(time.time())-10))
    async def run(a,s):
        calls.append((a,s))
        with db.connect() as c:c.execute('UPDATE review_schedules SET next_at=? WHERE id=?',(int(time.time())+3600,s))
    monkeypatch.setattr(reviews,'run_review',run)
    asyncio.run(reviews.scheduler_tick());assert not calls
    with db.connect() as c:c.execute('UPDATE review_schedules SET enabled=1 WHERE id=?',(sid,))
    asyncio.run(reviews.scheduler_tick());asyncio.run(reviews.scheduler_tick());assert calls==[(aid,sid)]

def test_github_mcp_protocol_and_live_revocation(spec,monkeypatch):
    import anyio,json
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from app.models import AgentSpec
    from app import mcp_connections,repository
    from app.mcp_tools import build_mcp
    d=spec.model_dump();d['tools'].append('mcp_github');d['autonomy_policy']['mcp_github']='allowed';d['resources']={'github':['user/project']}
    aid=repo.create_agent(AgentSpec.model_validate(d));calls=[]
    async def info(*args):return ('user/project','main','a'*40,True)
    async def read(*args):calls.append(args);return ('example','100644')
    monkeypatch.setattr(repository,'base_info',info);monkeypatch.setattr(repository,'read_file',read);monkeypatch.setattr(repository,'session',lambda:{'access_token':'test'})
    server=build_mcp(aid,{'mcp_github'})[0]['ada']['instance']
    async def run():
        async with create_client_server_memory_streams() as (client_streams,server_streams):
            async with anyio.create_task_group() as tg:
                tg.start_soon(server.run,server_streams[0],server_streams[1],server.create_initialization_options())
                async with ClientSession(client_streams[0],client_streams[1]) as client:
                    await client.initialize()
                    result=await client.call_tool('github_arquivo',{'repo':'user/project','path':'README.md'})
                    assert json.loads(result.content[0].text)['content']=='example'
                    denied=await client.call_tool('github_arquivo',{'repo':'user/other','path':'README.md'})
                    assert denied.is_error
                    new=repo.add_version(aid,spec,'Revogar','teste');repo.activate_version(aid,new)
                    revoked=await client.call_tool('github_arquivo',{'repo':'user/project','path':'README.md'})
                    assert revoked.is_error and len(calls)==1
                tg.cancel_scope.cancel()
    anyio.run(run)

def test_scheduled_native_runtime_exposes_only_github_read_tools(spec,monkeypatch):
    import json,claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from app.models import AgentSpec
    from app import config
    from app.model_gateway import ClaudeAgentGateway,active_review_schema,active_budget,TaskBudget
    from app.orchestration import orchestrate
    from app.reviews import ReviewReport
    d=spec.model_dump();d['tools']+=['mcp_github','mcp_codigo','pesquisar_web','mcp_memoria'];d['autonomy_policy'].update(mcp_github='allowed',mcp_codigo='allowed',pesquisar_web='allowed',mcp_memoria='allowed');d['resources']={'github':['user/project']}
    current=AgentSpec.model_validate(d);aid=repo.create_agent(current)
    with db.connect() as c:
        cid=db.insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
        rid=db.insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'running')",(aid,cid))
    def result(text):return ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='x',result=text,total_cost_usd=0.001,usage={})
    async def plan(*,prompt,options):yield result(json.dumps({'tasks':[{'capability':'scheduled_review','assignment':'Revise'}]}))
    class Client:
        def __init__(self,options):
            assert 'Agent' in options.allowed_tools
            assert 'WebSearch' not in options.tools
            assert all('preparar_codigo' not in t and 'propor_nota' not in t for t in options.allowed_tools)
            assert all('WebSearch' not in a.tools and not any('preparar_codigo' in t for t in a.tools) for a in options.agents.values())
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def query(self,*args):pass
        async def receive_response(self):yield result(json.dumps({'commit':'a'*40,'previous_review_id':None,'summary':'Revisado','findings':[]}))
    monkeypatch.setattr(config,'MODEL_API_KEY','test');monkeypatch.setattr(config,'MODEL_NAME','test')
    monkeypatch.setattr(claude_agent_sdk,'query',plan);monkeypatch.setattr(claude_agent_sdk,'ClaudeSDKClient',Client)
    async def run():
        t=active_review_schema.set(ReviewReport);b=active_budget.set(TaskBudget(limit=0.5))
        try:assert ReviewReport.model_validate_json(await orchestrate(ClaudeAgentGateway(),current,[],'Revise user/project',rid)).commit=='a'*40
        finally:active_review_schema.reset(t);active_budget.reset(b)
    asyncio.run(run())

def test_creation_preserves_selected_identity(spec, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'gateway', FakeGateway([json.dumps({'type':'proposal','spec':spec.model_dump(),'rationale':'Teste'})]))
    with TestClient(main.app) as client:
        response=client.post('/create/messages',json={'messages':[{'role':'user','content':'Criar pesquisador'}],'identity':{'name':'Bowie','avatar':'toucan','color':'amber'}})
    assert response.status_code==200
    assert response.json()['spec']['name']=='Bowie'
    assert response.json()['spec']['style']['avatar']=='toucan'

def test_structured_accepts_fenced_json():
    from pydantic import BaseModel
    class Result(BaseModel):
        ok: bool
    result=asyncio.run(generate_structured(FakeGateway(['```json\n{"ok":true}\n```']), [], Result))
    assert result.ok

def test_direct_creation_returns_proposal_without_interview(spec,monkeypatch):
    import json
    from app import main
    from fastapi.testclient import TestClient
    gateway=FakeGateway([json.dumps({'type':'question','text':'Qual fonte?'}),json.dumps({'type':'proposal','spec':spec.model_dump()})])
    monkeypatch.setattr(main,'gateway',gateway)
    with TestClient(main.app) as client:
        response=client.post('/create/messages',json={'direct':True,'messages':[{'role':'user','content':'Analisar notícias e preparar relatórios'}],'identity':{'name':'Tom','avatar':'hedgehog','color':'amber'}})
    assert response.status_code==200
    assert response.json()['type']=='proposal'
    assert response.json()['spec']['name']=='Tom'
    assert response.json()['spec']['style']['avatar']=='hedgehog'

def test_export_on_request_is_bound_to_original_response(spec,monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main,'gateway',FakeGateway(['Relatório original','Outro conteúdo']))
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Analise.'})
        exported=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Exporte a última resposta em Markdown'})
        assert exported.status_code==200
        import re
        link=re.search(r'\]\(([^)]+)\)',exported.json()['text'])[1]
        client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Outra análise.'})
        assert client.get(link.removeprefix('/api')).content.decode()=='Relatório original'
        other=client.post(f'/agents/{aid}/conversations').json()['id']
        assert client.get(link.removeprefix('/api').replace(f'conversations/{cid}/',f'conversations/{other}/')).status_code==404

def test_delete_chat_removes_sourced_memory_and_keeps_other_chat(spec):
    from app import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        other=client.post(f'/agents/{aid}/conversations').json()['id']
        with db.connect() as c:
            c.execute("INSERT INTO messages(conversation_id,role,content) VALUES(?,'assistant','texto')",(cid,))
            c.execute("INSERT INTO agent_notes(agent_id,content,status,source,conversation_id,evidence) VALUES(?,'Preferência','active','user',?,'texto')",(aid,cid))
        assert client.delete(f'/agents/{aid}/conversations/{cid}').status_code==200
        assert client.get(f'/agents/{aid}/conversations/{cid}/messages').status_code==404
        assert client.get(f'/agents/{aid}/conversations/{other}/messages').status_code==200
        with db.connect() as c:
            note=c.execute('SELECT * FROM agent_notes WHERE agent_id=?',(aid,)).fetchone()
            assert note is None


def mock_sdk_client_for_query(monkeypatch):
    import claude_agent_sdk
    class Client:
        def __init__(self,*,options):self.options=options
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def query(self,prompt):self.prompt=prompt
        async def receive_response(self):
            async for message in claude_agent_sdk.query(prompt=self.prompt,options=self.options):yield message
    monkeypatch.setattr(claude_agent_sdk,'ClaudeSDKClient',Client)


def test_persistent_summary_incremental_recall_and_delete(spec,monkeypatch):
    import json
    from app import conversation_context as ctx,main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(ctx,'COMPACT_AFTER_MESSAGES',6)
    monkeypatch.setattr(ctx,'RECENT_MESSAGES',4)
    aid=repo.create_agent(spec)
    with db.connect() as c:
        cid=db.insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
        c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(cid,'user' if i%2==0 else 'assistant',f'Mensagem {i}: requisito pagamento sem cadastro' if i==0 else f'Mensagem {i}') for i in range(10)])
    gateway=FakeGateway([json.dumps({'summary':'Decisão: pagamento sem cadastro [mensagem 1].'})])
    result=asyncio.run(ctx.build_context(gateway,aid,cid,'cadastro'))
    assert any('Resumo anterior' in m['content'] for m in result)
    assert any('Trechos originais' in m['content'] and 'pagamento' in m['content'] for m in result)
    with db.connect() as c:
        summary=c.execute('SELECT * FROM conversation_summaries WHERE conversation_id=?',(cid,)).fetchone()
        assert summary['revision']==1
        cursor=summary['last_message_id']
        assert c.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]==10
    # A reload uses the persisted summary and needs no additional model call.
    again=asyncio.run(ctx.build_context(FakeGateway([]),aid,cid,'cadastro'))
    assert any('Decisão: pagamento sem cadastro' in m['content'] for m in again)
    other=repo.create_agent(spec)
    assert ctx.recall(other,cid,'cadastro',999999)==[]
    with db.connect() as c:
        c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(cid,'user',f'Nova mensagem {i}') for i in range(8)])
    asyncio.run(ctx.build_context(FakeGateway([json.dumps({'summary':'Resumo atualizado.'})]),aid,cid,'atualização'))
    with db.connect() as c:
        row=c.execute('SELECT * FROM conversation_summaries WHERE conversation_id=?',(cid,)).fetchone()
        assert row['revision']==2 and row['last_message_id']>cursor
    with TestClient(main.app) as client:
        assert client.delete(f'/agents/{aid}/conversations/{cid}').status_code==200
    with db.connect() as c:assert not c.execute('SELECT 1 FROM conversation_summaries WHERE conversation_id=?',(cid,)).fetchone()

def test_summary_failure_preserves_cursor_and_messages(spec,monkeypatch):
    from app import conversation_context as ctx
    monkeypatch.setattr(ctx,'COMPACT_AFTER_MESSAGES',6);monkeypatch.setattr(ctx,'RECENT_MESSAGES',4)
    aid=repo.create_agent(spec)
    with db.connect() as c:
        cid=db.insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
        c.executemany("INSERT INTO messages(conversation_id,role,content) VALUES(?,'user',?)",[(cid,f'Histórico {i}') for i in range(10)])
    context=asyncio.run(ctx.build_context(FakeGateway(['invalid']),aid,cid,'Histórico'))
    assert context
    with db.connect() as c:
        assert not c.execute('SELECT 1 FROM conversation_summaries WHERE conversation_id=?',(cid,)).fetchone()
        assert c.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]==10

def test_delete_profile_removes_only_its_data(spec):
    from app import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        other=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        client.post(f'/agents/{aid}/documents?name=brief.txt',content=b'Contexto',headers={'content-type':'application/octet-stream'})
        with db.connect() as c:
            c.execute("INSERT INTO messages(conversation_id,role,content) VALUES(?,'user','mensagem')",(cid,))
            c.execute("INSERT INTO conversation_summaries(conversation_id,summary,last_message_id) VALUES(?,'Resumo',1)",(cid,))
            c.execute("INSERT INTO agent_notes(agent_id,content,status,source,conversation_id) VALUES(?,'Memória','active','agent',?)",(aid,cid))
            rid=db.insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'completed')",(aid,cid))
            c.execute("INSERT INTO run_events(run_id,seq,type,payload_json) VALUES(?,1,'completed','{}')",(rid,))
            c.execute("INSERT INTO approvals(run_id,tool,payload_json,status) VALUES(?,'salvar_relatorio','{}','pending')",(rid,))
        assert client.delete(f'/agents/{aid}').status_code==200
        assert client.get(f'/agents/{aid}').status_code==404
        assert client.get(f'/agents/{other}').status_code==200
        with db.connect() as c:
            assert not c.execute('SELECT 1 FROM messages WHERE conversation_id=?',(cid,)).fetchone()
            assert not c.execute('SELECT 1 FROM agent_notes WHERE agent_id=?',(aid,)).fetchone()
            assert not c.execute('SELECT 1 FROM documents WHERE agent_id=?',(aid,)).fetchone()


def test_chat_reports_provider_rate_limit(spec,monkeypatch):
    import httpx
    from app import main
    from fastapi.testclient import TestClient
    class LimitedGateway(ModelGateway):
        async def chat(self,messages,**kwargs):
            request=httpx.Request('POST','https://example.invalid')
            response=httpx.Response(429,request=request)
            raise httpx.HTTPStatusError('limited',request=request,response=response)
    monkeypatch.setattr(main,'gateway',LimitedGateway())
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        response=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Oi'})
        assert response.status_code==429
        assert 'limite de uso' in response.json()['detail']
        assert client.get(f'/agents/{aid}/conversations/{cid}/messages').json()==[]


def test_workflows_version_execution_and_profile_cleanup(spec,monkeypatch):
    from app import main,workflows
    from app.model_gateway import ClaudeAgentGateway
    from fastapi.testclient import TestClient
    calls=[]
    async def send(aid,cid,body):calls.append((aid,cid,body.content));return {'text':'Entrega do workflow.'}
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway());monkeypatch.setattr(main,'send_message',send)
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        other=client.post('/agents',json=spec.model_dump()).json()['id']
        base=f'/agents/{aid}/workflows'
        created=client.post(base,json={'name':'Relatório','objective':'Pesquisar contexto e preparar relatório.'});assert created.status_code==200
        wid=created.json()['id'];assert created.json()['enabled'] is False
        assert client.get(f'/agents/{other}/workflows/{wid}/runs').status_code==404
        result=client.post(f'{base}/{wid}/run');assert result.status_code==200,result.text
        assert calls[0][2]=='Pesquisar contexto e preparar relatório.'
        assert client.post(f'{base}/{wid}/run').status_code==429
        assert client.get(f'{base}/{wid}/runs').json()[0]['version']==1
        assert client.patch(f'{base}/{wid}/recurrence',json={'enabled':True}).status_code==200
        assert client.put(f'{base}/{wid}',json={'name':'Novo relatório','objective':'Analisar métricas e criar relatório.'}).json()['version']==2
        assert client.get(base).json()[0]['enabled']==0
        assert len(client.get(f'{base}/{wid}/versions').json())==2
        assert client.delete(f'/agents/{aid}').status_code==200
        with db.connect() as c:assert c.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0]==0

def test_workflow_failure_pauses_and_restart_recovery(spec,monkeypatch):
    from app import main,workflows
    from app.model_gateway import ClaudeAgentGateway
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    async def send(*args):raise HTTPException(502,'Falha simulada')
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway());monkeypatch.setattr(main,'send_message',send)
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id'];base=f'/agents/{aid}/workflows'
        wid=client.post(base,json={'name':'Fluxo','objective':'Preparar entrega.'}).json()['id']
        client.patch(f'{base}/{wid}/recurrence',json={'enabled':True})
        assert client.post(f'{base}/{wid}/run').status_code==502
        assert client.get(base).json()[0]['enabled']==0
        assert client.get(f'{base}/{wid}/runs').json()[0]['status']=='failed'
        with db.connect() as c:c.execute("UPDATE workflows SET status='running',enabled=1 WHERE id=?",(wid,))
        workflows.recover_interrupted()
        assert client.get(base).json()[0]['status']=='idle'
        assert client.get(base).json()[0]['enabled']==0


def test_attachment_text_scope_and_chat(spec,monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    captured=[]
    class Gateway(ModelGateway):
        async def chat(self,messages,**kwargs):captured.extend(messages);return ModelResult('Análise recebida.',1,1,False)
    monkeypatch.setattr(main,'gateway',Gateway())
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id']
        cid=client.post(f'/agents/{aid}/conversations').json()['id']
        other=client.post(f'/agents/{aid}/conversations').json()['id']
        base=f'/agents/{aid}/conversations/{cid}'
        upload=client.post(base+'/attachments?name=vendas.csv',content=b'produto,quantidade,preco\nMaca,2,10')
        assert upload.status_code==200,upload.text
        ident=upload.json()['id']
        assert client.post(f'/agents/{aid}/conversations/{other}/messages',json={'content':'Analisar','attachment_ids':[ident]}).status_code==422
        result=client.post(base+'/messages',json={'content':'Analise os dados','attachment_ids':[ident]})
        assert result.status_code==200,result.text
        assert any('Maca,2,10' in str(m['content']) for m in captured)
        assert client.post(base+'/messages',json={'content':'Analisar','attachment_ids':[ident]}).status_code==422
        assert client.delete(base).status_code==200
        with db.connect() as c:assert not c.execute('SELECT 1 FROM chat_attachments WHERE id=?',(ident,)).fetchone()

def test_gemini_image_payload_and_reset(spec,monkeypatch):
    import httpx,base64
    from app import config,main
    from app.model_gateway import OpenAICompatibleGateway,active_images
    from fastapi.testclient import TestClient
    captured=[]
    class HTTP:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,*,json,headers):
            captured.append(json)
            return httpx.Response(200,json={'choices':[{'message':{'content':'Imagem recebida.'}}],'usage':{'prompt_tokens':10,'completion_tokens':5}},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'AsyncClient',HTTP);monkeypatch.setattr(main,'gateway',OpenAICompatibleGateway())
    monkeypatch.setattr(config,'MODEL_NAME','gemini-test');monkeypatch.setattr(config,'MODEL_BASE_URL','https://generativelanguage.googleapis.com/v1beta/openai');monkeypatch.setattr(config,'MODEL_API_KEY','test')
    monkeypatch.setenv('ADA_INPUT_USD_PER_MTOK','1');monkeypatch.setenv('ADA_OUTPUT_USD_PER_MTOK','10')
    image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
    with TestClient(main.app) as client:
        aid=client.post('/agents',json=spec.model_dump()).json()['id'];cid=client.post(f'/agents/{aid}/conversations').json()['id'];base=f'/agents/{aid}/conversations/{cid}'
        assert client.post(base+'/attachments?name=bad.png',content=b'not image').status_code==400
        ident=client.post(base+'/attachments?name=image.png',content=image).json()['id']
        result=client.post(base+'/messages',json={'content':'Descreva','attachment_ids':[ident]});assert result.status_code==200,result.text
        assert captured[0]['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert not active_images.get()

@pytest.mark.parametrize('password',['testpass','test-password-long-enough-123'])
def test_private_cloud_auth_origin_and_api_prefix(spec,monkeypatch,password):
    from app import main
    from fastapi.testclient import TestClient
    monkeypatch.setenv('ADA_DEPLOYMENT_MODE','private_cloud');monkeypatch.setenv('ADA_PUBLIC_ORIGIN','https://ada.example');monkeypatch.setenv('ADA_ACCESS_PASSWORD',password);monkeypatch.setenv('ADA_DATABASE_BACKEND','postgres')
    with TestClient(main.app,base_url='https://ada.example') as client:
        assert client.get('/api/agents').status_code==401
        assert client.get('/health').status_code==200
        assert client.get('/api/agents',auth=('ada','wrong')).status_code==401
        assert client.get('/api/agents',auth=('ada',password)).status_code==200
        assert client.get('/api/connections').status_code==401
        assert client.get('/api/connections',auth=('ada',password)).status_code==200
        assert client.get('/api/connections',auth=('ada',password),headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/agents',json=spec.model_dump(),auth=('ada',password),headers={'Origin':'https://evil.example'}).status_code==403
        assert client.get('/api/agents',auth=('ada',password),headers={'Host':'other.example'}).status_code==403
        monkeypatch.setenv('ADA_ACCESS_PASSWORD','short77')
        assert client.get('/api/agents',auth=('ada','short77')).status_code==503
    monkeypatch.delenv('ADA_ACCESS_PASSWORD')
    with TestClient(main.app,base_url='https://ada.example') as client:assert client.get('/api/agents').status_code==503


def test_claude_image_uses_multimodal_prompt(monkeypatch):
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from app import config
    from app.model_gateway import ClaudeAgentGateway,active_images
    seen=[]
    async def query(*,prompt,options):
        async for item in prompt:seen.append(item)
        yield ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,is_error=False,num_turns=1,session_id='image-test',result='Imagem analisada.',total_cost_usd=.001,usage={'input_tokens':1,'output_tokens':1})
    monkeypatch.setattr(claude_agent_sdk,'query',query)
    monkeypatch.setattr(config,'MODEL_API_KEY','test');monkeypatch.setattr(config,'MODEL_NAME','test')
    token=active_images.set([{'mime':'image/png','data':'testdata'}])
    try:result=asyncio.run(ClaudeAgentGateway().chat([{'role':'user','content':'Analise a imagem.'}]))
    finally:active_images.reset(token)
    assert seen[0]['message']['content'][1]['source']['media_type']=='image/png'
    assert result.text=='Imagem analisada.'


@pytest.mark.parametrize('avatar',['bear','crab'])
def test_new_character_identity_persisted(spec,avatar):
    from app import main
    from fastapi.testclient import TestClient
    data=spec.model_dump();data['name']='Nome escolhido';data['style']['avatar']=avatar
    with TestClient(main.app) as client:
        result=client.post('/agents',json=data);assert result.status_code==200
        aid=result.json()['id']
        assert client.get(f'/agents/{aid}').json()['spec']['style']['avatar']==avatar
        entry=next(a for a in client.get('/agents').json() if a['id']==aid)
        assert entry['avatar']==avatar and entry['name']=='Nome escolhido'


def test_postgres_adapter_escapes_literal_percent_in_execute_and_many():
    captured=[]
    class Raw:
        def execute(self,query,params):captured.append((query,params))
        def cursor(self):return self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def executemany(self,query,rows):captured.append((query,rows))
    adapter=db.PgConnection(Raw())
    query="SELECT ? WHERE 'image/png' LIKE 'image/%' AND 'Arquivo solicitado: teste' LIKE 'Arquivo solicitado:%'"
    adapter.execute(query,('100% do parâmetro',))
    adapter.executemany(query,[('outro valor %',)])
    expected="SELECT %s WHERE 'image/png' LIKE 'image/%%' AND 'Arquivo solicitado: teste' LIKE 'Arquivo solicitado:%%'"
    assert captured[0]==(expected,('100% do parâmetro',))
    assert captured[1]==(expected,[('outro valor %',)])
