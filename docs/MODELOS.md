# Dois modelos Anthropic na Ada

Uma única chave Anthropic atende o modelo principal e o modelo leve, desde que a conta tenha acesso a ambos. O saldo e o orçamento da tarefa são compartilhados; não é necessário comprar uma chave por modelo.

```dotenv
ADA_MODEL_PROVIDER=claude_agent_sdk
MODEL_API_KEY=CONFIGURE_PRIVADAMENTE
MODEL_NAME=sonnet
ADA_LIGHT_MODEL=haiku
```

Os aliases `sonnet` e `haiku` são resolvidos pelo Claude Agent SDK. Também é possível usar identificadores completos de modelos disponíveis na conta para fixar a versão. Não configure o alias Haiku no gateway de outro provedor: esta seleção de dois modelos vale para a execução nativa com Claude.

O principal continua responsável pela criação do perfil, coordenação e resposta final. No runtime nativo, ele escolhe especialistas main ou light durante a execução, sem uma chamada obrigatória de planejamento. Tarefas delimitadas podem ir para o leve; análises complexas ou incertas podem continuar no principal. O modo `planned` preserva o planejamento anterior com `model_tier`.

Se `ADA_LIGHT_MODEL` estiver vazia ou ausente, todos os especialistas continuam no modelo principal. Perfis existentes não precisam de migração. O SDK recebe o modelo escolhido em `AgentDefinition.model`; o hook de delegação impede substituições fora da definição autorizada. Os eventos de planejamento e início do especialista registram o modelo selecionado.

O teto da chamada SDK e o orçamento da tarefa permanecem compartilhados. A estimativa de custo é obtida do SDK, incluindo a delegação; Haiku não recebe um orçamento separado. Memória, resumos e outras chamadas auxiliares continuam no modelo principal nesta implementação.

No Render, adicione `ADA_LIGHT_MODEL` em Environment e configure seu valor quando quiser ativá-lo. Reinicie/republique o serviço depois de mudar o modelo. Localmente, altere o `.env` privado e reinicie o backend. O `.env` privado não é enviado ao GitHub.

Para os primeiros testes, confirme os identificadores disponíveis e o saldo na conta Anthropic. Os testes de código usam clientes simulados; a qualidade, o custo e a execução real dos dois modelos ainda precisam ser medidos com a chave da conta.
