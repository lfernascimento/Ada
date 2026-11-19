"""Provider-neutral workflow graph with persistent, reviewable milestones."""
from contextlib import asynccontextmanager
import asyncio
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from langgraph.types import Command,interrupt
from . import db

class WorkflowState(TypedDict,total=False):
    agent_id:int
    conversation_id:int
    run_id:int
    review:bool
    approved:bool

async def run_agent(state:WorkflowState):
    from . import main
    with db.connect() as c:
        row=c.execute('SELECT v.objective FROM workflow_runs r JOIN workflow_versions v ON v.workflow_id=r.workflow_id AND v.number=r.version WHERE r.id=?',(state['run_id'],)).fetchone()
    if not row:raise RuntimeError('Execução inexistente.')
    response=await main.send_message(state['agent_id'],state['conversation_id'],main.ChatRequest(content=row['objective']))
    with db.connect() as c:c.execute('UPDATE workflow_runs SET result=? WHERE id=?',(response['text'],state['run_id']))
    return {}

def review(state:WorkflowState):
    decision=interrupt({'kind':'review','message':'Revise o resultado antes de concluir o workflow.'})
    if not isinstance(decision,bool):raise ValueError('Decisão inválida.')
    return {'approved':decision}

def choose(state:WorkflowState):return 'review' if state.get('review') else END

def build_graph(checkpointer):
    graph=StateGraph(WorkflowState)
    graph.add_node('agent',run_agent);graph.add_node('review',review)
    graph.add_edge(START,'agent');graph.add_conditional_edges('agent',choose,['review',END]);graph.add_edge('review',END)
    return graph.compile(checkpointer=checkpointer)

@asynccontextmanager
async def checkpoint_store():
    if db.DB_BACKEND=='sqlite':
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        # A separate SQLite file avoids blocking the agent's own synchronous writes
        # while LangGraph's asynchronous checkpointer is committing.
        async with aiosqlite.connect(str(db.DB_PATH)+'.langgraph') as conn:
            saver=AsyncSqliteSaver(conn)
            await saver.setup()
            yield saver
    elif db.DB_BACKEND=='postgres':
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver
        # psycopg async connections do not support the default Windows event loop.
        # Keep the supported synchronous saver off the server event loop instead.
        class ThreadedSaver(PostgresSaver):
            async def aget_tuple(self,config):return await asyncio.to_thread(self.get_tuple,config)
            async def aput(self,config,checkpoint,metadata,new_versions):return await asyncio.to_thread(self.put,config,checkpoint,metadata,new_versions)
            async def aput_writes(self,config,writes,task_id,task_path=''):return await asyncio.to_thread(self.put_writes,config,writes,task_id,task_path)
            async def alist(self,config,*,filter=None,before=None,limit=None):
                entries=await asyncio.to_thread(lambda:list(self.list(config,filter=filter,before=before,limit=limit)))
                for item in entries:yield item
            async def adelete_thread(self,thread_id):return await asyncio.to_thread(self.delete_thread,thread_id)
        _,url,cert=db.pg_config()
        conn=await asyncio.to_thread(psycopg.connect,url,sslmode='verify-full',sslrootcert=cert,connect_timeout=10,autocommit=True,row_factory=dict_row)
        try:
            await asyncio.to_thread(conn.execute,sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(db.DB_SCHEMA)))
            saver=ThreadedSaver(conn)
            def prepare_private_tables():
                tables=('checkpoint_migrations','checkpoints','checkpoint_blobs','checkpoint_writes')
                with conn.transaction():
                    for migration,table in zip(saver.MIGRATIONS[:4],tables):
                        conn.execute(migration)
                        conn.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(table)))
            await asyncio.to_thread(prepare_private_tables)
            await asyncio.to_thread(saver.setup)
            yield saver
        finally:
            await asyncio.to_thread(conn.close)
    else:raise RuntimeError('Banco de checkpoints indisponível.')

def configuration(rid):return {'configurable':{'thread_id':f'ada-workflow-{rid}'}}

async def invoke(aid,cid,rid,require_review=False):
    async with checkpoint_store() as saver:
        state=await build_graph(saver).ainvoke({'agent_id':aid,'conversation_id':cid,'run_id':rid,'review':require_review},configuration(rid))
        return {'waiting':bool(state.get('__interrupt__'))}

async def resume(rid,approved):
    async with checkpoint_store() as saver:
        state=await build_graph(saver).ainvoke(Command(resume=approved),configuration(rid))
        if state.get('__interrupt__') or state.get('approved') is not approved:raise RuntimeError('A revisão não foi concluída.')

async def delete_checkpoints(ids):
    if not ids:return
    async with checkpoint_store() as saver:
        for rid in ids:await saver.adelete_thread(f'ada-workflow-{rid}')
