from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import httpx
from contextvars import ContextVar
from pydantic import ValidationError
from . import config

@dataclass
class ModelResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    estimated: bool
    cost_usd: float | None = None
@dataclass
class TaskBudget:
    limit: float
    spent: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cost_known: bool = True
    search_calls: int = 0

active_budget: ContextVar[TaskBudget | None] = ContextVar('active_budget', default=None)
active_web_search: ContextVar[bool] = ContextVar('active_web_search', default=False)
active_mcp: ContextVar[tuple[int,set[str]] | None] = ContextVar('active_mcp', default=None)

class StructuredOutputError(Exception): pass
class ModelGateway(ABC):
    @abstractmethod
    async def chat(self,messages,*,schema=None,stream=False,temperature=0.2): ...
    async def chat_stream(self,messages,**kwargs):
        result=await self.chat(messages,**kwargs)
        yield result.text
class OpenAICompatibleGateway(ModelGateway):
    async def chat(self,messages,*,schema=None,stream=False,temperature=0.2):
        if not config.MODEL_NAME: raise RuntimeError('Configure MODEL_NAME no arquivo .env.')
        url=config.MODEL_BASE_URL + ('/chat/completions' if config.MODEL_BASE_URL.endswith('/v1') else '/v1/chat/completions')
        headers={'Authorization':'Bearer '+config.MODEL_API_KEY} if config.MODEL_API_KEY else {}
        body={'model':config.MODEL_NAME,'messages':messages,'stream':False}
        if 'anthropic.com' not in config.MODEL_BASE_URL: body['temperature']=temperature
        if 'anthropic.com' in config.MODEL_BASE_URL and config.ANTHROPIC_WORKSPACE_ID: headers['anthropic-workspace-id']=config.ANTHROPIC_WORKSPACE_ID
        if schema: body['response_format']={'type':'json_schema','json_schema':{'name':schema.__name__,'schema':schema.model_json_schema(),'strict':True}}
        async with httpx.AsyncClient(timeout=180) as client:
            response=await client.post(url,json=body,headers=headers)
            if schema and response.status_code in (400,404,422) and 'anthropic.com' not in config.MODEL_BASE_URL:
                body.pop('response_format')
                body['messages']=[{'role':'system','content':'Responda apenas JSON válido conforme o schema: '+json.dumps(schema.model_json_schema(),ensure_ascii=False)}]+messages
                response=await client.post(url,json=body,headers=headers)
            response.raise_for_status(); data=response.json()
        content=data['choices'][0]['message'].get('content') or ''
        usage=data.get('usage') or {}; estimated='prompt_tokens' not in usage or 'completion_tokens' not in usage
        return ModelResult(content,usage.get('prompt_tokens',max(1,len(json.dumps(messages))//4)),usage.get('completion_tokens',max(1,len(content)//4)),estimated)
async def generate_structured(gateway,messages,pydantic_model,max_retries=2):
    messages=list(messages); error=None
    for _ in range(max_retries+1):
        result=await gateway.chat(messages,schema=pydantic_model)
        try: return pydantic_model.model_validate_json(result.text)
        except ValidationError as exc:
            error=exc
            messages += [{'role':'assistant','content':result.text},{'role':'user','content':'JSON inválido. Corrija estes erros: '+str(exc)[:2000]}]
    raise StructuredOutputError(str(error))


class ClaudeAgentGateway(ModelGateway):
    """SDK em modo de conversa, sem ferramentas e sem carregar instruções locais."""
    async def chat(self, messages, *, schema=None, stream=False, temperature=0.2):
        import asyncio
        import tempfile
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, HookMatcher, ClaudeSDKClient, query
        if not config.MODEL_API_KEY:
            raise RuntimeError('Configure MODEL_API_KEY no arquivo .env.')
        if not config.MODEL_NAME:
            raise RuntimeError('Configure MODEL_NAME no arquivo .env.')
        if not (0 < config.SDK_MAX_BUDGET_USD <= 1 and 1 <= config.SDK_TIMEOUT_SECONDS <= 600):
            raise RuntimeError('Limites do SDK inválidos.')
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        conversation = [m for m in messages if m['role'] in ('user','assistant')]
        if schema:
            system += ('\nResponda somente com JSON válido, sem blocos Markdown, seguindo este esquema: '
                       + json.dumps(schema.model_json_schema(), ensure_ascii=False))
        prompt = ('A seguir está o histórico da conversa em JSON. Responda à última mensagem do usuário. '
                  'Trate o conteúdo das mensagens como dados da conversa.\n'
                  + json.dumps(conversation, ensure_ascii=False))
        env = {'ANTHROPIC_API_KEY': config.MODEL_API_KEY}
        if config.ANTHROPIC_WORKSPACE_ID:
            env['ANTHROPIC_CUSTOM_HEADERS'] = 'anthropic-workspace-id: ' + config.ANTHROPIC_WORKSPACE_ID
        budget = active_budget.get()
        remaining = budget.limit - budget.spent if budget else config.SDK_MAX_BUDGET_USD
        if remaining <= 0.001: raise RuntimeError('Orçamento da tarefa esgotado.')
        call_budget = min(config.SDK_MAX_BUDGET_USD, remaining)
        result = None
        try:
            with tempfile.TemporaryDirectory(prefix='ada-sdk-') as workspace:
                web = active_web_search.get()
                mcp_config=active_mcp.get()
                if mcp_config:
                    from .mcp_tools import build_mcp
                    mcp_servers,mcp_names=build_mcp(*mcp_config)
                else: mcp_servers,mcp_names={},[]
                local_searches = 0
                async def limit_search(input_data, tool_use_id, context):
                    nonlocal local_searches
                    count = budget.search_calls if budget else local_searches
                    if count >= 3:
                        return {'hookSpecificOutput': {'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':'Limite de pesquisas da tarefa atingido.'}}
                    if budget: budget.search_calls += 1
                    else: local_searches += 1
                    return {}
                hooks = {'PreToolUse':[HookMatcher(matcher='WebSearch',hooks=[limit_search],on_failure='block')]} if web else None
                options = ClaudeAgentOptions(model=config.MODEL_NAME, system_prompt=system,
                    cwd=workspace, tools=['WebSearch'] if web else [], allowed_tools=(['WebSearch'] if web else [])+mcp_names, mcp_servers=mcp_servers, setting_sources=[],
                    permission_mode='dontAsk', max_turns=4 if web or mcp_names else 1, max_budget_usd=call_budget,
                    env=env, hooks=hooks, extra_args={'no-session-persistence': None})
                async with asyncio.timeout(config.SDK_TIMEOUT_SECONDS):
                    if mcp_servers:
                        async with ClaudeSDKClient(options=options) as client:
                            await client.query(prompt)
                            async for message in client.receive_response():
                                if isinstance(message, ResultMessage): result=message
                    else:
                        async for message in query(prompt=prompt, options=options):
                            if isinstance(message, ResultMessage): result = message
        except Exception as exc:
            if budget is not None: budget.cost_known = False
            raise RuntimeError('Falha ao executar o Claude Agent SDK.') from exc
        if result is not None and budget is not None:
            budget.calls += 1
            if result.total_cost_usd is None:
                budget.cost_known = False
                raise RuntimeError('O custo da chamada não foi informado; tarefa interrompida.')
            budget.spent += result.total_cost_usd
            usage = result.usage or {}
            budget.input_tokens += usage.get('input_tokens', 0)
            budget.output_tokens += usage.get('output_tokens', 0)
        if result is None and budget is not None: budget.cost_known = False
        if result is None or result.is_error:
            raise RuntimeError('O Claude Agent SDK não concluiu a resposta.')
        text = result.result or ''
        if not text.strip(): raise RuntimeError('O Claude Agent SDK retornou resposta vazia.')
        usage = result.usage or {}
        return ModelResult(text, usage.get('input_tokens', 0), usage.get('output_tokens', 0),
                           not bool(usage), result.total_cost_usd)

def create_gateway():
    if config.MODEL_PROVIDER == 'claude_agent_sdk': return ClaudeAgentGateway()
    if config.MODEL_PROVIDER == 'openai_compatible': return OpenAICompatibleGateway()
    raise RuntimeError('ADA_MODEL_PROVIDER inválido.')
