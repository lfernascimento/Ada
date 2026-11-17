"""Avalia um servidor compatível com OpenAI antes de escolher o modelo."""
import json, os, statistics, time
from typing import Literal
import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    purpose: str
    tools: list[Literal['ler_url', 'salvar_relatorio', 'consultar_documentos']]
    instructions: list[str]

class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acao: Literal['usar_ferramenta', 'responder']
    nome: str | None = None
    argumentos: dict | None = None
    texto: str | None = None
    def valid_action(self):
        return (self.acao == 'usar_ferramenta' and self.nome in ('ler_url','salvar_relatorio','consultar_documentos') and isinstance(self.argumentos, dict) and self.texto is None) or (self.acao == 'responder' and isinstance(self.texto, str) and self.nome is None and self.argumentos is None)

INPUTS = ['Acompanhar notícias de IA e resumir com fontes', 'Responder dúvidas sobre o regulamento do meu curso', 'Pesquisar tendências de design e preparar relatórios', 'Organizar respostas sobre políticas internas', 'Analisar notícias sobre energia solar', 'Sintetizar documentos de reuniões', 'Preparar um resumo de artigos científicos', 'Responder perguntas sobre um manual de produto', 'Acompanhar novidades de acessibilidade', 'Criar relatórios sobre o mercado de educação']
TASKS = ['Leia https://example.com e resuma', 'Responda olá', 'Consulte documentos sobre matrícula', 'Salve um relatório intitulado Resumo', 'Leia https://example.org']

def main():
    base=os.getenv('MODEL_BASE_URL', '').rstrip('/')
    name=os.getenv('MODEL_NAME', '')
    if not base or not name:
        print('Configure MODEL_BASE_URL e MODEL_NAME antes de executar.'); return 2
    url=base + ('/chat/completions' if base.endswith('/v1') else '/v1/chat/completions')
    headers={'Authorization': 'Bearer ' + os.environ['MODEL_API_KEY']} if os.getenv('MODEL_API_KEY') else {}
    structured=True; durations=[]
    def call(messages, schema):
        nonlocal structured
        body={'model':name,'messages':messages,'temperature':0.2,'stream':False}
        if structured:
            body['response_format']={'type':'json_schema','json_schema':{'name':schema.__name__, 'schema':schema.model_json_schema(), 'strict':True}}
        else:
            body['messages']=[{'role':'system','content':'Responda somente JSON válido conforme este schema: '+json.dumps(schema.model_json_schema(), ensure_ascii=False)}]+messages
        start=time.perf_counter()
        response=client.post(url, json=body, headers=headers)
        if structured and response.status_code in (400,404,422):
            structured=False
            return call(messages, schema)
        response.raise_for_status()
        durations.append(time.perf_counter()-start)
        return response.json()['choices'][0]['message']['content']
    first=retry=invalid=protocol=0
    with httpx.Client(timeout=180) as client:
        for phrase in INPUTS:
            messages=[{'role':'system','content':'Crie uma especificação de agente. Responda apenas JSON.'},{'role':'user','content':phrase}]
            for attempt in range(2):
                try:
                    raw=call(messages, Spec); Spec.model_validate_json(raw)
                    if attempt: retry+=1
                    else: first+=1
                    break
                except (ValidationError, ValueError, KeyError) as e:
                    if attempt: invalid+=1
                    else: messages += [{'role':'assistant','content':raw if 'raw' in locals() else ''},{'role':'user','content':'Corrija o JSON. Erro: '+str(e)[:1000]}]
        for task in TASKS:
            try:
                a=Action.model_validate_json(call([{'role':'system','content':'Responda somente com JSON: {"acao":"usar_ferramenta","nome":"...","argumentos":{...}} ou {"acao":"responder","texto":"..."}.'},{'role':'user','content':task}],Action))
                protocol += int(a.valid_action())
            except (ValidationError, ValueError, KeyError): pass
    p95=sorted(durations)[max(0, __import__('math').ceil(.95*len(durations))-1)] if durations else 0
    print(f'JSON: primeira={first}, após repetição={retry}, inválidos={invalid}; protocolo={protocol}/5')
    print(f'Latência média={statistics.mean(durations):.2f}s; p95={p95:.2f}s; schema nativo={structured}')
    print('GO' if (first+retry)>=9 and protocol>=4 else 'NO-GO')
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except (httpx.HTTPError, KeyError, ValueError) as e:
        print(f'Teste inconclusivo: erro de acesso ou resposta do servidor: {type(e).__name__}: {e}'); raise SystemExit(2)
