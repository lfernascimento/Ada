"""Ferramentas MCP internas, restritas ao agente e a fontes públicas."""
import asyncio,json
from claude_agent_sdk import tool,create_sdk_mcp_server
from .tools.registry import SearchDocuments,ReadURL,search_documents,read_url,ToolError

def build_mcp(agent_id:int, allowed:set[str]):
    tools=[]; names=[]
    def ensure_permission(grant):
        from .repo import get_active_spec
        spec=get_active_spec(agent_id)
        if grant not in spec.tools or spec.autonomy_policy.get(grant)!='allowed':raise ValueError('Permissão revogada.')
    if 'mcp_documentos' in allowed:
        @tool('buscar_documentos','Busca trechos de documentos adicionados a este agente.',{'pergunta':str})
        async def docs(args):
            try:
                ensure_permission('mcp_documentos')
                query=SearchDocuments.model_validate(args)
                data=await asyncio.to_thread(search_documents,query,agent_id)
                return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]}
            except Exception:
                return {'is_error':True,'isError':True,'content':[{'type':'text','text':'Não foi possível consultar os documentos.'}]}
        tools.append(docs);names.append('mcp__ada__buscar_documentos')
    if 'mcp_paginas' in allowed:
        @tool('ler_pagina','Lê uma URL pública HTTP ou HTTPS e devolve texto e fonte.',{'url':str})
        async def page(args):
            try:
                ensure_permission('mcp_paginas')
                data=await asyncio.to_thread(read_url,ReadURL.model_validate(args))
                return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]}
            except Exception:
                return {'is_error':True,'isError':True,'content':[{'type':'text','text':'Não foi possível consultar essa página pública.'}]}
        tools.append(page);names.append('mcp__ada__ler_pagina')
    if 'mcp_memoria' in allowed:
        from .notes import add_note,memory_context
        @tool('consultar_notas','Consulta notas aprovadas deste agente.',{})
        async def memory_read(args):
            ensure_permission('mcp_memoria')
            data=await asyncio.to_thread(memory_context,agent_id)
            return {'content':[{'type':'text','text':data or 'Nenhuma nota aprovada.'}]}
        @tool('propor_nota','Propõe uma nota curta para memória. Exige aprovação do usuário; nunca salve credenciais.',{'content':str})
        async def memory_write(args):
            try:
                ensure_permission('mcp_memoria')
                data=await asyncio.to_thread(add_note,agent_id,args['content'],'agent')
                return {'content':[{'type':'text','text':json.dumps(data)}]}
            except Exception:
                return {'is_error':True,'isError':True,'content':[{'type':'text','text':'Não foi possível propor a nota.'}]}
        tools.extend([memory_read,memory_write]);names.extend(['mcp__ada__consultar_notas','mcp__ada__propor_nota'])
    from . import mcp_connections as connectors
    from .repo import get_active_spec
    def guarded(name,description,schema,func,grant):
        @tool(name,description,schema)
        async def call(args):
            try:
                spec=get_active_spec(agent_id)
                if grant not in spec.tools or spec.autonomy_policy.get(grant)!='allowed':raise ValueError('revoked')
                data=await func(agent_id,**args)
                encoded=json.dumps(data,ensure_ascii=False)
                if len(encoded)>40000:encoded=json.dumps({'truncated':True,'text':encoded[:39000]},ensure_ascii=False)
                return {'content':[{'type':'text','text':encoded}]}
            except Exception:
                return {'is_error':True,'isError':True,'content':[{'type':'text','text':'Consulta não concluída. Confira conexão, recurso autorizado e permissões.'}]}
        tools.append(call);names.append('mcp__ada__'+name)
    if 'mcp_github' in allowed:
        guarded('github_arquivo','Lê um arquivo de repositório público autorizado.',{'repo':str,'path':str},connectors.github_file,'mcp_github')
        guarded('github_ci','Consulta status de CI e passos de jobs do commit atual.',{'repo':str},connectors.github_status,'mcp_github')
        guarded('github_arvore','Lista caminhos no commit atual do repositório autorizado.',{'repo':str},connectors.repository_snapshot,'mcp_github')
    if 'mcp_figma' in allowed:
        guarded('figma_contexto','Consulta estrutura de um arquivo Figma autorizado.',{'key':str},connectors.figma_context,'mcp_figma')
    if 'mcp_workspace' in allowed:
        guarded('workspace_documento','Lê Docs, Sheets ou metadados Drive de recurso autorizado.',{'service':str,'docid':str},connectors.workspace_document,'mcp_workspace')
    if 'mcp_codigo' in allowed:
        from .conversation_tools import prepare_chat_code
        guarded('preparar_codigo','Implementa a tarefa atual do usuário em repositório autorizado, verifica na sandbox e prepara uma proposta. Nunca publica PR.',{'repo':str},prepare_chat_code,'mcp_codigo')
    if 'mcp_workflows' in allowed:
        @tool('salvar_workflow','Salva um objetivo reutilizável para este agente, quando solicitado pelo usuário. Não executa nem ativa recorrência; aparece em Contexto > Workflows.',{'name':str,'objective':str})
        async def save_workflow(args):
            from .conversation_tools import chat_task
            from .workflows import create,Definition
            task=chat_task.get()
            try:
                if not task or task[0]!=agent_id:raise ValueError('Sem conversa vinculada.')
                data=await asyncio.to_thread(create,agent_id,Definition.model_validate(args))
                return {'content':[{'type':'text','text':json.dumps(data)}]}
            except Exception:return {'is_error':True,'content':[{'type':'text','text':'Não foi possível salvar o workflow.'}]}
        tools.append(save_workflow);names.append('mcp__ada__salvar_workflow')
    if 'mcp_historico' in allowed:
        @tool('consultar_historico','Recupera trechos originais relevantes da conversa atual, incluindo mensagens anteriores ao resumo. Use quando faltar um detalhe histórico.',{'pergunta':str})
        async def history(args):
            from .conversation_tools import chat_task
            from .conversation_context import recall
            task=chat_task.get()
            if not task or task[0]!=agent_id:return {'is_error':True,'content':[{'type':'text','text':'Sem conversa vinculada.'}]}
            result=await asyncio.to_thread(recall,agent_id,task[1],args['pergunta'],9223372036854775807)
            return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}]}
        tools.append(history);names.append('mcp__ada__consultar_historico')
    if 'mcp_arquivos' in allowed:
        @tool('gerar_arquivo','Gera um arquivo a partir da última resposta desta conversa, quando o usuário solicitar. Formatos: md, pdf, docx, pptx, xlsx.',{'format':str})
        async def export_file(args):
            from .conversation_tools import chat_task
            from .exports import export_link
            task=chat_task.get()
            try:
                if not task or task[0]!=agent_id:raise ValueError('Sem conversa vinculada.')
                data=await asyncio.to_thread(export_link,agent_id,task[1],args['format'])
                return {'content':[{'type':'text','text':data}]}
            except Exception:
                return {'is_error':True,'content':[{'type':'text','text':'Não foi possível exportar. É necessário ter uma resposta anterior nesta conversa e escolher um formato disponível.'}]}
        tools.append(export_file);names.append('mcp__ada__gerar_arquivo')
    if 'mcp_runtime' in allowed:
        from .sdk_runtime_tools import runtime_tools
        extra=runtime_tools(agent_id)
        tools.extend(extra);names.extend('mcp__ada__'+t.name for t in extra)
    return ({'ada':create_sdk_mcp_server('ada',tools=tools)},names) if tools else ({},[])
