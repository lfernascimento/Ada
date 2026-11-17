"""Ferramentas MCP internas, restritas ao agente e a fontes públicas."""
import asyncio,json
from claude_agent_sdk import tool,create_sdk_mcp_server
from .tools.registry import SearchDocuments,ReadURL,search_documents,read_url,ToolError

def build_mcp(agent_id:int, allowed:set[str]):
    tools=[]; names=[]
    if 'mcp_documentos' in allowed:
        @tool('buscar_documentos','Busca trechos de documentos adicionados a este agente.',{'pergunta':str})
        async def docs(args):
            try:
                query=SearchDocuments.model_validate(args)
                data=await asyncio.to_thread(search_documents,query,agent_id)
                return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]}
            except Exception:
                return {'isError':True,'content':[{'type':'text','text':'Não foi possível consultar os documentos.'}]}
        tools.append(docs);names.append('mcp__ada__buscar_documentos')
    if 'mcp_paginas' in allowed:
        @tool('ler_pagina','Lê uma URL pública HTTP ou HTTPS e devolve texto e fonte.',{'url':str})
        async def page(args):
            try:
                data=await asyncio.to_thread(read_url,ReadURL.model_validate(args))
                return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]}
            except Exception:
                return {'isError':True,'content':[{'type':'text','text':'Não foi possível consultar essa página pública.'}]}
        tools.append(page);names.append('mcp__ada__ler_pagina')
    return ({'ada':create_sdk_mcp_server('ada',tools=tools)},names) if tools else ({},[])
