"""Questions and per-execution approvals while an SDK task is still running."""
import asyncio,secrets
from contextvars import ContextVar
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from .db import connect
from .runtime_events import publish

router=APIRouter();pending={}
answered=ContextVar('runtime_answers',default=None)

class Reply(BaseModel):
    model_config=ConfigDict(extra='forbid')
    answer:str=Field(default='',max_length=4000)
    approved:bool|None=None

async def ask(aid,cid,rid,question,*,approval=False,detail=''):
    key=secrets.token_urlsafe(24);future=asyncio.get_running_loop().create_future()
    item={'id':key,'agent_id':aid,'conversation_id':cid,'run_id':rid,'question':question[:1000],
          'kind':'approval' if approval else 'question','detail':detail[:12000]}
    pending[key]=(item,future)
    from .orchestration import record_event
    try:
        record_event(rid,'input_requested',item)
        result=await asyncio.wait_for(future,timeout=600)
        history=answered.get()
        if history is not None:
            history.extend([{'role':'assistant','content':item['question']},{'role':'user','content':result.answer if not approval else ('Aprovo esta execução na sandbox.' if result.approved else 'Não aprovo esta execução na sandbox.')}])
        record_event(rid,'input_resolved',{'id':key,'kind':item['kind'],'approved':result.approved})
        return result
    finally:
        pending.pop(key,None)

@router.get('/agents/{aid}/conversations/{cid}/runtime')
def state(aid:int,cid:int):
    from .sdk_workspace import owns
    with connect() as c:
        owns(c,aid,cid)
        row=c.execute("SELECT id FROM runs WHERE agent_id=? AND conversation_id=? AND status='running' ORDER BY id DESC LIMIT 1",(aid,cid)).fetchone()
    return {'run_id':row['id'] if row else None,'requests':[i for i,_ in pending.values() if i['agent_id']==aid and i['conversation_id']==cid]}

@router.post('/agents/{aid}/conversations/{cid}/runtime/inputs/{key}')
async def reply(aid:int,cid:int,key:str,body:Reply):
    entry=pending.get(key)
    if not entry or entry[0]['agent_id']!=aid or entry[0]['conversation_id']!=cid:raise HTTPException(404,'Solicitação não encontrada.')
    item,future=entry
    with connect() as c:
        row=c.execute('SELECT status FROM runs WHERE id=?',(item['run_id'],)).fetchone()
    if not row or row['status']!='running' or future.done():raise HTTPException(409,'A solicitação já foi encerrada.')
    if item['kind']=='approval' and body.approved is None:raise HTTPException(422,'Informe aprovar ou rejeitar.')
    if item['kind']=='question' and not body.answer.strip():raise HTTPException(422,'Informe sua resposta.')
    future.set_result(body)
    return {'accepted':True}
