"""Explicit resource grants, versioned with each agent specification."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from .models import AgentSpec
from . import repo,repository
router=APIRouter()
class ResourceBody(BaseModel):
    resources:dict[str,list[str]]=Field(default_factory=dict)
    allow_code:bool=False
@router.put('/agents/{agent_id}/resources')
def set_resources(agent_id:int,body:ResourceBody):
    try:spec=repo.get_active_spec(agent_id)
    except KeyError:raise HTTPException(404,'Agente não encontrado.')
    if set(body.resources)-{'github','figma','docs','sheets','drive'}:raise HTTPException(422,'Serviço desconhecido.')
    values={k:list(dict.fromkeys(x.strip() for x in v if x.strip())) for k,v in body.resources.items()}
    values['github']=[repository.repo_name(x) for x in values.get('github',[])]
    data=spec.model_dump();data['resources']=values
    bindings={'mcp_codigo':body.allow_code and bool(values.get('github')),'mcp_github':bool(values.get('github')),'mcp_figma':bool(values.get('figma')),'mcp_workspace':any(values.get(k) for k in ('drive','docs','sheets'))}
    for tool,on in bindings.items():
        if on:
            if tool not in data['tools']:data['tools'].append(tool)
            data['autonomy_policy'][tool]='allowed'
        else:
            data['tools']=[t for t in data['tools'] if t!=tool];data['autonomy_policy'].pop(tool,None)
    try:updated=AgentSpec.model_validate(data)
    except ValueError:raise HTTPException(422,'Use até dez identificadores válidos por serviço.')
    version=repo.add_version(agent_id,updated,'Atualização de recursos MCP','usuário')
    repo.activate_version(agent_id,version)
    return {'version':version,'resources':updated.resources}
