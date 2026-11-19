"""NDJSON transport for visible SDK output and auditable progress, never thinking."""
import asyncio,json
from contextlib import suppress
from fastapi import APIRouter,HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
from .runtime_events import sink
from .db import connect

router=APIRouter()
VISIBLE={'started','tool_started','task_plan','subagent_started','subagent_completed',
         'subagent_failed','context_compacted','sdk_session','input_requested','input_resolved'}

class StreamRequest(BaseModel):
    content:str=Field(min_length=1,max_length=12000)
    attachment_ids:list[int]=Field(default_factory=list,max_length=3)

@router.post('/agents/{agent_id}/conversations/{cid}/messages/stream')
async def stream(agent_id:int,cid:int,body:StreamRequest):
    from .sdk_workspace import owns
    from .attachments import prepare
    with connect() as c:owns(c,agent_id,cid)
    prepare(agent_id,cid,body.attachment_ids)
    async def events():
        queue=asyncio.Queue(maxsize=512);loop=asyncio.get_running_loop()
        def enqueue(packet):
            if not queue.full():queue.put_nowait(packet)
        def receive(packet):
            if packet.get('type')=='text_delta' or packet.get('type') in VISIBLE:
                try:local=asyncio.get_running_loop() is loop
                except RuntimeError:local=False
                if local:enqueue(packet)
                else:loop.call_soon_threadsafe(enqueue,packet)
        async def produce():
            from .main import send_message,ChatRequest
            token=sink.set(receive)
            try:
                result=await send_message(agent_id,cid,ChatRequest(**body.model_dump()))
                await queue.put({'type':'result',**result})
            except asyncio.CancelledError:
                enqueue({'type':'cancelled'})
            except HTTPException as exc:
                await queue.put({'type':'error','detail':exc.detail,'status':exc.status_code})
            except Exception:
                await queue.put({'type':'error','detail':'Não foi possível concluir a tarefa.','status':502})
            finally:sink.reset(token)
        task=asyncio.create_task(produce())
        try:
            while True:
                try:packet=await asyncio.wait_for(queue.get(),timeout=15)
                except TimeoutError:packet={'type':'heartbeat'}
                yield json.dumps(packet,ensure_ascii=False)+'\n'
                if packet['type'] in ('result','error','cancelled'):break
        finally:
            if not task.done():task.cancel()
            with suppress(asyncio.CancelledError):await task
    return StreamingResponse(events(),media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
