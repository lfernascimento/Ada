# Runtime agêntico nativo

Com `ADA_MODEL_PROVIDER=claude_agent_sdk`, as conversas usam o ciclo nativo do SDK. A Ada fornece objetivo, perfil, contexto e ferramentas; o modelo decide os passos, quando usar uma skill, quando atualizar um plano e quando delegar. Uma tarefa simples pode ser resolvida em uma única execução SDK, sem uma chamada obrigatória de planejamento.

## Capacidades integradas

- Sessão por conversa, com transcript completo do SDK persistido em PostgreSQL/Supabase ou SQLite pelo protocolo `SessionStore`. Inclui resultados de ferramentas e sessões de especialistas. O armazenamento passa pela suíte oficial de conformidade do SDK.
- Retomada depois de reiniciar o servidor, quando perfil, modelos e diretório lógico continuam compatíveis. Mudanças no perfil/modelos reiniciam a sessão e usam o histórico visível como contexto. Trocar de sistema operacional ou de caminho também reconstrói o contexto.
- Especialistas nativos de pesquisa, produto, design, revisão, observabilidade e tarefas específicas, nos modelos principal e leve configurados. A coordenação escolhe durante a execução; os especialistas não delegam novamente nem publicam alterações.
- Skills confiáveis do projeto: `investigar`, `analisar-dados`, `construir-entrega`. Arquivos enviados pelo usuário não são instalados como skills. Não carregamos instruções, hooks, plugins ou MCPs do computador pessoal.
- `Read`, `Write`, `Edit`, `Glob` e `Grep` em uma área restrita à conversa; `TodoWrite` para um plano visível e revisável. Anexos ficam em `inputs/`. Arquivos de entrega persistem no banco e podem ser baixados por links produzidos pela ferramenta `entregar_arquivo`.
- Perguntas durante a execução pela ferramenta `perguntar_usuario`, respondidas diretamente no chat. O modelo pode continuar com a resposta sem iniciar outra tarefa independente.
- `executar_python` para análise com biblioteca padrão, após aprovação no chat. Transfere somente os caminhos selecionados à sandbox; devolve stdout, código de saída e arquivos de `outputs/`. Localmente usa Docker; remotamente usa o executor Render já integrado, quando habilitado. O fluxo existente de alteração de repositório continua podendo usar Multipass. Não há shell do servidor disponível ao modelo.
- Texto em partes, eventos de ferramentas, delegação, plano e compactação no chat. Blocos internos de raciocínio não são transmitidos. A resposta final confirmada substitui a prévia.
- Interrupção da tarefa em andamento. Desconectar a transmissão também solicita cancelamento do ciclo SDK. Um executor isolado que já iniciou pode terminar a sua rotina de encerramento e gerar cobrança até a remoção/TTL; cancelar não desfaz ações já executadas nem consumo anterior.

## Contexto, memória e exclusão

A compactação nativa do SDK trabalha sobre o transcript da sessão, que agora é persistente. O resumo e o recall da Ada continuam disponíveis: `consultar_historico` recupera mensagens originais e a busca de documentos consulta os arquivos do perfil. Isso é diferente das notas de memória, que guardam preferências e decisões entre conversas.

Excluir uma conversa apaga seu transcript SDK, arquivos, resumo, execuções e notas originadas nela. Excluir o perfil apaga também as outras conversas e seus recursos internos. Ramificar copia histórico e arquivos, começando uma sessão SDK independente. Uma execução que falha ou é cancelada perde o ponteiro de retomada; não reutilizamos uma mensagem não confirmada como se tivesse sido concluída.

## Configuração e limites

```dotenv
ADA_MODEL_PROVIDER=claude_agent_sdk
MODEL_API_KEY=CONFIGURE_PRIVADAMENTE
MODEL_NAME=sonnet
ADA_LIGHT_MODEL=haiku
ADA_SDK_ORCHESTRATION=native
ADA_SDK_TASK_MAX_TURNS=48
ADA_SDK_TASK_MAX_TOOLS=80
ADA_SDK_TASK_TIMEOUT_SECONDS=900
ADA_SDK_MAX_BUDGET_USD=0.25
ADA_TASK_BUDGET_USD=0.50
```

Os aliases precisam estar disponíveis na conta. Uma chave e um saldo atendem os dois modelos. `planned` mantém a coordenação anterior como alternativa explícita. O gateway compatível com Gemini continua disponível, com suas capacidades próprias; ele não executa o runtime Claude.

O teto SDK conta o consumo estimado da execução, inclusive subagentes. O orçamento da tarefa inclui também chamadas auxiliares de memória e contexto. Em sessões retomadas, a Ada desconta o total anterior para não somar novamente o mesmo consumo. Esses números são estimativas do cliente e podem ultrapassar o teto na resposta que o cruza; não substituem os limites de cobrança da conta. Sem custo final confiável, a execução é marcada como custo desconhecido.

Por padrão são até quatro chamadas de especialistas, três pesquisas web, 48 turnos e 80 ferramentas em até 15 minutos. A área permite 100 arquivos, 512 KB por entrega e 8 MB ao todo. Materializa os 12 anexos anteriores mais recentes e os atuais, até 24 MB. A análise Python recebe até três arquivos de 512 KB, respeitando 1,5 MB de payload, executa por até oito segundos e devolve até quatro arquivos somando 24 KB. A geração direta de arquivos pelo SDK tem os limites maiores da área de trabalho. Nenhum pacote é instalado implicitamente.

A instalação continua privada e com um worker, uma tarefa de modelo por vez. Perguntas e aprovações pendentes são transitórias: reiniciar o processo encerra tarefas interrompidas; não há reexecução paga automática. Tokens OAuth permanecem com a política existente da Ada. A tabela de transcript contém o contexto completo, inclusive dados das ferramentas; trate o banco como armazenamento privado.

## Atualização e validação

Antes de iniciar esta versão com PostgreSQL, execute `backend/scripts/prepare_postgres.py` para aplicar a migração aditiva 8. SQLite prepara o esquema na inicialização. O Dockerfile já inclui o backend, as skills e os runners.

Os testes simulam o SDK, o executor e o transporte, sem consumir créditos. Ainda é necessário um teste integrado com uma chave Anthropic válida para medir comportamento real, compactação automática, qualidade das skills e custo dos dois modelos. O executor Render precisa de configuração e validação próprias; nenhuma sandbox paga é criada pelos testes comuns.

Não há login Claude.ai nem reaproveitamento de tokens da assinatura Claude Code. O motor é o Agent SDK autenticado pela API, conforme a integração de produto suportada pela Anthropic.

Referências oficiais: [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview), [armazenamento de sessões](https://code.claude.com/docs/en/agent-sdk/session-storage), [skills](https://code.claude.com/docs/en/agent-sdk/skills), [streaming](https://code.claude.com/docs/en/agent-sdk/streaming-output), [custos](https://code.claude.com/docs/en/agent-sdk/cost-tracking).
