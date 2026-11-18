import asyncio
from .task_runtime import task_limited
from contextlib import asynccontextmanager
import logging

class RedactAccessQueries(logging.Filter):
    def filter(self, record):
        # OAuth authorization codes and state arrive in the callback query string.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str): args[2] = args[2].split('?', 1)[0]
            record.args = tuple(args)
        return True

logging.getLogger('uvicorn.access').addFilter(RedactAccessQueries())
from typing import Literal
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from .db import init_db, connect, insert_id
from .models import AgentSpec
from . import repo
from .model_gateway import create_gateway, generate_structured, StructuredOutputError
from .prompts.create import SYSTEM
from .prompts.behavior import BEHAVIOR

@asynccontextmanager
async def lifespan(app):
    import asyncio
    from contextlib import suppress
    from .reviews import scheduler_loop
    from .workflows import scheduler_loop as workflow_loop
    init_db()
    from .workflows import recover_interrupted
    recover_interrupted()
    workflow_scheduler=asyncio.create_task(workflow_loop())
    scheduler=asyncio.create_task(scheduler_loop())
    try:yield
    finally:
        workflow_scheduler.cancel()
        with suppress(asyncio.CancelledError):await workflow_scheduler
        scheduler.cancel()
        with suppress(asyncio.CancelledError):await scheduler
app=FastAPI(lifespan=lifespan)
from .security import LocalBoundary
app.add_middleware(LocalBoundary)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],allow_methods=['*'],allow_headers=['*'])
from .infrastructure import router as infrastructure_router
app.include_router(infrastructure_router)
from .orchestration import router as orchestration_router
app.include_router(orchestration_router)
from .cookbooks import router as cookbooks_router
app.include_router(cookbooks_router)
from .exports import router as exports_router
app.include_router(exports_router)
from .connections import router as connections_router
app.include_router(connections_router)
from .google_workspace import router as google_workspace_router
app.include_router(google_workspace_router)
from .repository import router as repository_router
app.include_router(repository_router)
from .code_workflow import router as code_workflow_router
app.include_router(code_workflow_router)
from .sandbox_api import router as sandbox_router
app.include_router(sandbox_router)
from .notes import router as notes_router, memory_context, auto_remember
app.include_router(notes_router)
from .resources import router as resources_router
from .reviews import router as reviews_router
app.include_router(resources_router)
app.include_router(reviews_router)
from .workflows import router as workflows_router
app.include_router(workflows_router)
from .attachments import router as attachments_router
app.include_router(attachments_router)
gateway=create_gateway()
def model_http_error(error:httpx.HTTPStatusError):
    if error.response.status_code==429:
        return HTTPException(429,'O provedor informou limite de uso ou cota atingida. Aguarde e tente novamente; se persistir, confira a cota do projeto no AI Studio.')
    return HTTPException(502,'O provedor recusou a solicitação. Confira o modelo e a configuração e tente novamente.')
class Message(BaseModel):
    role: Literal['user','assistant']
    content: str = Field(min_length=1,max_length=12000)
class CreateIdentity(BaseModel):
    name: str = Field(min_length=2,max_length=40)
    avatar: Literal['flower','mouse','toucan','hedgehog','bear','crab']
    color: Literal['amber','lilac','violet','sage']
class CreateRequest(BaseModel):
    direct: bool = False
    identity: CreateIdentity | None = None
    messages: list[Message] = Field(min_length=1,max_length=30)
class CreateResult(BaseModel):
    type: Literal['question','proposal']
    text: str | None = None
    spec: AgentSpec | None = None
    rationale: str | None = None
    @model_validator(mode='before')
    @classmethod
    def guard_write(cls, data):
        if isinstance(data,dict) and isinstance(data.get('spec'),dict):
            policy=data['spec'].get('autonomy_policy')
            if isinstance(policy,dict) and policy.get('salvar_relatorio') == 'allowed': policy['salvar_relatorio']='approval_required'
        return data
    @model_validator(mode='after')
    def check_result(self):
        if self.type=='question' and not self.text: raise ValueError('Pergunta sem texto')
        if self.type=='proposal' and not self.spec: raise ValueError('Proposta sem especificação')
        if self.type=='proposal' and not self.rationale:
            self.rationale='Proposta preparada a partir do objetivo descrito na conversa.'
        return self
class ChatRequest(BaseModel):
    attachment_ids: list[int] = Field(default_factory=list,max_length=3)
    content: str = Field(min_length=1,max_length=12000)
@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/installation')
def installation():
    import os
    return {'mode':os.getenv('ADA_DEPLOYMENT_MODE','local')}
@app.get('/agents')
def agents(): return repo.list_agents()
@app.post('/agents')
def create_agent(spec: AgentSpec):
    for capability in ('mcp_documentos','mcp_paginas','mcp_memoria','pesquisar_web'):
        if capability not in spec.autonomy_policy:
            spec.autonomy_policy[capability]='allowed'
        if spec.autonomy_policy[capability]=='allowed' and capability not in spec.tools:
            spec.tools.append(capability)
    return {'id':repo.create_agent(spec)}
@app.post('/create/messages')
@task_limited
async def create_messages(body: CreateRequest):
    questions=sum(1 for m in body.messages if m.role=='assistant')
    identity_prompt=(' Identidade escolhida pelo usuário: '+body.identity.model_dump_json()+'. Esta etapa já foi concluída. Não pergunte nome, personagem ou cor; preserve a identidade na proposta e converse apenas sobre o trabalho.') if body.identity else ''
    messages=[{'role':'system','content':SYSTEM + identity_prompt + (' Prepare uma proposta diretamente com o objetivo e o contexto disponíveis; não faça perguntas nesta etapa.' if body.direct else (' Já fez três perguntas: retorne obrigatoriamente uma proposta.' if questions>=3 else ''))}]+[m.model_dump() for m in body.messages]
    try:
        result=await generate_structured(gateway,messages,CreateResult)
        if (questions>=3 or body.direct) and result.type=='question':
            messages.append({'role':'user','content':'O limite de perguntas foi atingido. Gere uma proposta agora.'})
            result=await generate_structured(gateway,messages,CreateResult)
            if result.type!='proposal': raise StructuredOutputError('O modelo ultrapassou o limite de perguntas.')
        if result.spec and body.identity:
            result.spec.name=body.identity.name
            result.spec.style.avatar=body.identity.avatar
            result.spec.style.color=body.identity.color
        return result
    except httpx.HTTPStatusError as error:
        raise model_http_error(error)
    except httpx.RemoteProtocolError:
        raise HTTPException(502,'A conexão com o provedor foi interrompida. Sua mensagem foi preservada; tente enviar novamente.')
    except StructuredOutputError:
        raise HTTPException(502,'A resposta do modelo não veio no formato necessário para criar o agente. Sua conversa foi preservada; tente novamente.')
    except (httpx.HTTPError,RuntimeError,StructuredOutputError,ValueError,KeyError) as e: raise HTTPException(502,detail=f'Não foi possível gerar a proposta: {type(e).__name__}. Verifique o modelo e tente novamente.')
@app.get('/agents/{agent_id}/conversations')
def conversations(agent_id:int):
    try: repo.get_active_spec(agent_id)
    except KeyError: raise HTTPException(404,'Agente não encontrado')
    with connect() as c: return [dict(r) for r in c.execute("SELECT conversations.*, COALESCE((SELECT title FROM conversation_labels WHERE conversation_id=conversations.id),(SELECT substr(content,1,60) FROM messages WHERE conversation_id=conversations.id AND role='user' ORDER BY id LIMIT 1)) AS title FROM conversations WHERE agent_id=? ORDER BY id",(agent_id,))]
@app.post('/agents/{agent_id}/conversations')
def new_conversation(agent_id:int):
    try: repo.get_active_spec(agent_id)
    except KeyError: raise HTTPException(404,'Agente não encontrado')
    with connect() as c: return {'id':insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(agent_id,))}
class ConversationLabel(BaseModel):
    title: str = Field(min_length=1,max_length=80)

@app.patch('/agents/{agent_id}/conversations/{cid}')
def rename_conversation(agent_id:int,cid:int,body:ConversationLabel):
    title=body.title.strip()
    if not title: raise HTTPException(422,'Informe um título.')
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,agent_id)).fetchone(): raise HTTPException(404,'Conversa não encontrada')
        c.execute('INSERT INTO conversation_labels(conversation_id,title) VALUES(?,?) ON CONFLICT (conversation_id) DO UPDATE SET title=excluded.title',(cid,title))
    return {'id':cid,'title':title}

@app.post('/agents/{agent_id}/conversations/{cid}/fork')
def fork_conversation(agent_id:int,cid:int):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,agent_id)).fetchone(): raise HTTPException(404,'Conversa não encontrada')
        rows=c.execute('SELECT id,role,content FROM messages WHERE conversation_id=? ORDER BY id',(cid,)).fetchall()
        new_id=insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(agent_id,))
        if rows: c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(new_id,r['role'],r['content']) for r in rows])
        cloned=c.execute('SELECT id FROM messages WHERE conversation_id=? ORDER BY id',(new_id,)).fetchall()
        for old,new in zip(rows,cloned):
            c.execute('INSERT INTO chat_attachments(conversation_id,message_id,name,mime,data,extracted,created_at) SELECT ?,?,name,mime,data,extracted,created_at FROM chat_attachments WHERE conversation_id=? AND message_id=?',(new_id,new['id'],cid,old['id']))
        c.execute('INSERT INTO conversation_labels(conversation_id,title) VALUES(?,?)',(new_id,'Cópia da conversa'))
    return {'id':new_id,'title':'Cópia da conversa'}

@app.get('/agents/{agent_id}/conversations/{cid}/messages')
def get_messages(agent_id:int,cid:int):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,agent_id)).fetchone(): raise HTTPException(404,'Conversa não encontrada')
        return [dict(r) for r in c.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id',(cid,))]
@app.post('/agents/{agent_id}/conversations/{cid}/messages')
@task_limited
async def send_message(agent_id:int,cid:int,body:ChatRequest):
    try: spec=repo.get_active_spec(agent_id)
    except KeyError: raise HTTPException(404,'Agente não encontrado')
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,agent_id)).fetchone(): raise HTTPException(404,'Conversa não encontrada')
        previous=[]
    from .attachments import prepare,context as attachment_context,images_for_conversation,save_turn
    items=prepare(agent_id,cid,body.attachment_ids)
    task_content=body.content+attachment_context(items)
    from .exports import requested_export
    exported=requested_export(agent_id,cid,body.content) if not items else None
    if exported:
        with connect() as c:
            c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(cid,'user',body.content),(cid,'assistant',exported)])
        return {'text':exported}
    system=memory_context(agent_id,body.content)+'\nVocê é '+spec.name+'. Objetivo: '+spec.purpose+'\nInstruções: '+ '\n'.join(spec.instructions)+'\n'+BEHAVIOR+'\nNesta etapa você não possui ferramentas disponíveis. Não afirme ter acessado fontes ou executado ações.'
    from .model_gateway import ClaudeAgentGateway, TaskBudget, active_budget
    from .config import TASK_BUDGET_USD
    from .orchestration import orchestrate, record_event
    from .infrastructure import emit
    if isinstance(gateway, ClaudeAgentGateway):
        if not (0 < TASK_BUDGET_USD <= 5): raise HTTPException(503,'Orçamento da tarefa inválido.')
        budget=TaskBudget(limit=TASK_BUDGET_USD)
        with connect() as c:
            run_id=insert_id(c,"INSERT INTO runs(agent_id,conversation_id,status) VALUES(?,?,'running')",(agent_id,cid))
            emit(c,run_id,'started',{'budget_usd':budget.limit})
        token=active_budget.set(budget)
        from .conversation_tools import chat_artifacts
        artifacts_token=chat_artifacts.set([])
        try:
            from .conversation_context import build_context
            previous=await build_context(gateway,agent_id,cid,body.content)
            from .model_gateway import active_images
            active_images.set(images_for_conversation(cid,items))
            answer=await orchestrate(gateway,spec,previous,task_content,run_id)
            with connect() as state_conn:
                state=state_conn.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()['status']
            if state!='running':raise RuntimeError('Execução cancelada.')
            active_images.set([])
            await auto_remember(gateway,agent_id,cid,body.content)
            with connect() as c:
                from .db import begin_write
                begin_write(c)
                state=c.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()['status']
                if state != 'running': raise RuntimeError('Execução cancelada.')
                save_turn(c,cid,body.content,answer,items)
                c.execute("UPDATE runs SET status='completed',steps=?,tokens_in=?,tokens_out=?,cost_estimate=? WHERE id=?",(budget.calls,budget.input_tokens,budget.output_tokens,budget.spent if budget.cost_known else None,run_id))
                emit(c,run_id,'completed',{'cost_usd':budget.spent,'calls':budget.calls})
            return {'text':answer}
        except asyncio.CancelledError:
            with connect() as c:
                from .db import begin_write
                begin_write(c)
                state=c.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()
                if state and state['status']=='running':
                    c.execute("UPDATE runs SET status='cancelled',cost_estimate=NULL WHERE id=?",(run_id,))
                    emit(c,run_id,'cancelled',{'reason':'Execução interrompida; custo final pode ser desconhecido.'})
            raise
        except (httpx.HTTPError,RuntimeError,StructuredOutputError,ValueError,KeyError) as e:
            with connect() as c:
                from .db import begin_write
                begin_write(c)
                state=c.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()['status']
                c.execute('UPDATE runs SET steps=?,tokens_in=?,tokens_out=?,cost_estimate=? WHERE id=?',(budget.calls,budget.input_tokens,budget.output_tokens,budget.spent if budget.cost_known else None,run_id))
                if state=='running':
                    c.execute("UPDATE runs SET status='failed' WHERE id=?",(run_id,))
                    emit(c,run_id,'failed',{'reason':'Não foi possível concluir a tarefa.','cost_usd':budget.spent if budget.cost_known else None})
            raise HTTPException(502,detail='Não foi possível concluir a tarefa. Confira a configuração, os dados disponíveis e o orçamento.')
        finally:
            active_budget.reset(token);chat_artifacts.reset(artifacts_token)
    try:
        from .conversation_context import build_context
        previous=await build_context(gateway,agent_id,cid,body.content)
        from .model_gateway import active_images
        active_images.set(images_for_conversation(cid,items))
        answer=(await gateway.chat([{'role':'system','content':system}]+previous+[{'role':'user','content':task_content}])).text
    except httpx.HTTPStatusError as error: raise model_http_error(error)
    except (httpx.HTTPError,RuntimeError,ValueError,KeyError) as e: raise HTTPException(502,detail=f'O modelo não respondeu ({type(e).__name__}). Confira a configuração e tente novamente.')
    if not answer.strip(): raise HTTPException(502,'O modelo devolveu uma resposta vazia. Tente novamente.')
    with connect() as c:
        save_turn(c,cid,body.content,answer,items)
    from .model_gateway import active_images
    active_images.set([])
    await auto_remember(gateway,agent_id,cid,body.content)
    return {'text':answer}


@app.delete('/agents/{agent_id}/conversations/{cid}')
@task_limited
async def delete_conversation(agent_id:int,cid:int):
    with connect() as c:
        from .db import begin_write
        begin_write(c)
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,agent_id)).fetchone():raise HTTPException(404,'Conversa não encontrada.')
        if c.execute("SELECT 1 FROM runs WHERE conversation_id=? AND status='running'",(cid,)).fetchone():raise HTTPException(409,'Aguarde o término da tarefa antes de excluir a conversa.')
        c.execute('DELETE FROM approvals WHERE run_id IN (SELECT id FROM runs WHERE conversation_id=?)',(cid,))
        c.execute('DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE conversation_id=?)',(cid,))
        c.execute('DELETE FROM runs WHERE conversation_id=?',(cid,))
        c.execute('DELETE FROM note_events WHERE note_id IN (SELECT id FROM agent_notes WHERE conversation_id=?)',(cid,))
        c.execute('DELETE FROM agent_notes WHERE conversation_id=?',(cid,))
        if c.execute("SELECT 1 FROM workflow_runs WHERE conversation_id=? AND status='running'",(cid,)).fetchone():raise HTTPException(409,'Workflow em execução.')
        c.execute('DELETE FROM workflow_runs WHERE conversation_id=?',(cid,))
        c.execute('DELETE FROM conversation_summaries WHERE conversation_id=?',(cid,))
        c.execute('DELETE FROM chat_attachments WHERE conversation_id=?',(cid,))
        c.execute('DELETE FROM messages WHERE conversation_id=?',(cid,))
        c.execute('DELETE FROM conversation_labels WHERE conversation_id=?',(cid,))
        c.execute('DELETE FROM conversations WHERE id=?',(cid,))
    from . import repository
    for key,draft in list(repository._drafts.items()):
        if draft.get('agent_id')==agent_id and draft.get('conversation_id')==cid:
            repository._drafts.pop(key,None);repository._locks.pop(key,None)
    return {'deleted':True}

@app.delete('/agents/{agent_id}')
@task_limited
async def delete_agent(agent_id:int):
    with connect() as c:
        from .db import begin_write
        begin_write(c)
        if not c.execute('SELECT 1 FROM agents WHERE id=?',(agent_id,)).fetchone():raise HTTPException(404,'Agente não encontrado.')
        if c.execute("SELECT 1 FROM runs WHERE agent_id=? AND status='running'",(agent_id,)).fetchone() or c.execute("SELECT 1 FROM review_schedules WHERE agent_id=? AND status='running'",(agent_id,)).fetchone():raise HTTPException(409,'Aguarde o término das tarefas deste agente.')
        if c.execute("SELECT 1 FROM workflows WHERE agent_id=? AND status='running'",(agent_id,)).fetchone():raise HTTPException(409,'Workflow em execução.')
        c.execute('DELETE FROM workflow_runs WHERE workflow_id IN (SELECT id FROM workflows WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM workflow_versions WHERE workflow_id IN (SELECT id FROM workflows WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM workflows WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM approvals WHERE run_id IN (SELECT id FROM runs WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM runs WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM repository_reviews WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM review_schedules WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM note_events WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM agent_notes WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM agent_memory_settings WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM conversation_summaries WHERE conversation_id IN (SELECT id FROM conversations WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM chat_attachments WHERE conversation_id IN (SELECT id FROM conversations WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM conversation_labels WHERE conversation_id IN (SELECT id FROM conversations WHERE agent_id=?)',(agent_id,))
        c.execute('DELETE FROM conversations WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM chunks WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM documents WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM agent_versions WHERE agent_id=?',(agent_id,))
        c.execute('DELETE FROM agents WHERE id=?',(agent_id,))
    from . import repository
    for key,draft in list(repository._drafts.items()):
        if draft.get('agent_id')==agent_id:
            repository._drafts.pop(key,None);repository._locks.pop(key,None)
    return {'deleted':True}

# Static production frontend is mounted last so API routes take precedence.
import os
if os.getenv('ADA_DEPLOYMENT_MODE')=='private_cloud':
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    frontend_dist=Path(__file__).resolve().parents[2]/'frontend'/'dist'
    if frontend_dist.is_dir():app.mount('/',StaticFiles(directory=frontend_dist,html=True),name='frontend')
