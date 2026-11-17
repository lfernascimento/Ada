# Log de uso de IA

## 16/11/2026 — núcleo da Ada
- Pedido: construir a primeira etapa a partir dos documentos fornecidos.
- Gerado: spike de modelo, backend, interface, validação, persistência e testes.
- Verificação: consulte o resultado dos comandos de teste e a execução de modelo informados na conversa.
- Decisões: SQLite com sqlite3 da biblioteca padrão para reduzir dependências; chat com resposta inteira, como previsto no plano de corte.
- Falhas e correções: nenhuma registrada ainda. Não foram atribuídas verificações ou decisões ao usuário sem evidência.

Verificação executada nesta sessão: `npm run build` concluiu; `pytest -q` concluiu com 5 testes aprovados. O serviço iniciou, mas a conexão HTTP entre processos deste ambiente não foi concluída. O teste prolongado com modelo local precisa ser confirmado antes de considerar a etapa pronta. Não foi criada tag de conclusão.

## 16/11/2026 — infraestrutura e interface
- Pedido: preparar a infraestrutura das próximas etapas e remodelar o design mantendo a paleta; integração do modelo e testes ficam para depois.
- Gerado: estruturas persistentes de execuções, eventos, aprovações e documentos; ferramentas; painel de detalhes, contexto, atividade e versões; interface em três áreas.
- Verificação: build do frontend e verificação técnica básica dos endpoints, conforme resultados da sessão.
- Pendente: ciclo automático do modelo, evolução por feedback, streaming, medição de custos e validação completa de segurança e demo.

## 16/11/2026 — personagem primeiro e conexões
- Pedido: escolher personagem antes da conversa e preparar integrações; Claude Sonnet 5.5 definido como modelo pretendido.
- Gerado: escolha inicial de flor, ratinho e tucano; página de conexões; base OAuth e consultas de leitura; exemplo de configuração do modelo.
- Limitações: autenticação OAuth real, modelo, MCP, sandbox, orquestração de subagentes e uso de conexões por agentes ainda precisam de implementação e validação conforme README.

## 16/11/2026 — preparação do Supabase
- Pedido: preparar a conexão com o projeto Supabase criado pelo usuário, preservando credenciais fora do chat.
- Gerado: verificação somente leitura com TLS autenticado e rascunho de esquema PostgreSQL, sem executar migração nem modificar banco remoto.
- Pendente: configurar URL e CA localmente, testar conexão real; adaptar consultas e transações do backend, implementar autenticação e migração com testes de concorrência e integridade.

## 16/11/2026 — adaptação PostgreSQL
- Pedido: adaptar o backend da Ada ao projeto Supabase.
- Gerado: suporte a PostgreSQL com TLS verificado, migrações de esquema, RLS e opção SQLite.
- Verificação: testes SQLite; execução isolada PostgreSQL com criação, documento, versão, aprovações simultâneas; esquema temporário removido; inicialização no esquema definitivo.
- Limitações: sem autenticação multiusuário, políticas por usuário, migração automática de SQLite ou deploy público.
