# Ada — laboratório de agentes

Protótipo local para criar agentes especializados, conversar com eles, organizar contexto e acompanhar execuções. A orquestração pode usar especialistas temporários. O fluxo de código prepara alterações em repositórios públicos, executa verificações e permite criar um PR em rascunho após uma ação explícita do usuário.

## Instalação local

Requisitos: Python 3.11+, Node.js ^20.19.0 ou >=22.12.0. Docker ou Multipass são opcionais para a execução de código. A chave de modelo e cada configuração OAuth pertencem ao usuário e ficam no `.env`, nunca no Git.

No PowerShell, na raiz do projeto:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
cd frontend
npm ci
cd ..
```

Edite `.env`. Em dois terminais separados:

```powershell
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm run dev
```

Use `http://127.0.0.1:5173` no navegador. A API tem diagnóstico em `http://127.0.0.1:8000/health`.

Em Linux: `bash backend/scripts/setup-linux.sh`, configure `.env` e execute `make dev`. O script de desenvolvimento depende de Bash, setsid e de portas 8000/5173 disponíveis.


## Teste inicial com Gemini

Para testar criação e conversa, configure no `.env` uma chave do Google AI Studio e um modelo disponível na cota do seu projeto:

```dotenv
ADA_MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
MODEL_NAME=gemini-2.5-flash
MODEL_API_KEY=COLOQUE_SUA_CHAVE_AQUI
ADA_INPUT_USD_PER_MTOK=1
ADA_OUTPUT_USD_PER_MTOK=10
```

Reinicie o backend. As taxas acima são estimativas conservadoras para o limite interno, não são uma promessa de preço nem impedem cobranças no Google. Confira a modalidade e a cota no AI Studio. A integração compatível não executa a orquestração nativa Claude nem suas ferramentas MCP. Use dados de teste: o Google pode usar dados enviados na modalidade gratuita para melhorar produtos.

## Memória por agente

No painel **Contexto → Memória do agente**, consulte, edite ou esqueça notas e ative/desative a memória e sua atualização automática. Notas aprovadas são recuperadas entre conversas do mesmo agente, por relevância lexical e recência, limitadas a oito por chamada. Não são compartilhadas automaticamente entre agentes.

Após uma resposta, a atualização automática pode usar uma chamada adicional ao modelo para extrair até três fatos duradouros explicitamente declarados na mensagem do usuário. Exige uma evidência literal da mensagem; ignora evidência inventada e formatos comuns de credenciais. A extração usa o orçamento da tarefa e pode ser pulada se o orçamento estiver curto. Falha da extração não invalida a resposta. O usuário deve revisar o que foi salvo: a extração por modelo ainda depende de avaliação real e pode errar.

Notas manuais não são sobrescritas automaticamente. O modelo pode substituir uma nota automática anterior quando o usuário declarar uma atualização. A evidência e a conversa de origem são armazenadas; os eventos de edição/exclusão registram metadados sem manter uma cópia do texto apagado. Esquecer uma nota não remove mensagens originais da conversa.

A ferramenta opcional `mcp_memoria` também permite propor notas explicitamente; essas propostas ficam pendentes para revisão. A memória serve de contexto e não concede ferramentas, permissões ou aprovação para publicar. Não use para senhas, chaves ou dados sensíveis. `ADA_MEMORY_AUTOMATIC=false` desliga a extração automática em toda a instalação.

## Banco de dados

SQLite é o padrão local e recebe as tabelas novas automaticamente no reinício. PostgreSQL/Supabase é opcional: configure `ADA_DATABASE_BACKEND=postgres`, `SUPABASE_DATABASE_URL` e `SUPABASE_DB_CA_CERT` e execute, explicitamente:

```powershell
.\.venv\Scripts\python backend\scripts\prepare_postgres.py
```

O comando prepara as migrações correntes, incluindo títulos, catálogo e memória. Não migra dados do SQLite. A aplicação exige versão 7 do esquema. Não execute scripts de migração sem backup de um banco de produção.

## Sandbox e PR

Para Docker: `docker build -t ada-workspace:local ./sandbox`. Selecione `ADA_SANDBOX_BACKEND=docker`. Para VM Ubuntu, instale Multipass e selecione `multipass`; a primeira inicialização pode demorar. O usuário da tarefa tem saída IPv4/IPv6 bloqueada. O executor confirma ferramentas e regras de rede antes de usar a VM. Verificações sucessivas usam cópias limpas e encerram os processos do usuário da tarefa antes/depois; falha de exclusão da VM é reportada e bloqueia o slot até intervenção/reinício. Verifique `multipass list` e limpe somente a VM específica quando necessário.

A execução tem comandos de verificação predefinidos, até duas tentativas de correção e não instala dependências do repositório. Não oferece terminal interativo geral. Dependências ausentes podem impedir testes. O PR é bloqueado se as verificações falharem; resultados indisponíveis ou sem testes são informados. Criar um PR não integra mudanças na branch principal. Propostas e credenciais OAuth continuam em memória de processo e expiram/somem ao reiniciar.

## Proteção e limites

Esta instalação é **local, de uma pessoa e um processo**. Toda a API valida host, endereço do cliente e origem, bloqueando sites externos. Não é autenticação multiusuário: outros processos locais pertencem ao mesmo limite de confiança. Não exponha o servidor em Render, túnel ou proxy público sem implementar autenticação e autorização. A sandbox é uma camada de isolamento e ainda exige testes adicionais para código hostil.

Apenas uma tarefa de modelo por vez é aceita pelo processo. Criação, conversa, sugestão e código recebem um orçamento por tarefa. Limites monetários do SDK são controles do provedor e não substituem o saldo/limite na Console. No provedor compatível, uma chave paga exige taxas explicitamente configuradas para estimativa; custo desconhecido impede novas chamadas na tarefa. Não há roteamento Haiku/Sonnet ainda.

GitHub, Figma e documentos Google estão disponíveis ao chat via MCP interno quando a conexão OAuth e os recursos do agente estão autorizados. O fluxo de código e a publicação de PR continuam separados das ferramentas de leitura. O deployment de nuvem e isolamento multiusuário ainda não foram implementados.

## Verificação

```powershell
cd backend
..\.venv\Scripts\python -m pytest -q
```

No frontend, `npm run build`. Sem consumir modelo: `python backend/scripts/check_vm_iteration.py` verifica duas tentativas na VM. Atualizações no runner Docker exigem reconstruir a imagem.

Notas de desenvolvimento anteriores estão em `docs/NOTAS_HISTORICAS.md`; podem descrever estados antigos.


## MCP e especialistas nativos

Em Contexto → Recursos para o agente, autorize repositórios no formato `usuário/repositório` e IDs de arquivos Figma, Docs, Sheets e Drive. Salvar cria uma nova versão do agente. As ferramentas MCP usam os conectores da Ada e conferem a permissão ativa em cada chamada; não são os servidores MCP externos oficiais. Drive consulta metadados; Docs lê texto; Sheets lê A1:Z50; Figma devolve estrutura; GitHub lê arquivos públicos, árvore e resumos de CI. O CI não inclui o log completo. Desconectar o serviço impede consultas futuras.

O modelo produz um plano e o coordenador chief of staff recebe AgentDefinition por especialista. A execução ocorre pela ferramenta nativa Agent do SDK, com restrição de ferramentas, turnos, orçamento e ganchos de auditoria. Não há acesso geral a Bash ou arquivos locais. Especialistas não recebem permissão de delegar. Invocações não planejadas e além do limite são bloqueadas. O teste com SDK simulado valida os contratos; execução real ainda exige chave e deve ser ensaiada antes da demo.

### Observabilidade e reviewer

O especialista de observabilidade investiga status de CI, jobs e passos com o MCP GitHub do repositório autorizado. Ele relata evidência e hipóteses; não corrige produção nem altera workflows automaticamente.

Em Contexto → Revisões do repositório, prepare uma rotina desligada e use Revisar agora para uma execução explícita. Ativar recorrência autoriza chamadas pagas enquanto o backend estiver ligado, com OAuth ativo e dentro do limite diário (por padrão, uma revisão em 24h). Intervalos são de 1 a 168 horas. Commits sem mudança após uma revisão concluída são ignorados sem chamar o modelo. O relatório deve declarar commit, identificação da revisão anterior e achados estruturados; continuidade inválida ou falha pausa a recorrência. Relatórios ficam no banco. O código do repositório é tratado como dado não confiável.

O agendador roda no processo local a cada 30 segundos. Não é um serviço independente ou SLA de monitoramento. Uma interrupção pode exigir verificar rotinas marcadas como running antes de reabilitar; não force tentativas concorrentes. Não há publicação automática de PRs, alterações remotas ou notificações enviadas a terceiros.

Essas implementações adaptam padrões do cookbook: não executam automaticamente os arquivos de referência copiados. Playwright e servidores MCP externos continuam fora desta integração.


### Código pela conversa

A tela manual de edição não aparece mais em Conexões. Depois de conectar o GitHub, autorize o repositório no Contexto do agente e, se desejar implementação, marque a opção de preparar alterações em sandbox. Peça a mudança na conversa. O coordenador pode chamar a ferramenta MCP `preparar_codigo`; o conteúdo da tarefa vem da mensagem atual do usuário, não de instruções encontradas no repositório.

A proposta aparece como cartão no chat, com diff opcional e botão para autorizar o PR. O modelo não publica automaticamente. No máximo uma preparação é iniciada por mensagem; chamadas internas compartilham o orçamento da tarefa. O tempo de espera do SDK é ampliado para esse fluxo porque a VM pode precisar inicializar. As propostas continuam temporárias e expiram em 30 minutos.

### Retorno OAuth

Use a URI exibida em Configuração do retorno OAuth no cartão da conexão. O Google usa `/connections/drive/callback` também para Docs, Sheets, Gmail, Calendar e Classroom. Em modo de testes, inclua a conta utilizada na lista de usuários de teste do Google Auth Platform. Ao voltar pelo histórico do navegador, a tela libera os botões e recarrega as conexões. Reiniciar o backend exige novas autorizações porque os tokens estão em memória.

## Fluxo de criação

Escolha nome e personagem, descreva o objetivo e o contexto em uma página dedicada e revise a proposta antes de criar. Não há entrevista obrigatória. Orientações aceitam texto livre. Ferramentas e autorizações ficam em uma seção expansível da revisão; o painel lateral do chat começa recolhido. Mudanças no prompt de criação afetam agentes novos, não alteram as instruções dos agentes existentes.

## Conversas e arquivos

Novos agentes recebem ferramentas internas de leitura, documentos, memória e pesquisa, respeitando políticas explicitamente negadas. Conectores externos continuam vinculados aos recursos autorizados do agente e ao OAuth. A Ada decide quando usar as ferramentas disponíveis no SDK Claude; o caminho Gemini continua sem essa orquestração.

Peça “Exporte a última resposta em PDF” (ou Markdown, Word, slides, planilha). Esse comando explícito funciona também no Gemini; o SDK Claude dispõe da ferramenta gerar_arquivo para pedidos equivalentes. O arquivo usa uma resposta já salva, não cria uma apresentação ou análise nova. O link aponta para a mensagem original mesmo após novas respostas.

Excluir conversa remove mensagens, títulos, execuções, aprovações e memórias cuja origem registrada é aquela conversa e invalida propostas temporárias de código. Notas sem origem nessa conversa permanecem. O contexto usa resumo persistente e recuperação textual da conversa, além das mensagens recentes.

## Continuidade de contexto e perfis

O contexto é preparado tanto para Gemini compatível quanto para Claude SDK. Acima de 24 mensagens ou aproximadamente 24 mil caracteres, a Ada pode resumir incrementalmente mensagens antigas. Mantém o resumo atual, revisão e último ID processado no banco; os originais não são apagados. Cada atualização usa uma chamada dentro do orçamento compartilhado, com lote limitado. Conversas muito grandes podem precisar de várias interações para atualizar o resumo completo. Se o orçamento não permitir, a atualização é adiada.

Trechos antigos são recuperados por correspondência textual, limitados à conversa atual e com IDs de origem. Não é busca semântica por embeddings. O SDK também oferece consultar_historico para buscar detalhes ausentes. Resumos e trechos são contexto histórico, não concedem ferramentas ou aprovação.

Excluir conversa remove seu resumo e as memórias originadas dela. Opções do agente → Excluir agente remove o perfil inteiro e seus dados associados: conversas, resumos, notas, documentos, versões, execuções e rotinas. Conexões OAuth globais não são apagadas. Ações destrutivas pedem confirmação e ficam bloqueadas durante tarefas ativas.

PostgreSQL/Supabase: execute backend/scripts/prepare_postgres.py antes de reiniciar uma instalação antiga, para aplicar a migração 005_context.sql (com RLS). SQLite é atualizado automaticamente.

## Workflows salvos

Contexto → Workflows permite criar, editar (gerando versão), executar e consultar histórico. O objetivo fica em linguagem natural; a orquestração Claude escolhe os passos e especialistas temporários. Salvar não consome modelo. O SDK pode salvar workflows pelo chat via salvar_workflow; executar e ativar recorrência continuam ações explícitas na interface.

A recorrência é por intervalo em horas, começa desligada e tem limite de execuções em 24 horas, inclusive manuais. Executa no processo local/servidor enquanto ele estiver ativo. Cada tentativa cria uma conversa e registra versão, resultado ou falha. Falhas pausam recorrência; ocupação do executor adia sem consumir o limite diário. Após reinício, workflows interrompidos são marcados como falhos e pausados. Não há execução em múltiplos workers, retomada no meio de uma tarefa ou gatilhos por webhook ainda.

## Anexos na conversa

O botão + aceita até três arquivos por mensagem, de até 4 MB cada. PNG, JPEG e WebP são enviados como imagens ao modelo; documentos UTF-8, CSV, JSON, código e PDFs com texto são lidos como dados. Não são executados. Limite inicial de texto extraído: 12 mil caracteres por arquivo. PDFs digitalizados não têm OCR. O modelo selecionado precisa suportar visão para imagens. A validação de cabeçalho da imagem não substitui a validação do provedor.

Anexos ficam vinculados à conversa e à mensagem; exclusão do chat/perfil os remove. Ramificar copia os anexos. A recuperação textual também consulta seus textos extraídos. O contexto visual usa até as três imagens atuais ou mais recentes da conversa; não há busca semântica de imagens. Anexos pendentes não enviados expiram após 24 horas e são limpos no próximo upload.

## Hospedagem privada inicial

O runtime Claude agora integra sessões persistentes, skills confiáveis, coordenação dinâmica, arquivos por conversa, perguntas, aprovação de análise Python, streaming e interrupção. A API Anthropic continua sendo necessária para executar modelos Claude. Consulte [o funcionamento, limites e atualização do esquema](docs/RUNTIME_SDK.md).

Dockerfile e render.yaml preparam frontend e backend no mesmo serviço, com autenticação HTTP Basic sobre HTTPS. É uma instalação privada para um proprietário: todas as pessoas que receberem a credencial compartilharão os dados. Não é autenticação multiusuário. Veja docs/DEPLOY_PRIVADO.md. O modo local continua disponível.
