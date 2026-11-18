# Deploy privado inicial

Os arquivos de deploy estão preparados; este projeto ainda não foi publicado nem validado em um serviço hospedado. As demos podem continuar no computador local.

1. Suba o código atualizado ao seu repositório, sem `.env`, banco local, certificados ou chaves.
2. No Render, crie um serviço a partir de `render.yaml` ou um Web Service Docker apontado à raiz da Ada. O plano e a disponibilidade devem ser conferidos no painel antes de publicar.
3. Configure `ADA_PUBLIC_ORIGIN` com a URL HTTPS exata do serviço, sem caminho, e `ADA_OAUTH_CALLBACK_BASE` com a mesma URL.
4. Defina `ADA_ACCESS_USER` e uma senha de pelo menos 8 caracteres em `ADA_ACCESS_PASSWORD`. A navegação pedirá essa credencial; a API também exige autenticação. Não a coloque no Git.
5. Configure `ADA_DATABASE_BACKEND=postgres`, `SUPABASE_DATABASE_URL` e faça upload do certificado CA como arquivo secreto `/etc/secrets/prod-ca-2021.crt`. A aplicação requer esquema versão 7; prepare o banco pelo script `backend/scripts/prepare_postgres.py`.
6. Configure `ADA_MODEL_PROVIDER=claude_agent_sdk`, `MODEL_NAME` com o identificador disponível na sua conta, `MODEL_API_KEY`, orçamento e limites. A chave deve existir somente como segredo do servidor.
7. Copie os IDs e segredos OAuth para as variáveis do serviço. Cadastre nos provedores os callbacks HTTPS exibidos pela Ada. Google usa `/connections/drive/callback`, GitHub `/connections/github/callback`, Figma `/connections/figma/callback`.
8. Publique e valide login, `/health`, criação, conversa, anexos, conexões e uma execução de workflow. Use somente um worker.

Limitações da primeira hospedagem: OAuth ainda mantém tokens em memória e pode exigir reconexão após reinício. O serviço hospedado não terá acesso ao seu Multipass local nem ao Docker Desktop. O fluxo de código precisa de executor remoto ou outra VM configurada; não exponha o daemon Docker pela internet. Serviços que suspendem por inatividade não garantem horários de recorrência. A autenticação Basic não oferece gestão de contas, recuperação de senha ou logout de sessão pelo produto; use uma conta por instalação privada e revogue acesso trocando a senha no serviço.

O build Docker ainda precisa ser executado no ambiente de deploy. Os testes de aplicação e da fronteira de autenticação não substituem a validação na hospedagem real.

## Verificação antes de publicar

O modelo sem segredos está em `deploy/render.env.example`. Ele serve de referência para as variáveis do painel; não substitua o `.env` local por ele.

Na raiz do projeto, valide os arquivos e a conexão local, sem alterar registros ou consumir a API do modelo:

```powershell
..\.venv\Scripts\python.exe backend\scripts\check_deploy.py --database
```

Para conferir uma configuração de hospedagem salva em um arquivo privado fora do Git:

```powershell
..\.venv\Scripts\python.exe backend\scripts\check_deploy.py --cloud --env-file CAMINHO_DO_ARQUIVO_PRIVADO
docker build -t ada-platform:local .
```

O certificado precisa existir no ambiente em que a verificação é executada. O caminho `/etc/secrets/...` passa a existir no serviço depois do upload do arquivo secreto. A verificação não confirma saldo da API, consentimento OAuth, executor remoto nem publicação.

## Dependências do frontend

A auditoria atual sinaliza a cadeia de desenvolvimento Vite → glob → picomatch (GHSA-fc5q-39xc-vjpf). Não há versão corrigida do picomatch indicada no aviso consultado. O serviço de produção entrega os arquivos compilados pelo FastAPI; não exponha o servidor de desenvolvimento Vite. A atualização forçada sugerida pelo npm não deve ser aplicada sem conferir compatibilidade e os avisos da versão de destino. Reavalie `npm audit` antes da publicação.
