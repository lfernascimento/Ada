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

@asynccontextmanager
async def lifespan(app):
    init_db(); yield
app=FastAPI(lifespan=lifespan)
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
gateway=create_gateway()
class Message(BaseModel):
    role: Literal['user','assistant']
    content: str = Field(min_length=1,max_length=12000)
class CreateRequest(BaseModel):
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
        if self.type=='proposal' and (not self.spec or not self.rationale): raise ValueError('Proposta incompleta')
        return self
class ChatRequest(BaseModel):
    content: str = Field(min_length=1,max_length=12000)
@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/agents')
def agents(): return repo.list_agents()
@app.post('/agents')
def create_agent(spec: AgentSpec): return {'id':repo.create_agent(spec)}
@app.post('/create/messages')
async def create_messages(body: CreateRequest):
    questions=sum(1 for m in body.messages if m.role=='assistant')
    messages=[{'role':'system','content':SYSTEM + (' Já fez três perguntas: retorne obrigatoriamente uma proposta.' if questions>=3 else '')}]+[m.model_dump() for m in body.messages]
    try:
        result=await generate_structured(gateway,messages,CreateResult)
        if questions>=3 and result.type=='question':
            messages.append({'role':'user','content':'O limite de perguntas foi atingido. Gere uma proposta agora.'})
            result=await generate_structured(gateway,messages,CreateResult)
            if result.type!='proposal': raise StructuredOutputError('O modelo ultrapassou o limite de perguntas.')
        return result
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
        rows=c.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id',(cid,)).fetchall()
        new_id=insert_id(c,'INSERT INTO conversations(agent_id) VALUES(?)',(agent_id,))
        if rows: c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(new_id,r['role'],r['content']) for r in rows])
        c.execute('INSERT INTO conversation_labels(conversation_id,title) VALUES(?,?)',(new_id,'Cópia da conversa'))
    return {'id':new_id,'title':'Cópia da conversa'}

@app.get('/agents/{agent_id}/conversations/{cid}/messages')
def get_messages(agent_id:int,cid:int):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,agent_id)).fetchone(): raise HTTPException(404,'Conversa não encontrada')
        return [dict(r) for r in c.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id',(cid,))]
@app.post('/agents/{agent_id}/conversations/{cid}/messages')
async def send_message(agent_id:int,cid:int,body:ChatRequest):
    try: spec=repo.get_active_spec(agent_id)
    except KeyError: raise HTTPException(404,'Agente não encontrado')
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND agent_id=?',(cid,agent_id)).fetchone(): raise HTTPException(404,'Conversa não encontrada')
        previous=[dict(r) for r in c.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id',(cid,))]
    system='Você é '+spec.name+'. Objetivo: '+spec.purpose+'\nInstruções: '+ '\n'.join(spec.instructions)+'\nNesta etapa você não possui ferramentas disponíveis. Não afirme ter acessado fontes ou executado ações.'
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
        try:
            answer=await orchestrate(gateway,spec,previous,body.content,run_id)
            with connect() as c:
                from .db import begin_write
                begin_write(c)
                state=c.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()['status']
                if state != 'running': raise RuntimeError('Execução cancelada.')
                c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(cid,'user',body.content),(cid,'assistant',answer)])
                c.execute("UPDATE runs SET status='completed',steps=?,tokens_in=?,tokens_out=?,cost_estimate=? WHERE id=?",(budget.calls,budget.input_tokens,budget.output_tokens,budget.spent if budget.cost_known else None,run_id))
                emit(c,run_id,'completed',{'cost_usd':budget.spent,'calls':budget.calls})
            return {'text':answer}
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
        finally: active_budget.reset(token)
    try: answer=(await gateway.chat([{'role':'system','content':system}]+previous+[{'role':'user','content':body.content}])).text
    except (httpx.HTTPError,RuntimeError,ValueError,KeyError) as e: raise HTTPException(502,detail=f'O modelo não respondeu ({type(e).__name__}). Confira a configuração e tente novamente.')
    if not answer.strip(): raise HTTPException(502,'O modelo devolveu uma resposta vazia. Tente novamente.')
    with connect() as c:
        c.executemany('INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)',[(cid,'user',body.content),(cid,'assistant',answer)])
    return {'text':answer}

