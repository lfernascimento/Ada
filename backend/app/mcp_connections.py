"""Read-only MCP adapters. Every call rechecks the active agent resource grants."""
import json,re
from contextvars import ContextVar
pinned_revisions=ContextVar("pinned_revisions",default={})
from fastapi import HTTPException,Response
from .repo import get_active_spec
from . import repository,connections,google_workspace

def authorize(agent_id,tool,service,resource):
    spec=get_active_spec(agent_id)
    if tool not in spec.tools or spec.autonomy_policy.get(tool)!='allowed':raise HTTPException(403,'Ferramenta revogada.')
    if resource not in spec.resources.get(service,[]):raise HTTPException(403,'Recurso não autorizado para este agente.')

async def github_file(agent_id,repo,path):
    repo=repository.repo_name(repo);authorize(agent_id,'mcp_github','github',repo)
    token=repository.session()['access_token'];name,branch,sha,_=await repository.base_info(repo,token)
    sha=pinned_revisions.get().get(name,sha)
    content,_=await repository.read_file(name,path,sha,token)
    return {'repository':name,'branch':branch,'sha':sha,'path':path,'content':content[:24000],'truncated':len(content)>24000}

async def github_status(agent_id,repo):
    repo=repository.repo_name(repo);authorize(agent_id,'mcp_github','github',repo)
    token=repository.session()['access_token'];name,branch,sha,_=await repository.base_info(repo,token)
    sha=pinned_revisions.get().get(name,sha)
    runs=await repository.gh('GET',f'/repos/{name}/actions/runs',token,params={'head_sha':sha,'per_page':10})
    output=[]
    for run in runs.get('workflow_runs',[])[:3]:
        rid=run.get('id')
        if not isinstance(rid,int):continue
        jobs=await repository.gh('GET',f'/repos/{name}/actions/runs/{rid}/jobs',token,params={'per_page':20})
        output.append({'id':rid,'name':run.get('name'),'status':run.get('status'),'conclusion':run.get('conclusion'),'url':run.get('html_url'),
            'jobs':[{'name':j.get('name'),'conclusion':j.get('conclusion'),'steps':[{'name':x.get('name'),'conclusion':x.get('conclusion')} for x in j.get('steps',[])[:30]]} for j in jobs.get('jobs',[])[:10]]})
    return {'repository':name,'branch':branch,'sha':sha,'runs':output,'note':'Resumo de jobs e passos; não contém o log completo.'}

async def repository_snapshot(agent_id,repo):
    repo=repository.repo_name(repo);authorize(agent_id,'mcp_github','github',repo)
    token=repository.session()['access_token'];name,branch,sha,_=await repository.base_info(repo,token)
    sha=pinned_revisions.get().get(name,sha)
    commit=await repository.gh('GET',f'/repos/{name}/git/commits/{sha}',token)
    tree=commit.get('tree',{}).get('sha','')
    if not re.fullmatch('[a-f0-9]{40}',tree):raise HTTPException(502,'Árvore inválida.')
    data=await repository.gh('GET',f'/repos/{name}/git/trees/{tree}',token,params={'recursive':'1'})
    paths=[x['path'] for x in data.get('tree',[]) if x.get('type')=='blob' and x.get('mode') in ('100644','100755')][:800]
    return {'repository':name,'branch':branch,'sha':sha,'paths':paths,'truncated':bool(data.get('truncated')) or len(paths)>=800}

async def figma_context(agent_id,key):
    authorize(agent_id,'mcp_figma','figma',key)
    return await connections.figma_file(key)

async def workspace_document(agent_id,service,docid):
    if service not in ('docs','sheets','drive'):raise HTTPException(422,'Serviço não suportado.')
    docid=google_workspace.file_id(docid);authorize(agent_id,'mcp_workspace',service,docid)
    if service=='drive':
        import httpx
        token=google_workspace.session('drive')
        async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False,headers={'Authorization':'Bearer '+token}) as c:
            return await google_workspace.get(c,'https://www.googleapis.com/drive/v3/files/'+docid,{'fields':'id,name,mimeType,description'})
    return await google_workspace.browse(service,Response(),id=docid,range='A1:Z50')
