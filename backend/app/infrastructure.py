"""Persistent infrastructure for runs, tools, approvals and agent knowledge."""
import asyncio, json, re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .db import connect, begin_write, insert_id
from . import repo
from .models import AgentSpec
from .tools.registry import REGISTRY, ToolError, read_url, save_report, search_documents
from .tools.sandbox import execute_python

router=APIRouter()
class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class VersionBody(Strict):
    spec: AgentSpec
    reason: str = Field(min_length=1,max_length=1000)
class Decision(Strict): decision: str
class ToolRequest(Strict):
    tool: str
    arguments: dict
class RunRequest(Strict): conversation_id:int

def emit(c,run_id,kind,payload):
    seq=c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM run_events WHERE run_id=?',(run_id,)).fetchone()[0]
    c.execute('INSERT INTO run_events(run_id,seq,type,payload_json) VALUES(?,?,?,?)',(run_id,seq,kind,json.dumps(payload,ensure_ascii=False)))

def find_run(c,id):
    row=c.execute('SELECT * FROM runs WHERE id=?',(id,)).fetchone()
    if not row: raise HTTPException(404,'Execução não encontrada.')
    return row

def active_spec(agent_id):
    try: return repo.get_active_spec(agent_id)
    except KeyError: raise HTTPException(404,'Agente não encontrado.')

@router.get('/agents/{agent_id}')
def agent_details(agent_id:int):
    spec=active_spec(agent_id)
    with connect() as c:
        agent=c.execute('SELECT id,active_version,created_at FROM agents WHERE id=?',(agent_id,)).fetchone()
    return {'id':agent_id,'active_version':agent['active_version'],'created_at':agent['created_at'],'spec':spec.model_dump()}

@router.get('/agents/{agent_id}/versions')
def versions(agent_id:int):
    active_spec(agent_id)
    with connect() as c: rows=c.execute('SELECT number,spec_json,reason,origin,created_at FROM agent_versions WHERE agent_id=? ORDER BY number DESC',(agent_id,)).fetchall()
    return [dict(number=r['number'],spec=json.loads(r['spec_json']),reason=r['reason'],origin=r['origin'],created_at=r['created_at']) for r in rows]

@router.post('/agents/{agent_id}/versions')
def create_version(agent_id:int,body:VersionBody):
    active_spec(agent_id)
    n=repo.add_version(agent_id,body.spec,body.reason,'feedback do usuário')
    return {'number':n,'activated':False}

@router.post('/agents/{agent_id}/versions/{number}/activate')
def activate(agent_id:int,number:int):
    try: repo.activate_version(agent_id,number)
    except KeyError: raise HTTPException(404,'Versão não encontrada.')
    return {'active_version':number}

@router.post('/agents/{agent_id}/versions/diff')
def diff(agent_id:int,body:VersionBody):
    old=active_spec(agent_id); changes=repo.diff_specs(old,body.spec)
    for change in changes: change['permission_change']=change['field'] in ('tools','autonomy_policy')
    return {'diff':changes}

@router.get('/agents/{agent_id}/documents')
def documents(agent_id:int):
    active_spec(agent_id)
    with connect() as c: rows=c.execute('SELECT id,name,created_at FROM documents WHERE agent_id=? ORDER BY id DESC',(agent_id,)).fetchall()
    return [dict(r) for r in rows]

@router.post('/agents/{agent_id}/documents')
async def upload_document(agent_id:int,request:Request,name:str):
    active_spec(agent_id)
    name=Path(name.replace('\\','/')).name
    if not name or len(name)>200 or not name.lower().endswith(('.md','.txt','.pdf')): raise HTTPException(400,'Envie um arquivo .md, .txt ou .pdf.')
    length=request.headers.get('content-length')
    try:
        if length and int(length)>5_000_000: raise HTTPException(413,'Documento maior que 5 MB.')
    except ValueError: raise HTTPException(400,'Tamanho inválido.')
    buf=bytearray()
    async for part in request.stream():
        buf.extend(part)
        if len(buf)>5_000_000: raise HTTPException(413,'Documento maior que 5 MB.')
    try:
        if name.lower().endswith('.pdf'):
            from pypdf import PdfReader
            from io import BytesIO
            reader=PdfReader(BytesIO(buf)); text='\n'.join((page.extract_text() or '') for page in reader.pages)
        else: text=buf.decode('utf-8-sig')
    except ImportError: raise HTTPException(503,'Leitura de PDF precisa da biblioteca pypdf.')
    except Exception: raise HTTPException(400,'Não foi possível ler o documento.')
    if not text.strip(): raise HTTPException(400,'O documento não contém texto legível.')
    if len(text)>2_000_000: raise HTTPException(413,'Texto extraído é grande demais.')
    with connect() as c:
        doc=insert_id(c,'INSERT INTO documents(agent_id,name) VALUES(?,?)',(agent_id,name))
        c.executemany('INSERT INTO chunks(document_id,agent_id,position,content) VALUES(?,?,?,?)',[(doc,agent_id,i,text[i:i+800]) for i in range(0,len(text),700)])
    return {'id':doc,'name':name}

@router.get('/agents/{agent_id}/runs')
def list_runs(agent_id:int):
    active_spec(agent_id)
    with connect() as c: rows=c.execute('SELECT * FROM runs WHERE agent_id=? ORDER BY id DESC LIMIT 10',(agent_id,)).fetchall()
    return [dict(r) for r in rows]

@router.post('/agents/{agent_id}/runs')
def create_run(agent_id:int,body:RunRequest):
    active_spec(agent_id)
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(body.conversation_id,agent_id)).fetchone(): raise HTTPException(404,'Conversa não encontrada.')
        # The agent loop is deliberately deferred until a real model is configured.
        rid=insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'draft')",(agent_id,body.conversation_id))
        emit(c,rid,'created',{'message':'Execução preparada; ciclo do modelo ainda não está conectado.'})
    return {'id':rid,'status':'draft'}

@router.get('/runs/{run_id}')
def get_run(run_id:int):
    with connect() as c:
        run=dict(find_run(c,run_id)); rows=c.execute('SELECT seq,type,payload_json,created_at FROM run_events WHERE run_id=? ORDER BY seq',(run_id,)).fetchall()
        approvals=c.execute('SELECT id,tool,payload_json,status,created_at,decided_at FROM approvals WHERE run_id=? ORDER BY id',(run_id,)).fetchall()
    run['events']=[{'seq':r['seq'],'type':r['type'],'payload':json.loads(r['payload_json']),'created_at':r['created_at']} for r in rows]
    run['approvals']=[{'id':r['id'],'tool':r['tool'],'payload':json.loads(r['payload_json']),'status':r['status'],'created_at':r['created_at'],'decided_at':r['decided_at']} for r in approvals]
    return run

@router.post('/runs/{run_id}/cancel')
def cancel_run(run_id:int):
    with connect() as c:
        begin_write(c)
        run=find_run(c,run_id)
        if run['status'] not in ('draft','running','waiting_approval'): raise HTTPException(409,'A execução já foi encerrada.')
        c.execute("UPDATE runs SET status='cancelled' WHERE id=?",(run_id,)); c.execute("UPDATE approvals SET status='rejected',decided_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='pending'",(run_id,)); emit(c,run_id,'cancelled',{})
    return {'status':'cancelled'}

@router.post('/runs/{run_id}/tools')
async def request_tool(run_id:int,body:ToolRequest):
    with connect() as c: run=find_run(c,run_id)
    if run['status'] not in ('draft','running'): raise HTTPException(409,'Execução indisponível.')
    spec=active_spec(run['agent_id'])
    if body.tool not in REGISTRY or body.tool not in spec.tools: raise HTTPException(403,'Ferramenta não autorizada.')
    policy=spec.autonomy_policy.get(body.tool,'denied')
    if policy=='denied': raise HTTPException(403,'A política nega esta ferramenta.')
    schema,risk=REGISTRY[body.tool]
    try: args=schema.model_validate(body.arguments)
    except ValidationError: raise HTTPException(422,'Argumentos da ferramenta inválidos.')
    if run['steps']>=spec.limits.max_steps: raise HTTPException(409,'Limite de passos atingido.')
    payload=args.model_dump()
    if risk=='external_effect' or policy=='approval_required':
        with connect() as c:
            begin_write(c); current=find_run(c,run_id)
            if current['status'] not in ('draft','running'): raise HTTPException(409,'Execução indisponível.')
            if current['steps']>=spec.limits.max_steps: raise HTTPException(409,'Limite de passos atingido.')
            approval_id=insert_id(c,'INSERT INTO approvals(run_id,tool,payload_json) VALUES(?,?,?)',(run_id,body.tool,json.dumps(payload,ensure_ascii=False)))
            c.execute("UPDATE runs SET status='waiting_approval',steps=steps+1 WHERE id=?",(run_id,)); emit(c,run_id,'approval_requested',{'approval_id':approval_id,'tool':body.tool,'payload':payload})
        return {'status':'waiting_approval','approval_id':approval_id,'payload':payload}
    try:
        result=await asyncio.wait_for(asyncio.to_thread(read_url,args) if body.tool=='ler_url' else asyncio.to_thread(search_documents,args,run['agent_id']),timeout=20)
    except (ToolError, asyncio.TimeoutError) as e: raise HTTPException(400, str(e) or 'Tempo limite da ferramenta atingido.')
    with connect() as c:
        begin_write(c)
        current=find_run(c,run_id)
        if current['status'] not in ('draft','running'): raise HTTPException(409,'Execução indisponível.')
        c.execute('UPDATE runs SET steps=steps+1 WHERE id=?',(run_id,)); emit(c,run_id,'tool_result',{'tool':body.tool,'result':result})
    return {'ok':True,'data':result}

@router.post('/approvals/{approval_id}/decision')
def decide(approval_id:int,body:Decision):
    if body.decision not in ('approve','reject'): raise HTTPException(422,'Decisão inválida.')
    with connect() as c:
        begin_write(c)
        row=c.execute('SELECT a.*,r.agent_id,r.status AS run_status FROM approvals a JOIN runs r ON r.id=a.run_id WHERE a.id=?',(approval_id,)).fetchone()
        if not row: raise HTTPException(404,'Aprovação não encontrada.')
        if row['status']!='pending' or row['run_status']!='waiting_approval': raise HTTPException(409,'Esta aprovação já foi decidida.')
        if body.decision=='reject':
            c.execute("UPDATE approvals SET status='rejected',decided_at=CURRENT_TIMESTAMP WHERE id=?",(approval_id,)); c.execute("UPDATE runs SET status='cancelled' WHERE id=?",(row['run_id'],)); emit(c,row['run_id'],'approval_rejected',{'approval_id':approval_id}); return {'status':'rejected'}
        # Claim exactly once. A crash after claiming is reported as uncertain, never retried automatically.
        c.execute("UPDATE approvals SET status='executing',decided_at=CURRENT_TIMESTAMP WHERE id=?",(approval_id,)); c.execute("UPDATE runs SET status='executing' WHERE id=?",(row['run_id'],)); emit(c,row['run_id'],'approval_granted',{'approval_id':approval_id})
    try:
        spec=active_spec(row['agent_id']); tool=row['tool']; schema,risk=REGISTRY[tool]
        if tool not in spec.tools or spec.autonomy_policy.get(tool)=='denied': raise ToolError('A permissão foi revogada.')
        args=schema.model_validate_json(row['payload_json'])
        if tool=='salvar_relatorio': result=save_report(args)
        elif tool=='executar_codigo': result=execute_python(args)
        elif tool=='ler_url': result=read_url(args)
        else: result=search_documents(args,row['agent_id'])
    except Exception as exc:
        with connect() as c:
            c.execute("UPDATE approvals SET status='failed' WHERE id=?",(approval_id,)); c.execute("UPDATE runs SET status='failed' WHERE id=?",(row['run_id'],)); emit(c,row['run_id'],'tool_error',{'message':str(exc)[:300]})
        raise HTTPException(500,'Não foi possível executar a ação aprovada.')
    with connect() as c:
        c.execute("UPDATE approvals SET status='approved' WHERE id=?",(approval_id,)); c.execute("UPDATE runs SET status='draft' WHERE id=?",(row['run_id'],)); emit(c,row['run_id'],'tool_result',{'tool':tool,'result':result})
    return {'status':'approved','result':result}

@router.get('/runs/{run_id}/events')
async def stream_events(run_id:int,request:Request,after:int=0):
    from fastapi.responses import StreamingResponse
    with connect() as c: find_run(c,run_id)
    async def events():
        cursor=max(0,after)
        while not await request.is_disconnected():
            with connect() as c:
                current=find_run(c,run_id)
                rows=c.execute('SELECT seq,type,payload_json FROM run_events WHERE run_id=? AND seq>? ORDER BY seq',(run_id,cursor)).fetchall()
            for row in rows:
                cursor=row['seq']; data=json.dumps({'seq':row['seq'],'type':row['type'],'payload':json.loads(row['payload_json'])},ensure_ascii=False)
                yield f'id: {cursor}\nevent: {row["type"]}\ndata: {data}\n\n'
            if current['status'] in ('succeeded','failed','cancelled'): break
            yield ': aguardando\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
