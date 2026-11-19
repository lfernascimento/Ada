import asyncio,base64,json,uuid
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import db,repo,config,security,main
from app.models import AgentSpec
from app.model_gateway import ClaudeAgentGateway

SID=str(uuid.uuid4())

@pytest.fixture(autouse=True)
def database(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_BACKEND','sqlite');monkeypatch.setattr(db,'DB_PATH',tmp_path/'runtime.db')
    monkeypatch.setattr(config,'SDK_ORCHESTRATION','native');monkeypatch.setattr(config,'MODEL_API_KEY','test-key')
    monkeypatch.setattr(config,'MODEL_NAME','principal');monkeypatch.setattr(config,'LIGHT_MODEL','leve')
    monkeypatch.setattr(config,'MEMORY_AUTOMATIC',False)
    monkeypatch.setattr(security,'TRUSTED_HOSTS',security.TRUSTED_HOSTS|{'testserver'})
    monkeypatch.setattr(security,'TRUSTED_PEERS',security.TRUSTED_PEERS|{'testclient'})
    monkeypatch.setattr(main,'gateway',ClaudeAgentGateway());db.init_db()

def profile():
    return AgentSpec.model_validate(dict(name='Nina',purpose='Resolver problemas com evidências e entregas.',instructions=['Use seu julgamento.'],tools=[],autonomy_policy={},limits={'max_steps':6},style={'color':'sage','avatar':'hedgehog'}))

def conversation():
    aid=repo.create_agent(profile())
    with db.connect() as c:cid=db.insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
    return aid,cid

def result(text='Concluído.',cost=.001,error=False):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype='success' if not error else 'error_max_budget_usd',duration_ms=1,duration_api_ms=1,is_error=error,num_turns=1,session_id=SID,result=text,usage={'input_tokens':10,'output_tokens':5},total_cost_usd=cost)

def sdk(monkeypatch,respond):
    import claude_agent_sdk
    calls=[]
    class Client:
        def __init__(self,*,options):self.options=options;calls.append(self)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def query(self,prompt):self.prompt=prompt
        async def receive_response(self):
            async for message in respond(self,len(calls)):yield message
    monkeypatch.setattr(claude_agent_sdk,'ClaudeSDKClient',Client)
    return calls

def test_native_one_loop_resume_store_files_and_incremental_cost(monkeypatch):
    aid,cid=conversation();cwd=[]
    async def respond(client,number):
        opts=client.options;cwd.append(opts.cwd);key={'project_key':'opaque','session_id':SID}
        assert opts.tools==['Agent','Read','Write','Edit','Glob','Grep','Skill','TodoWrite']
        assert opts.setting_sources==['project'] and opts.skills==['investigar','analisar-dados','construir-entrega']
        assert opts.strict_mcp_config and 'Bash' in opts.disallowed_tools
        assert opts.agents['research_main'].model=='principal' and opts.agents['research_light'].model=='leve'
        assert 'mcp__ada__perguntar_usuario' in opts.allowed_tools
        assert 'mcp__ada__perguntar_usuario' not in opts.agents['research_main'].tools
        path=Path(opts.cwd)/'relatorio.md'
        if number==1:
            assert opts.resume is None;path.write_text('Primeira entrega.',encoding='utf-8')
        else:
            assert opts.resume==SID and path.read_text()=='Primeira entrega.'
            assert len(await opts.session_store.load(key))==1
            assert 'Primeiro pedido' not in client.prompt and 'Segunda mensagem' in client.prompt
        await opts.session_store.append(key,[{'uuid':str(uuid.uuid4()),'message':{'role':'user','content':'opaque'}}])
        yield result(cost=.001*number)
    calls=sdk(monkeypatch,respond)
    with TestClient(main.app) as client:
        for text in ('Primeiro pedido','Segunda mensagem'):
            response=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':text})
            assert response.status_code==200,response.text
        runs=client.get(f'/agents/{aid}/runs').json()
        assert len(calls)==2 and all(r['steps']==1 and r['cost_estimate']==pytest.approx(.001) for r in runs)
        assert cwd[0]==cwd[1] and not Path(cwd[0]).exists()
        assert client.get(f'/agents/{aid}/conversations/{cid}/workspace/file?path=relatorio.md').content==b'Primeira entrega.'
        fork=client.post(f'/agents/{aid}/conversations/{cid}/fork').json()['id']
        with db.connect() as c:
            assert not c.execute('SELECT session_id FROM sdk_runtime_state WHERE conversation_id=?',(fork,)).fetchone()['session_id']
        assert client.delete(f'/agents/{aid}/conversations/{cid}').status_code==200
        assert client.get(f'/agents/{aid}/conversations/{cid}/workspace/files').status_code==404
        assert client.delete(f'/agents/{aid}').status_code==200

def test_store_preserves_entries_deduplicates_and_scopes():
    from app.sdk_sessions import ConversationStore
    aid,cid=conversation();other,foreign=conversation()
    async def run():
        store=ConversationStore(aid,cid);key={'project_key':'opaque','session_id':SID}
        entries=[{'uuid':'one','nested':{'content':['ação',{'n':1}]}},{'uuid':'two','payload':'raw'}]
        await store.append(key,entries);await store.append(key,entries)
        assert await store.load(key)==entries
        sub={**key,'subpath':'subagents/worker.jsonl'}
        await store.append(sub,[{'uuid':'child','opaque':True}])
        assert await store.list_subkeys(key)==['subagents/worker.jsonl']
        assert await ConversationStore(other,foreign).load(key) is None
        with pytest.raises(ValueError):await ConversationStore(other,cid).load(key)
        await store.delete(sub);assert await store.list_subkeys(key)==[]
        await store.delete(key);assert await store.load(key) is None
    asyncio.run(run())

def test_official_sdk_session_store_conformance():
    from claude_agent_sdk.testing import run_session_store_conformance
    from app.sdk_sessions import ConversationStore
    def factory():
        aid,cid=conversation();return ConversationStore(aid,cid)
    asyncio.run(run_session_store_conformance(factory))

def test_workspace_paths_skills_inputs_and_limits(tmp_path,monkeypatch):
    from app.sdk_workspace import Workspace,attachments,current
    aid,cid=conversation();token=attachments.set([{'id':1,'name':'vendas.csv','data':'','extracted':'produto,quantidade,preco\nMaçã,10,2.50'}])
    try:
        with Workspace(aid,cid,profile()) as w:
            assert (w.path/'inputs/1-vendas.csv').is_file()
            assert w.allowed('Read',{'file_path':'inputs/1-vendas.csv'})
            assert not w.allowed('Edit',{'file_path':'inputs/1-vendas.csv'})
            assert not w.allowed('Read',{'file_path':str(tmp_path/'outside')})
            assert not w.allowed('Read',{'file_path':'../other'})
            assert not w.allowed('Bash',{'command':'env'})
            assert not w.allowed('Grep',{'path':'.','glob':'../*'})
            assert not w.allowed('Read',{'file_path':'.env'})
            assert w.allowed('Read',{'file_path':'.claude/skills/investigar/SKILL.md'})
            assert not w.allowed('Write',{'file_path':'.claude/skills/investigar/SKILL.md'})
            assert w.allowed('Write',{'file_path':'outputs/test.csv','content':'dados'})
            (w.path/'oversized').write_bytes(b'x'*512001)
            with pytest.raises(RuntimeError):w.capture()
    finally:attachments.reset(token)
    assert current.get() is None and not w.path.exists()

def test_stream_visible_text_and_failure_does_not_resume(monkeypatch):
    from claude_agent_sdk import StreamEvent
    aid,cid=conversation()
    async def respond(client,number):
        await client.options.session_store.append({'session_id':SID},[{'uuid':str(number),'raw':True}])
        yield StreamEvent(uuid=str(uuid.uuid4()),session_id=SID,event={'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'PRIVADO'}})
        yield StreamEvent(uuid=str(uuid.uuid4()),session_id=SID,event={'type':'content_block_delta','delta':{'type':'text_delta','text':'Resposta parcial'}})
        yield result(cost=.001*number,error=number==2)
    sdk(monkeypatch,respond)
    with TestClient(main.app) as client:
        response=client.post(f'/agents/{aid}/conversations/{cid}/messages/stream',json={'content':'Uma tarefa'})
        packets=[json.loads(line) for line in response.text.splitlines()]
        assert packets[0]['type']=='started' and packets[-1]=={'type':'result','text':'Concluído.'}
        assert {'type':'text_delta','text':'Resposta parcial'} in packets and 'PRIVADO' not in response.text
        response=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Falhe agora'})
        assert response.status_code==502
        with db.connect() as c:
            assert c.execute('SELECT session_id FROM sdk_runtime_state WHERE conversation_id=?',(cid,)).fetchone()['session_id']==''
            assert not c.execute('SELECT 1 FROM sdk_session_entries WHERE conversation_id=?',(cid,)).fetchone()
            assert c.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]==2

def test_question_reply_is_bound_to_conversation_and_cancellation(monkeypatch):
    from app.sdk_inputs import ask,reply,Reply,pending
    from app.runtime_events import register,interrupt,unregister,tasks
    aid,cid=conversation()
    with db.connect() as c:rid=db.insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'running')",(aid,cid))
    async def run():
        task=asyncio.create_task(ask(aid,cid,rid,'Qual período?'))
        await asyncio.sleep(.01);key=next(iter(pending))
        with pytest.raises(HTTPException):await reply(aid,cid+1,key,Reply(answer='Hoje'))
        await reply(aid,cid,key,Reply(answer='Hoje'));assert (await task).answer=='Hoje'
        async def wait():
            register(rid)
            try:await ask(aid,cid,rid,'Executar?',approval=True)
            finally:unregister(rid)
        task=asyncio.create_task(wait());await asyncio.sleep(.01);interrupt(rid)
        with pytest.raises(asyncio.CancelledError):await task
        assert not pending and rid not in tasks
    asyncio.run(run())

def test_sdk_files_do_not_cross_agent_or_changed_profile(monkeypatch):
    from app.sdk_workspace import Workspace
    aid,cid=conversation();other,foreign=conversation()
    with Workspace(aid,cid,profile()) as w:
        (w.path/'answer.txt').write_text('private');w.save(SID,.01)
    with db.connect() as c:
        c.execute('INSERT INTO sdk_session_entries(conversation_id,session_id,subpath,seq,entry_uid,entry_json) VALUES(?,?,?,?,?,?)',(cid,SID,'',1,'1','{}'))
    with Workspace(aid,cid,profile().model_copy(update={'instructions':['Mudou']})) as w:assert w.resume is None
    with TestClient(main.app) as client:
        assert client.get(f'/agents/{other}/conversations/{cid}/workspace/file?path=answer.txt').status_code==404
        assert client.get(f'/agents/{aid}/conversations/{cid}/workspace/file?path=../.env').status_code==400

def test_analysis_outputs_are_verified_before_host_import(monkeypatch):
    from app.tools import analysis_sandbox as sandbox,sandbox as executor
    monkeypatch.setenv('ADA_SANDBOX_BACKEND','docker')
    monkeypatch.setattr(executor,'execute_python',lambda args:{'saida':json.dumps({'codigo_saida':0,'saida':'ok','files':{'../../outside':'eA=='}})})
    with pytest.raises(executor.SandboxError):sandbox.execute('print(1)',{})
    monkeypatch.setattr(executor,'execute_python',lambda args:{'saida':json.dumps({'codigo_saida':0,'saida':'ok','files':{'outputs/result.csv':'eA=='}})})
    assert sandbox.execute('print(1)',{})['files']=={'outputs/result.csv':'eA=='}

def test_python_requires_approval_and_only_selected_files(monkeypatch):
    from app.sdk_runtime_tools import runtime_tools
    from app.sdk_workspace import Workspace
    from app import sdk_runtime_tools,sdk_inputs
    from app.model_gateway import active_native
    from app.tools import analysis_sandbox
    aid,cid=conversation();calls=[];approve=False
    with db.connect() as c:rid=db.insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'running')",(aid,cid))
    async def ask(*args,**kwargs):return sdk_inputs.Reply(approved=approve)
    monkeypatch.setattr(sdk_inputs,'ask',ask)
    monkeypatch.setattr(analysis_sandbox,'execute',lambda code,files:(calls.append(files) or {'codigo_saida':0,'saida':'ok','files':{'outputs/result.csv':base64.b64encode(b'total\n25').decode()}}))
    async def run():
        nonlocal approve
        token=active_native.set((rid,{}))
        try:
            with Workspace(aid,cid,profile()) as w:
                (w.path/'selected.csv').write_text('dados');(w.path/'other.txt').write_text('private')
                tool=next(t for t in runtime_tools(aid) if t.name=='executar_python')
                args={'codigo':'print(1)','arquivos':['selected.csv']}
                await tool.handler(args);assert calls==[]
                approve=True;await tool.handler(args)
                assert list(calls[0])==['selected.csv'] and (w.path/'outputs/result.csv').read_bytes()==b'total\n25'
                with pytest.raises(ValueError):await tool.handler({'codigo':'print(1)','arquivos':['../outside']})
        finally:active_native.reset(token)
    asyncio.run(run())

def test_stop_endpoint_interrupts_the_running_sdk_task(monkeypatch):
    from app.runtime_events import tasks
    aid,cid=conversation()
    async def respond(client,number):
        await asyncio.sleep(60)
        yield result()
    sdk(monkeypatch,respond)
    async def run():
        from app.infrastructure import cancel_run
        task=asyncio.create_task(main.send_message(aid,cid,main.ChatRequest(content='Uma tarefa longa')))
        for _ in range(100):
            if tasks:break
            await asyncio.sleep(.01)
        assert tasks
        rid=next(iter(tasks));await cancel_run(rid)
        with pytest.raises(HTTPException) as error:await task
        assert error.value.status_code==499
        with db.connect() as c:assert c.execute('SELECT status FROM runs WHERE id=?',(rid,)).fetchone()['status']=='cancelled'
        assert not tasks
    asyncio.run(run())

def test_session_mirror_failure_prevents_success(monkeypatch):
    aid,cid=conversation()
    async def respond(client,number):
        client.options.session_store.failed=True
        yield result()
    sdk(monkeypatch,respond)
    with TestClient(main.app) as client:
        response=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Uma tarefa'})
        assert response.status_code==502
        assert client.get(f'/agents/{aid}/conversations/{cid}/messages').json()==[]

def test_answers_become_part_of_the_committed_chat(monkeypatch):
    from app.sdk_inputs import ask,reply,Reply,pending
    aid,cid=conversation()
    async def respond(client,number):
        with db.connect() as c:rid=c.execute('SELECT id FROM runs WHERE conversation_id=?',(cid,)).fetchone()['id']
        question=asyncio.create_task(ask(aid,cid,rid,'Qual período?'))
        await asyncio.sleep(.01);key=next(iter(pending))
        await reply(aid,cid,key,Reply(answer='O ano de 2025.'));await question
        yield result()
    sdk(monkeypatch,respond)
    with TestClient(main.app) as client:
        response=client.post(f'/agents/{aid}/conversations/{cid}/messages',json={'content':'Analise os dados.'})
        assert response.status_code==200,response.text
        assert response.json()['interactions']==[{'role':'assistant','content':'Qual período?'},{'role':'user','content':'O ano de 2025.'}]
        assert [m['content'] for m in client.get(f'/agents/{aid}/conversations/{cid}/messages').json()]==['Analise os dados.','Qual período?','O ano de 2025.','Concluído.']

def test_restart_does_not_resume_an_uncommitted_sdk_turn():
    from app.sdk_workspace import Workspace
    aid,cid=conversation()
    with Workspace(aid,cid,profile()) as w:w.save(SID,.01)
    with db.connect() as c:rid=db.insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'running')",(aid,cid))
    with TestClient(main.app) as client:
        assert client.get(f'/runs/{rid}').json()['status']=='failed'
        with db.connect() as c:assert c.execute('SELECT session_id FROM sdk_runtime_state WHERE conversation_id=?',(cid,)).fetchone()['session_id']==''

def test_guest_runner_processes_a_controlled_csv_fixture(tmp_path):
    # This is our fixed test program, never uploaded or model-generated code.
    import subprocess,sys
    from app.tools.analysis_sandbox import RUNNER
    payload=tmp_path/'input.json'
    code="import csv; from decimal import Decimal; from pathlib import Path\nwith open('inputs/vendas.csv') as f: total=sum(Decimal(r['quantidade'])*Decimal(r['preco']) for r in csv.DictReader(f))\nPath('outputs/total.csv').write_text('total\\n'+str(total))\nprint(total)"
    payload.write_text(json.dumps({'code':code,'files':{'inputs/vendas.csv':base64.b64encode(b'produto,quantidade,preco\nMaca,10,2.50\nBanana,2,1.80\n').decode()}}))
    proc=subprocess.run([sys.executable,str(RUNNER),str(payload)],capture_output=True,text=True,timeout=12)
    assert proc.returncode==0,proc.stderr
    result=json.loads(proc.stdout)
    assert result['codigo_saida']==0 and result['saida'].strip()=='28.60'
    assert base64.b64decode(result['files']['outputs/total.csv']).decode().splitlines()==['total','28.60']
