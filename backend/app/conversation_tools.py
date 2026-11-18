"""Chat-bound code preparation. The current user's task is never taken from repository text."""
from contextvars import ContextVar
chat_task=ContextVar('chat_task',default=None)
chat_artifacts=ContextVar('chat_artifacts',default=None)
code_in_chat=ContextVar('code_in_chat',default=False)
async def prepare_chat_code(agent_id,repo):
    from .mcp_connections import authorize
    from .model_gateway import active_mcp,active_native,active_web_search
    from . import code_workflow
    task=chat_task.get()
    if not task or task[0]!=agent_id:raise ValueError('Sem tarefa de conversa.')
    authorize(agent_id,'mcp_codigo','github',repo)
    artifacts=chat_artifacts.get()
    if artifacts is None:raise ValueError('Sem execução vinculada.')
    if artifacts:return {'message':'A proposta desta mensagem já está sendo preparada ou está disponível na conversa.'}
    artifacts.append('__preparing__')
    nt=active_native.set(None);mt=active_mcp.set(None);wt=active_web_search.set(False);ct=code_in_chat.set(True)
    try:
        result=await code_workflow.code_task.__wrapped__(agent_id,task[1],code_workflow.CodeRequest(repository=repo,task=task[2]))
        artifacts[:]=[result['draft_id']]
        return {'summary':result['text'],'draft_id':result['draft_id'],'validation':result['validation'],'message':'A proposta aparecerá na conversa para aprovação do PR.'}
    finally:
        if artifacts==['__preparing__']:artifacts.clear()
        active_native.reset(nt);active_mcp.reset(mt);active_web_search.reset(wt);code_in_chat.reset(ct)
