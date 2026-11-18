from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal, Annotated
Tool = Literal['ler_url', 'salvar_relatorio', 'consultar_documentos', 'executar_codigo', 'pesquisar_web', 'mcp_documentos', 'mcp_paginas', 'mcp_memoria', 'mcp_github', 'mcp_figma', 'mcp_workspace', 'mcp_codigo']
Policy = Literal['allowed', 'approval_required', 'denied']
class Limits(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_steps: int = Field(ge=1, le=8)
class Style(BaseModel):
    model_config = ConfigDict(extra='forbid')
    color: Literal['amber', 'lilac', 'violet', 'sage']
    avatar: Literal['classic', 'flower', 'mouse', 'toucan', 'hedgehog', 'bear', 'crab'] = 'classic'
class AgentSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=2, max_length=40)
    purpose: str = Field(min_length=1,max_length=4000)
    instructions: list[Annotated[str,Field(min_length=1,max_length=2000)]] = Field(max_length=12)
    tools: list[Tool]
    autonomy_policy: dict[Tool, Policy]
    resources: dict[Literal['github','figma','docs','sheets','drive'],list[Annotated[str,Field(min_length=1,max_length=200)]]] = Field(default_factory=dict)
    limits: Limits
    style: Style
    @model_validator(mode='after')
    def policy_is_safe(self):
        if any(len(values)>10 for values in self.resources.values()):raise ValueError('Até dez recursos por serviço.')
        if any(tool not in self.autonomy_policy for tool in self.tools):
            raise ValueError('Cada ferramenta precisa de uma política')
        if any(self.autonomy_policy.get(tool) == 'allowed' for tool in ('salvar_relatorio','executar_codigo')):
            raise ValueError('Salvar relatório e executar código exigem aprovação')
        return self
