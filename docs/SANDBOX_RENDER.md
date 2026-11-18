# Sandbox remota da Ada

O backend aceita `ADA_SANDBOX_BACKEND=render` para executar trechos Python e validar as alterações preparadas pelo fluxo de código. Docker e Multipass continuam disponíveis localmente. Esta integração não depende da chave do Claude para o teste técnico; o modelo só é necessário para gerar as alterações pelo chat.

## Configuração

No Render, em Environment do Web Service, adicione:

```dotenv
ADA_SANDBOX_BACKEND=render
ADA_RENDER_SANDBOX_ENABLED=true
RENDER_API_KEY=VALOR_PRIVADO_NO_PAINEL
RENDER_WORKSPACE_ID=ID_DO_WORKSPACE
```

Use a credencial com o menor escopo disponível para gerenciar sandboxes no workspace escolhido. Não coloque a chave no frontend ou no Git e não a envie pelo chat. A conta precisa estar habilitada para Sandboxes e sua cobrança. A chave de autenticação da plataforma Ada é independente desta chave de API do Render.

O SDK oficial Python `render==1.9.0` está nas dependências. O Dockerfile inclui o runner de testes necessário para o backend hospedado. O código só cria recursos depois de uma execução explícita ou de uma tarefa autorizada no chat; consultar o status verifica apenas a configuração, sem criar sandboxes nem atestar acesso real.

## Comportamento

- Plano fixo `1c-2g`, uma execução simultânea por processo. A instalação utiliza um worker.
- Cada validação usa uma sandbox nova, com a mesma cópia do repositório e as alterações da tentativa atual. Estado de processos não é reutilizado entre tentativas.
- Saída limitada a 64 KB e prazo local de até 150 segundos, mais até 20 segundos para limpeza. O runtime do recurso tem limite de 300 segundos como proteção adicional.
- Rede de saída bloqueada pelo provedor (`deny-all`). As credenciais e as variáveis de ambiente da Ada não são enviadas ao ambiente remoto.
- Arquivos do trabalho são transferidos pelo SDK; repositórios continuam sujeitos aos limites existentes. Nenhuma dependência de projeto é instalada automaticamente. O runner suporta sintaxe Python, unittest e npm test quando disponíveis e utilizáveis na imagem.
- Encerramento solicitado e confirmado antes de retornar o resultado. Se a limpeza falhar, novas execuções remotas ficam bloqueadas no processo até que o operador confira o painel e reinicie o backend. Se o processo for encerrado abruptamente, o timeout do provedor é a proteção final.
- Não são criados snapshots persistentes, endpoints públicos ou permissões de escrita no GitHub por esta integração. A publicação de PR mantém sua aprovação independente.

Esta primeira integração conecta o executor ao fluxo existente; não oferece um terminal geral ao modelo, instalação irrestrita de pacotes, preview público ou máquina permanente por agente.

## Teste técnico real

Depois de configurar a conta e conferir custos, no computador com `.env` privado atualizado:

```powershell
cd C:\Projetos\ADA\ada
..\.venv\Scripts\python.exe backend\scripts\check_render_sandbox.py --run-paid-test
```

O teste cria três sandboxes sequenciais: um trecho Python, uma validação com erro e outra correta. Não usa a chave do modelo. A opção `--run-paid-test` é obrigatória para impedir execução acidental. Não executamos esse teste real durante a implementação: os testes automatizados usam um cliente simulado e não geram cobrança.

## Custo e referências

Em 18/11/2026, a documentação informa CPU a US$ 0,12 por CPU-hora efetivamente usada e memória a US$ 0,019 por GB-hora provisionado. Para `1c-2g` a plena CPU, cinco minutos correspondem a aproximadamente US$ 0,0132 de CPU e memória; três execuções nessa duração corresponderiam a aproximadamente US$ 0,0395. São estimativas, não um teto de faturamento. Outros serviços e eventuais cobranças adicionais da conta são separados.

- [Preços e limites](https://render.com/docs/sandboxes-limits)
- [SDK Python](https://render.com/docs/sandboxes-sdk-python)
- [Rede e ciclo de vida](https://render.com/docs/sandboxes-creating)

O recurso e o SDK estão em beta. A execução real, disponibilidade de Python/Node na imagem padrão e permissões da conta ainda precisam ser verificadas antes de afirmar que a sandbox está operacional.
