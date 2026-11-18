"""Reusable goal-driven workflows. The existing agent runtime plans each execution."""
import asyncio,time,logging
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from .db import connect,begin_write,insert_id
from . import repo
router=APIRouter()
class Definition(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=2,max_length=80)
    objective:str=Field(min_length=5,max_length=8000)
class Recurrence(BaseModel):
    enabled:bool
    interval_hours:int=Field(default=24,ge=1,le=168)
    max_daily_runs:int=Field(default=1,ge=1,le=5)
def ensure_agent(aid):
    try:repo.get_active_spec(aid)
    except KeyError:raise HTTPException(404,'Agente não encontrado.')
def get_workflow(c,aid,wid):
    row=c.execute('SELECT * FROM workflows WHERE id=? AND agent_id=?',(wid,aid)).fetchone()
    if not row:raise HTTPException(404,'Workflow não encontrado.')
    return row
@router.get('/agents/{aid}/workflows')
def list_workflows(aid:int):
    ensure_agent(aid)
    with connect() as c:return [dict(r) for r in c.execute('SELECT w.*,v.name,v.objective FROM workflows w JOIN workflow_versions v ON v.workflow_id=w.id AND v.number=w.active_version WHERE w.agent_id=? ORDER BY w.id DESC',(aid,))]
@router.post('/agents/{aid}/workflows')
def create(aid:int,body:Definition):
    ensure_agent(aid)
    with connect() as c:
        begin_write(c)
        if c.execute('SELECT COUNT(*) FROM workflows WHERE agent_id=?',(aid,)).fetchone()[0]>=20:raise HTTPException(409,'Limite de workflows atingido.')
        wid=insert_id(c,'INSERT INTO workflows(agent_id) VALUES(?)',(aid,))
        c.execute('INSERT INTO workflow_versions(workflow_id,number,name,objective) VALUES(?,1,?,?)',(wid,body.name,body.objective))
    return {'id':wid,'enabled':False,'version':1}
@router.put('/agents/{aid}/workflows/{wid}')
def edit(aid:int,wid:int,body:Definition):
    with connect() as c:
        begin_write(c);row=get_workflow(c,aid,wid)
        if row['status']=='running':raise HTTPException(409,'Workflow em execução.')
        version=row['active_version']+1
        c.execute('INSERT INTO workflow_versions(workflow_id,number,name,objective) VALUES(?,?,?,?)',(wid,version,body.name,body.objective))
        c.execute('UPDATE workflows SET active_version=?,enabled=0 WHERE id=?',(version,wid))
    return {'version':version,'enabled':False}
@router.get('/agents/{aid}/workflows/{wid}/versions')
def versions(aid:int,wid:int):
    with connect() as c:
        get_workflow(c,aid,wid)
        return [dict(r) for r in c.execute('SELECT * FROM workflow_versions WHERE workflow_id=? ORDER BY number DESC',(wid,))]
@router.patch('/agents/{aid}/workflows/{wid}/recurrence')
def recurrence(aid:int,wid:int,body:Recurrence):
    with connect() as c:
        begin_write(c);row=get_workflow(c,aid,wid)
        if row['status']=='running':raise HTTPException(409,'Workflow em execução.')
        c.execute('UPDATE workflows SET enabled=?,interval_hours=?,max_daily_runs=?,next_at=? WHERE id=?',(int(body.enabled),body.interval_hours,body.max_daily_runs,int(time.time())+body.interval_hours*3600,wid))
    return {'enabled':body.enabled}
@router.get('/agents/{aid}/workflows/{wid}/runs')
def runs(aid:int,wid:int):
    with connect() as c:
        get_workflow(c,aid,wid)
        return [dict(r) for r in c.execute('SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY id DESC LIMIT 20',(wid,))]
@router.delete('/agents/{aid}/workflows/{wid}')
def remove(aid:int,wid:int):
    with connect() as c:
        begin_write(c);row=get_workflow(c,aid,wid)
        if row['status']=='running':raise HTTPException(409,'Workflow em execução.')
        c.execute('DELETE FROM workflow_runs WHERE workflow_id=?',(wid,));c.execute('DELETE FROM workflow_versions WHERE workflow_id=?',(wid,));c.execute('DELETE FROM workflows WHERE id=?',(wid,))
    return {'deleted':True}
@router.post('/agents/{aid}/workflows/{wid}/run')
async def execute(aid:int,wid:int):
    from . import main
    from .model_gateway import ClaudeAgentGateway
    if not isinstance(main.gateway,ClaudeAgentGateway):raise HTTPException(503,'A execução de workflows requer o Claude Agent SDK configurado.')
    now=int(time.time())
    with connect() as c:
        begin_write(c);row=get_workflow(c,aid,wid)
        if row['status']=='running':raise HTTPException(409,'Workflow já está em execução.')
        used=c.execute("SELECT COUNT(*) FROM workflow_runs WHERE workflow_id=? AND created_at>=? AND status<>'deferred'",(wid,now-86400)).fetchone()[0]
        if used>=row['max_daily_runs']:raise HTTPException(429,'Limite diário de execuções atingido.')
        definition=c.execute('SELECT * FROM workflow_versions WHERE workflow_id=? AND number=?',(wid,row['active_version'])).fetchone()
        c.execute("UPDATE workflows SET status='running',next_at=? WHERE id=?",(now+row['interval_hours']*3600,wid))
        cid=insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(aid,))
        c.execute('INSERT INTO conversation_labels(conversation_id,title) VALUES(?,?)',(cid,'Workflow · '+definition['name']))
        rid=insert_id(c,"INSERT INTO workflow_runs(workflow_id,version,conversation_id,status,created_at) VALUES(?,?,?,'running',?)",(wid,row['active_version'],cid,now))
    try:
        response=await main.send_message(aid,cid,main.ChatRequest(content=definition['objective']))
        with connect() as c:c.execute("UPDATE workflow_runs SET status='completed',result=? WHERE id=?",(response['text'],rid))
        return {'id':rid,'status':'completed','conversation_id':cid,'text':response['text']}
    except BaseException as error:
        busy=isinstance(error,HTTPException) and error.status_code==409
        with connect() as c:
            c.execute('UPDATE workflow_runs SET status=?,error=? WHERE id=?',('deferred' if busy else 'failed','Executor ocupado.' if busy else 'Execução interrompida ou não concluída; confira modelo, recursos e orçamento.',rid))
            if busy:c.execute('UPDATE workflows SET next_at=? WHERE id=?',(now+60,wid))
            else:c.execute('UPDATE workflows SET enabled=0 WHERE id=?',(wid,))
        if isinstance(error,asyncio.CancelledError):raise
        if busy:raise HTTPException(409,'Executor ocupado; tente novamente.')
        raise HTTPException(502,'Workflow não concluído. A recorrência foi pausada; consulte o histórico.')
    finally:
        with connect() as c:c.execute("UPDATE workflows SET status='idle' WHERE id=?",(wid,))
async def scheduler_tick():
    with connect() as c:rows=c.execute("SELECT id,agent_id FROM workflows WHERE enabled=1 AND status='idle' AND next_at<=? ORDER BY next_at LIMIT 1",(int(time.time()),)).fetchall()
    for row in rows:
        try:await execute(row['agent_id'],row['id'])
        except HTTPException as error:
            with connect() as c:
                if error.status_code==429:c.execute('UPDATE workflows SET next_at=? WHERE id=?',(int(time.time())+3600,row['id']))
                elif error.status_code not in (409,):c.execute('UPDATE workflows SET enabled=0 WHERE id=?',(row['id'],))
async def scheduler_loop():
    while True:
        await asyncio.sleep(30)
        try:await scheduler_tick()
        except Exception:logging.getLogger(__name__).warning('Agendador de workflows não concluiu a verificação.')

def recover_interrupted():
    with connect() as c:
        c.execute("UPDATE workflow_runs SET status='failed',error='Backend reiniciado durante a execução.' WHERE status='running'")
        c.execute("UPDATE workflows SET status='idle',enabled=0 WHERE status='running'")
