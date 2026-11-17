# Ada — primeira etapa

Ada permite descrever um trabalho, receber uma proposta de agente e conversar com o agente depois de confirmá-la. As especificações são validadas e armazenadas em SQLite. **Nesta etapa as ferramentas listadas são apenas planejamento:** o chat não acessa URLs, documentos nem salva relatórios. Evolução e aprovação ficam para a etapa 2.

## Preparação

Instale Python 3.11 ou superior, Node.js 20 ou superior e um servidor compatível com a API OpenAI. Para Ollama, inicie o serviço e garanta que o modelo indicado esteja instalado. Copie `.env.example` para `.env` e ajuste o modelo. Nunca compartilhe `.env` nem sua chave.

No Windows, no PowerShell, a partir desta pasta:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
cd frontend
npm install
cd ..
Copy-Item .env.example .env
```

Em dois terminais separados:

```powershell
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

```powershell
cd frontend
npm run dev
```

Abra http://localhost:5173; o diagnóstico do servidor fica em http://localhost:8000/health. Em sistemas Unix, `make setup`, `make dev` e `make test` estão disponíveis.

Para executar os testes no Windows: `cd backend; ..\.venv\Scripts\python -m pytest`. Para avaliar um modelo com o spike: configure `MODEL_BASE_URL` e `MODEL_NAME` como variáveis de ambiente e execute `python spike/model_check.py`.

A resposta do chat chega inteira; streaming fica como pendência. O produto depende de um modelo real configurado e não usa respostas falsas como alternativa. `spike/` permite medir aderência ao JSON e ao protocolo antes da demonstração.

## Infraestrutura de execução — trabalho em andamento

As tabelas de execuções, eventos, aprovações, versões e documentos são criadas automaticamente ao iniciar o backend. O painel lateral apresenta dados reais de cada agente. Documentos `.txt` e `.md` são indexados por palavras-chave; PDFs também são aceitos após instalar as dependências atualizadas:

```powershell
C:\Projetos\ADA\.venv\Scripts\python.exe -m pip install -r C:\Projetos\ADA\ada\backend\requirements.txt
```

A leitura de páginas bloqueia destinos internos e usa conexão ao IP validado. A gravação de relatório exige uma aprovação persistida e usa o conteúdo aprovado. As APIs de execução e aprovação estão disponíveis para a futura integração do ciclo de agente. Versões salvas podem ser restauradas no painel.

**Pendente:** conectar o ciclo automático ao modelo e ao chat, gerar propostas de evolução, streaming de eventos, contagem de tokens e custo reais, ensaio da demo e testes abrangentes. A infraestrutura não substitui essas funcionalidades. Não exponha o servidor local à internet: não há autenticação de usuários.

Após atualizar o projeto, reinicie o backend e o frontend. O arquivo `.env` e o banco existente são preservados. Atualizar o backend não exige apagar dados.

## Modelo Claude Sonnet 5.5

O `.env.example` contém o endereço da API da Anthropic e `claude-sonnet-5-5`. Coloque a chave apenas em `MODEL_API_KEY` no arquivo `.env` local. Não envie a chave por chat e nunca a comite. Chaves não vinculadas a um workspace podem exigir `ANTHROPIC_WORKSPACE_ID`. Após alterar o arquivo, reinicie o backend. A conexão real e a compatibilidade do schema ainda precisam ser verificadas com uma chave válida.

## Conexões externas — preparação

A tela Conexões mostra GitHub, Figma e Google Drive e a situação de cada serviço. Os conectores em preparação autenticam contas via OAuth e oferecem consultas iniciais somente de leitura; eles ainda **não estão ligados ao ciclo de ferramentas dos agentes**. Os tokens ficam apenas em memória e são perdidos quando o backend é reiniciado. A desconexão apaga a sessão local; para revogar uma autorização junto ao serviço, use também as configurações desse serviço.

A chave do modelo não substitui os cadastros OAuth abaixo. Cada serviço precisa de um aplicativo próprio com escopos mínimos e de consentimento explícito:

- GitHub: `GITHUB_CLIENT_ID` e `GITHUB_CLIENT_SECRET`; callback `http://127.0.0.1:8000/connections/github/callback`. A leitura privada por repositório selecionado e GitHub App ainda precisam de implementação. A consulta de repositórios incluída cobre somente repositórios públicos.
- Google Drive: `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`; habilite a API e registre a URI `http://127.0.0.1:8000/connections/drive/callback` no tipo de cliente compatível. A lista usa o escopo restrito `drive.file`, portanto mostra apenas arquivos acessíveis ao aplicativo; o seletor de arquivos ainda não foi construído.
- Figma: `FIGMA_CLIENT_ID` e `FIGMA_CLIENT_SECRET`; registre a URI de callback aprovada pelo Figma e os escopos `current_user:read` e `file_content:read`. O Figma exige endpoint externo de callback; a configuração local não garante que ele aceite localhost. `ADA_OAUTH_CALLBACK_BASE` permite indicar um endpoint HTTPS configurado, mas a exposição e a segurança desse endpoint precisam ser implementadas e verificadas antes de uso.

O cliente MCP, a extensão do VS Code e a coordenação de subagentes **ainda não estão implementados**. Há uma base de sandbox Python via Docker, com execução somente após aprovação, sem rede e sem montar o projeto, mas ela ainda não está ligada ao ciclo dos agentes e não foi validada em Docker real. Antes de usar conexões com agentes: criar concessão por agente e recurso, integrar ao runtime, validar as permissões no servidor, proteger credenciais, implementar expiração/renovação/revogação e realizar revisão de segurança. A interface não é uma garantia de segurança. Não exponha este backend local à internet.

### Sandbox Python em preparação

Para experimentar futuramente é necessário Docker Desktop em execução e uma imagem `python:3.12-alpine` já instalada localmente. A sandbox não baixa imagens automaticamente; utiliza limites de tempo, memória, CPU e processos, sem rede e sem montar diretórios do usuário. Essa abordagem é uma camada de isolamento, não uma garantia completa para código hostil: faça revisão de segurança e testes de escape antes de qualquer uso além de um experimento local. O caminho atual exige aprovação do payload exato e ainda não está ligado ao modelo.

## Verificação de conexão Supabase (sem migração)

No painel do Supabase, abra **Connect → Session pooler** e copie a connection string. Em **Project Settings → Database → SSL Configuration**, baixe o certificado CA. No `.env` local preencha `SUPABASE_DATABASE_URL` com a string, substituindo o marcador de senha pela senha do banco; se a senha tiver caracteres especiais, codifique-os para URL. Preencha `SUPABASE_DB_CA_CERT` com o caminho absoluto do certificado. Para caminhos do Windows, use barras normais (`C:/...`) no `.env`.

```powershell
C:\Projetos\ADA\.venv\Scripts\python.exe C:\Projetos\ADA\ada\backend\scripts\check_supabase.py
```

O comando força TLS com verificação do certificado e hostname, abre somente transação de leitura e informa contagens do SQLite. Não imprime credenciais, não cria tabelas e não migra dados. Os arquivos em `backend/migrations` são aplicados pelo script de preparação; não os execute manualmente. O backend agora suporta SQLite e PostgreSQL. O projeto local foi configurado para PostgreSQL após a preparação do esquema. Autenticação para usuários e isolamento entre usuários ainda não foram implementados.


## Banco usado pela Ada

`ADA_DATABASE_BACKEND=postgres` usa o projeto Supabase configurado em `SUPABASE_DATABASE_URL` e `SUPABASE_DB_CA_CERT`, com TLS e verificação do servidor. Para trabalhar offline com o banco local existente, mude para `ADA_DATABASE_BACKEND=sqlite` e reinicie o backend. Os bancos são separados: mudar essa opção não copia registros entre eles. O esquema `ada` no PostgreSQL tem RLS ativado e não foi concedido aos papéis públicos; o proprietário utilizado pela aplicação contorna RLS, como previsto no PostgreSQL. Antes de qualquer deploy público, é necessário implementar autenticação de usuários, autorização por recurso e políticas apropriadas; o RLS por si só não torna o backend atual multiusuário. A migração automática de dados SQLite existentes ainda não foi implementada.


## Planejamento de subagentes e referências

O backend disponibiliza `GET /orchestration/capabilities` e `POST /runs/{id}/delegation-plan` para registrar um plano em uma execução em rascunho. O plano aceita até dois subagentes temporários, com objetivo, contexto e limites propostos; fica registrado nos eventos. O SDK é uma dependência e há uma função para preparar definições de agentes, sem iniciar o runtime. **O modelo não produz planos automaticamente e nada é executado por esse endpoint.** Os limites do plano ainda precisam ser impostos pelo futuro executor, inclusive em delegações internas. Nenhuma ferramenta foi concedida aos subagentes.

O catálogo `GET /cookbooks` contém títulos, descrições e URLs de três exemplos oficiais, não o conteúdo integral dos notebooks. São referências externas, não instruções de sistema nem ferramentas que os agentes já possam usar. No SQLite de desenvolvimento o catálogo é preparado na inicialização; no PostgreSQL é necessário executar uma vez (a operação é idempotente):

```powershell
C:\Projetos\ADA\.venv\Scripts\python.exe C:\Projetos\ADA\ada\backend\scripts\prepare_cookbooks.py
```

Antes de habilitar o executor: integrar chave e modelo, isolar o runtime do Claude Code, controlar ferramentas e acesso a arquivos, impor limites de custo/tempo/delegação no servidor, conectar aprovações e eventos, e validar com testes reais. A opção de orçamento no plano não é, por si só, um limite efetivo de cobrança. O chat ainda não usa os planos de delegação; ele agora usa o SDK para respostas quando o provedor está configurado como Claude.


## Claude Agent SDK no fluxo de conversa

Com `MODEL_BASE_URL` da Anthropic, a Ada seleciona automaticamente `claude_agent_sdk`, salvo se `ADA_MODEL_PROVIDER` estiver definido. A criação de agentes e as conversas usam o SDK. Configure `MODEL_API_KEY` e `MODEL_NAME` no `.env`, depois reinicie o backend. Outros provedores continuam disponíveis com `ADA_MODEL_PROVIDER=openai_compatible`. Não envie sua chave pelo chat.

O runtime recebe uma pasta temporária, sem ferramentas habilitadas, sem configurações locais do Claude e sem persistência da sessão do SDK; o histórico de conversa da Ada é reenviado em cada chamada. O teto `ADA_SDK_MAX_BUDGET_USD` (padrão 0.25) é por invocação do SDK, não um limite global de conta ou de conversa. A criação pode tentar várias vezes para validar o JSON; use limites de gasto também no provedor. A execução pode falhar se atingir tempo ou orçamento. O SDK não está conectado a ferramentas, conexões OAuth, subagentes ou planos; ainda não há execução automática de agentes. A compatibilidade real de modelo e autenticação precisa ser testada com chave válida.

A fonte oficial de três arquivos de exemplo e sua licença MIT estão em `backend/cookbook_sources`, fixados por commit, e também foram preparados no banco como dados de referência. Eles não são importados, executados ou incluídos automaticamente nos prompts. `GET /cookbooks/source-files` lista metadados e hashes. Para atualizar um banco já existente, execute novamente `backend/scripts/prepare_cookbooks.py`.


## Orquestração de tarefas no chat

No provedor `claude_agent_sdk`, mensagens enviadas a um agente criado passam por planejamento, até duas análises especializadas independentes e síntese. As especialidades são pesquisa, revisão de código e observabilidade; o agente principal exerce a coordenação. São chamadas separadas ao SDK, sem ferramentas, que usam apenas dados do histórico recente e da mensagem. Não acessam páginas, repositórios, documentos carregados, GitHub ou CI. Sem dados suficientes, o agente deve pedir material ou explicar a limitação. O modo OpenAI compatível continua usando o chat simples.

As etapas e, no máximo, os primeiros 4.000 caracteres de cada resultado ficam registrados na execução e visíveis em Atividade. As conversas existentes podem ser selecionadas na barra lateral, identificadas pela primeira mensagem; ainda não há renomeação ou ramificação de sessões. Isso não habilita persistência dos arquivos locais de sessão do Claude. O cancelamento impede passos posteriores, mas não interrompe uma chamada ao SDK que já esteja em curso.

`ADA_TASK_BUDGET_USD` define um orçamento compartilhado por tarefa de chat (padrão US$ 0,50, máximo configurável US$ 5). Antes de cada chamada, o valor restante também é limitado por `ADA_SDK_MAX_BUDGET_USD` (padrão US$ 0,25). O custo informado pelo SDK é somado após cada chamada; se o SDK não informar o custo, a execução é interrompida. Valores já consumidos podem ultrapassar um limite durante uma chamada, e falhas de comunicação podem impedir o registro de seu custo; use também controles de gastos do provedor. A criação inicial de um agente ainda não usa o orçamento compartilhado e pode repetir chamadas de validação. Um alerta é registrado na atividade ao atingir 80% do orçamento; ainda não há aviso em tempo real nem botão para ampliar o orçamento.

As informações acima substituem trechos anteriores que descrevem o chat SDK como sem delegação. A execução real ainda precisa ser validada com chave, modelo e saldo. Integrações externas, ferramentas, isolamento do runtime para habilitá-las, continuidade dos planos e execução com aprovações ainda são pendências.


## Recursos para a demo de produto

Ao criar um agente, habilite explicitamente a consulta aos documentos e/ou a pesquisa na web na revisão. A pesquisa usa `WebSearch` do Claude Agent SDK, até três chamadas por tarefa via hook, e pode gerar custos adicionais; só é habilitada para uma etapa de pesquisa com política `allowed`. Verifique o gasto no provedor. O suporte a fontes depende do retorno real do modelo e ainda precisa ser validado com uma chave.

Quando autorizados, documentos enviados são selecionados por busca lexical, com até seis trechos recentes como alternativa; não é uma leitura completa, e tabelas/imagens de PDFs podem não ser extraídas. Os especialistas de produto e design usam instruções próprias inspiradas nos padrões pesquisados; as skills oficiais não foram instaladas nem ativadas automaticamente. O planejamento aceita até quatro especialistas por tarefa (configurável por `ADA_MAX_SUBAGENTS`, de 1 a 4), incluindo papel temporário personalizado.

A resposta mais recente de cada conversa pode ser baixada como Markdown, Word, PDF, slides ou planilha. Os últimos quatro são conversões simples de texto, não substituem as skills de documentos da Anthropic. Slides podem resumir ou truncar linhas longas. Blocos HTML podem ser visualizados em iframe sem scripts e sem acesso externo; não é uma sandbox de desenvolvimento ou execução completa.

Conversas podem ser renomeadas e ramificadas; a cópia inclui as mensagens existentes, não as execuções ou aprovações. Para PostgreSQL já existente, execute `backend/scripts/prepare_conversation_labels.py` uma vez. Integração real de GitHub, Figma, Drive, cliente MCP, observabilidade de CI, sandbox para código, skills oficiais e autenticação multiusuário ainda precisam ser concluídos. Recursos que dependem de API e o comportamento real das ferramentas continuam sem teste até haver uma chave válida.


## Proposta revisável em repositório público

Em Conexões → GitHub → Revisar arquivo, informe um repositório público e o caminho de um arquivo UTF-8 existente (até 200 KB). A Ada lê a branch padrão, permite edição manual e apresenta um diff. A opção de pedir sugestão ao modelo também prepara o diff, sem publicá-lo; depende da chave e ainda não foi validada com o provedor real. A revisão atual é para um arquivo por vez; não clona nem executa o repositório, e não é acionada automaticamente pelo chat do agente.

Para criar um PR é necessário reconectar o GitHub pela opção de autorizar PRs em repositórios públicos. Isso solicita o escopo OAuth `public_repo`, que concede acesso amplo aos repositórios públicos acessíveis à conta; é mais abrangente do que selecionar um repositório em uma GitHub App. A ação final é o botão Criar branch e PR, que cria um PR em rascunho, sem alterar a branch base. Antes disso são verificados a permissão de escrita e o SHA da branch padrão; se mudou, prepare novamente. Um erro durante a publicação pode deixar uma branch ou um PR criados apesar da resposta ter falhado: verifique no GitHub antes de repetir. A proposta e a autorização expiram ou são perdidas ao reiniciar o processo. O processo não acessa repositórios privados nem suporta forks. O fluxo de código do agente aceita vários arquivos e tenta executar verificações na sandbox.

O teste do Docker pode exigir permissão para acessar o daemon no computador. O contêiner de snippets existente ainda não foi validado para executar projetos ou hospedar prévias, e nunca deve receber tokens, o socket do Docker ou diretórios sensíveis do host. Antes de expor o backend publicamente é necessário implementar autenticação e autorização por usuário e proteger o fluxo OAuth.


## Google Workspace, MCP e sandbox local

Na tela Conexões, Drive, Docs, Sheets, Gmail, Calendar e Classroom possuem consentimentos separados. Cadastre `http://127.0.0.1:8000/connections/drive/callback` no cliente OAuth Google; todos os serviços Google usam esse callback. A API correspondente precisa estar habilitada e, se o aplicativo estiver em testes, a conta precisa estar entre os usuários de teste. Após cada autorização, a sessão fica somente na memória do servidor e desaparece ao reiniciar ou expirar. Não há renovação automática.

As consultas são iniciadas pelo usuário: Drive lista arquivos visíveis a `drive.file`; Docs lê o texto de um documento por link/ID; Sheets lê até 50 linhas e 26 colunas de um intervalo; Gmail lista até 10 cabeçalhos sem corpo; Calendar lista próximos eventos da agenda principal; Classroom lista turmas ativas. Docs e Sheets requerem escopos de leitura para todos os arquivos desses tipos aos quais a conta tem acesso; Gmail solicita `gmail.metadata`, que o Google classifica como restrito. O compartilhamento de um resultado com o modelo é uma escolha explícita no botão Usar na conversa, seguida do envio da mensagem. Não há envio de e-mail nem alteração dos serviços. Acesso real e consentimento ainda devem ser validados com cada conta. Aplicações públicas podem exigir verificação OAuth.

A Ada possui um servidor MCP **interno**, com duas ferramentas, no processo do Claude SDK: consulta aos documentos indexados do agente e leitura de uma página pública. Podem ser habilitados individualmente nas permissões ao criar um agente. Não são servidores MCP externos e não fornecem acesso automático a Google, GitHub ou sandbox. São configurados usando `ClaudeSDKClient`; o handshake real com o modelo ainda exige teste com chave.

Em Conexões → Sandbox é possível verificar Docker e, por ação explícita, executar manualmente um trecho Python no contêiner restrito. A imagem local `python:3.12-alpine` precisa existir; o aplicativo não baixa imagens automaticamente. A sandbox executa sem rede, com limites de recursos e sem montar o projeto. O agente não a chama automaticamente e ela não executa aplicações web completas. Ainda são necessários testes no daemon real, integração de aprovações do agente, arquitetura segura para projetos e um executor remoto (VM ou serviço equivalente). Nada aqui cria recursos de nuvem ou garante isolamento contra todo código malicioso.


## Criação manual no Google Workspace

A tela Conexões não mostra identificadores técnicos de escopo. Cada serviço oferece consulta e, após autorização específica quando necessária, uma ação de criação iniciada pelo usuário: upload de até 5 MB no Drive; novo documento Docs; planilha Sheets a partir de linhas com tabulação; rascunho no Gmail, sem envio; evento sem convidados no calendário principal; e turma Classroom no estado PROVISIONED, sem ativação. O Drive usa `drive.file`, portanto sua listagem não inclui necessariamente arquivos que não tenham sido compartilhados com o aplicativo. Docs e Sheets usam `drive.file` para os arquivos criados pelo aplicativo e seus escopos de leitura anteriores para consulta. Gmail e Classroom solicitam escopos restritos ou sensíveis; verifique as exigências OAuth do Google para uso público.

O Figma permite consultar um arquivo e solicitar uma imagem temporária de uma camada pelo ID, quando conectado. O GitHub mantém o fluxo de alteração de arquivo e PR em rascunho para repositórios públicos. Não são implementações completas de todas as APIs, nem ferramentas de escrita executadas automaticamente pelos agentes. Nunca há envio automático de e-mail. Foram testados o protocolo e respostas simuladas; as autorizações e operações reais precisam ser testadas com as contas e serviços. Erros ou interrupções após uma solicitação de criação podem ter criado o item; confira o serviço antes de tentar novamente.

## Ambiente de trabalho para código (atualização)

No chat de um agente, **Criar código** recebe a tarefa e um repositório público do GitHub. O modelo lê até cinco arquivos selecionados e propõe alterações em até seis. O backend baixa o arquivo do commit público sem enviar o token ao serviço de arquivo, cria uma cópia temporária e aplica as mudanças em um contêiner Docker descartável. O código e o diff ficam disponíveis para inspeção, mas não é necessário abri-los na Ada: após a autorização explícita, o PR em rascunho pode ser revisado no GitHub. Os limites anteriores de um arquivo e validação exclusivamente sintática foram substituídos para esse fluxo.

Crie a imagem no computador com Docker Desktop ligado:

```powershell
cd C:\Projetos\ADA\ada
docker build -t ada-workspace:local .\sandbox
```

Reinicie o backend depois de atualizar o código. A construção da imagem requer acesso à internet; a execução da sandbox não tem rede, credenciais nem diretórios do usuário montados. O contêiner roda sem privilégios, com limites de memória, CPU, processos, armazenamento e tempo; Docker não é uma VM dedicada e não é uma fronteira completa para código malicioso. A execução é local e não deve ser exposta a usuários externos sem autenticação e isolamento adicionais.

O executor verifica sintaxe Python e tenta `npm test` se o projeto declarar esse script, além de `python3 -m unittest discover` quando encontra arquivos `test_*.py`. Ele não instala dependências do projeto; testes que precisam delas podem falhar. A ausência de verificações é indicada. O modelo ainda não usa um terminal interativo geral; pode propor até duas correções com base nas falhas verificadas. Os resultados não equivalem a uma revisão de segurança ou garantia de funcionamento. O arquivo do repositório tem limites de tamanho; arquivos simbólicos não são extraídos. Sem imagem, Docker ou cópia acessível, os testes são marcados como indisponíveis.

O PR exige autorização GitHub `public_repo`, permissão de escrita e a branch padrão sem mudanças desde a proposta. O botão para criá-lo é uma autorização explícita; a branch base não é integrada automaticamente. Os rascunhos expiram após 30 minutos e são perdidos ao reiniciar o backend. Sem conexão com o modelo, o fluxo de geração não funciona. Essas notas atualizam as descrições anteriores desta página sobre a sandbox e o estúdio de repositório.

## Instalação local em Linux

O repositório pode ser executado em uma máquina Linux com Python 3, Node.js/npm, Git e Docker ou Multipass. Após clonar o repositório:

```bash
bash backend/scripts/setup-linux.sh
# Edite .env: provedor, modelo e chave quando estiver pronto.
make dev
```

O frontend fica em `http://127.0.0.1:5173` e o backend em `http://127.0.0.1:8000`. O script preserva um `.env` existente. SQLite é o padrão local; para usar Supabase, configure PostgreSQL e execute as migrações documentadas anteriormente. Integrações OAuth exigem suas próprias credenciais.

### Executor local com VM Ubuntu (experimental)

Instale [Multipass](https://canonical.com/multipass), confira `multipass version` e a virtualização do computador. Defina `ADA_SANDBOX_BACKEND=multipass` no `.env` e reinicie o backend. A cada validação a Ada tenta criar uma VM Ubuntu 24.04 com 2 CPUs, 2 GB de RAM e disco de até 8 GB; a imagem e os pacotes exigem internet e a primeira inicialização pode levar vários minutos. O teste usa um usuário não privilegiado com regras de saída IPv4 e IPv6 bloqueadas e não recebe token GitHub, API do modelo ou pastas do computador. A VM é excluída ao terminar. O processo usa uma VM por vez; se a limpeza falhar, procure instâncias `ada-task-*` com `multipass list` e remova apenas aquela instância com `multipass delete --purge NOME`.

Para verificar sem consumir API, rode `ADA_SANDBOX_BACKEND=multipass .venv/bin/python backend/scripts/check_code_sandbox.py`. No PowerShell: `$env:ADA_SANDBOX_BACKEND='multipass'; ..\.venv\Scripts\python.exe backend\scripts\check_code_sandbox.py` (ajuste o caminho ao ambiente virtual). Para usar o executor Docker já existente, selecione `ADA_SANDBOX_BACKEND=docker` e construa a imagem conforme a seção anterior.

Este executor ainda executa as verificações predefinidas descritas acima: não é um terminal interativo para o modelo, não instala dependências do repositório e não oferece preview. A regra IPv4 limita o processo de testes, mas ainda são necessárias validações adicionais de isolamento de rede e do hipervisor antes de executar código hostil ou disponibilizar o recurso para terceiros. O backend atual é local e não oferece autenticação multiusuário: não o exponha na internet. A interface de execução permite acrescentar outro backend de nuvem no futuro; não há worker remoto ou deploy seguro implementado ainda.


### Ciclo de correção no ambiente local

Ao criar código, o agente seleciona arquivos de um repositório público, prepara a alteração e executa as verificações disponíveis. Em caso de falha, recebe a saída dos testes e pode propor até duas correções, respeitando o orçamento configurado. No Multipass, as verificações de uma tarefa usam a mesma VM; a cada tentativa uma cópia limpa é preparada. Um PR não pode ser publicado se as verificações retornarem falha. Sem teste disponível ou com sandbox indisponível, a interface informa o resultado e não afirma que os testes passaram.

Este é um ciclo limitado, não um terminal interativo geral: os comandos são pré-definidos, o agente lê apenas os arquivos selecionados e não instala as dependências do repositório. As propostas aceitam até seis arquivos e somente repositórios públicos. A rede do usuário da sandbox está bloqueada; a VM é criada e destruída por tarefa. Consulte os resultados dos testes antes de autorizar um PR. Para usar com terceiros na nuvem ainda faltam autenticação, autorização por usuário e isolamento do executor.
