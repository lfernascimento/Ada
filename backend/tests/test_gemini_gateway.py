import asyncio
import httpx
from app import config
from app.model_gateway import OpenAICompatibleGateway, TaskBudget, active_budget

def test_gemini_compatible_url_and_budget(monkeypatch):
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json, headers):
            calls.append((url, json, headers))
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 5}}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    monkeypatch.setattr(config, 'MODEL_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai')
    monkeypatch.setattr(config, 'MODEL_NAME', 'gemini-test')
    monkeypatch.setattr(config, 'MODEL_API_KEY', 'test')
    monkeypatch.setenv('ADA_INPUT_USD_PER_MTOK', '1')
    monkeypatch.setenv('ADA_OUTPUT_USD_PER_MTOK', '10')
    budget = TaskBudget(limit=0.5)
    token = active_budget.set(budget)
    try:
        result = asyncio.run(OpenAICompatibleGateway().chat([{'role': 'user', 'content': 'Oi'}]))
    finally:
        active_budget.reset(token)
    assert calls[0][0] == 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
    assert result.text == 'ok'
    assert budget.calls == 1 and budget.spent > 0
