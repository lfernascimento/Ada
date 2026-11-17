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
        text = proposal if len(calls)==1 else ('{\"tasks\":[{\"capability\":\"code_review\",\"assignment\":\"Revisar trecho enviado\"}]}' if len(calls)==2 else ('Análise da função.' if len(calls)==3 else 'Resposta pelo SDK.'))
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
        assert runs[0]['status']=='completed' and runs[0]['steps']==3
        assert abs(runs[0]['cost_estimate']-0.003)<0.000001
        events=client.get(f"/runs/{runs[0]['id']}").json()['events']
        assert [event['type'] for event in events] == ['started','plan','subagent_started','subagent_completed','synthesis_started','completed']
        assert client.get(f'/agents/{aid}/conversations').json()[0]['title']=='Oi'
    assert all(opts.tools==[] and opts.allowed_tools==[] and opts.setting_sources==[] for _,opts in calls)
    assert all(opts.max_turns==1 and opts.max_budget_usd > 0 for _,opts in calls)
    assert 'Crie um agente' in calls[0][0] and 'Oi' in calls[1][0] and len(calls)==4

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
    import json
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from fastapi.testclient import TestClient
    from app import main,config
    from app.model_gateway import ClaudeAgentGateway
    outputs=iter([json.dumps({'tasks':[{'capability':'research','assignment':'Busque referências'}]}),'Fonte: https://example.com','## Proposta\nFazer MVP.'])
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
        assert calls[1][1].tools==['WebSearch'] and calls[1][1].allowed_tools==['WebSearch']
        assert calls[1][1].hooks['PreToolUse'][0].on_failure=='block'
        hook=calls[1][1].hooks['PreToolUse'][0].hooks[0]
        async def check_hook():
            results=[await hook({'tool_name':'WebSearch','hook_event_name':'PreToolUse'},None,None) for _ in range(4)]
            assert results[-1]['hookSpecificOutput']['permissionDecision']=='deny'
        asyncio.run(check_hook())
        assert calls[0][1].tools==[] and calls[2][1].tools==[]
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
        response=client.get('/connections/github/callback',params={'state':state,'code':'test-code'},follow_redirects=False)
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
        r=client.get('/connections/drive/callback',params={'state':state,'code':'test'},follow_redirects=False)
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
    aid=repo.create_agent(spec);other=repo.create_agent(spec)
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
