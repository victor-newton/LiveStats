# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões seguem [SemVer](https://semver.org/lang/pt-BR/).

## [1.0.0] - 2026-11-03

Primeira versão em produção. Substitui os dois apps antigos do ZimaOS (Live Stats e YT Inscritos) por uma aplicação única, com as mesmas funções e o formato JSON antigo mantido byte a byte.

### Funcionalidades

- **Lives**: até 4 slots lidos pela YouTube Data API v3 (status, visualizações, curtidas, espectadores). Endpoints legados `/live1.json` … `/live4.json` (byte a byte iguais aos antigos) e novos `/api/public/lives/{n}`.
- **Overlay de likes** (`/overlay/live.html?slot=N`): número de likes/meta e barra de progresso arredondada colada embaixo do número, com meta, cor da barra, fonte e cor do texto por live (D34, C15).
- **Contador de inscritos**: lido do YouTube Studio com Playwright/Chromium usando cookies colados no painel. Endpoints `/inscritos.json` (legado) e `/api/public/subscribers`. Overlay único em `/contador`, que acompanha também as quedas do número (D36).
- **Renovação automática da sessão do Studio**: depois de cada leitura válida o worker exporta os cookies atualizados (no máximo a cada 10 minutos), cifra e grava um conjunto novo; colar cookies à mão só é necessário se o Google derrubar a sessão (D37).
- **Painel admin** em abas (Inscritos, Lives, Saúde), visual escuro minimalista e otimizado para celular. Cada aba lista os seus endpoints públicos com botão Copiar, sempre no endereço público (`PUBLIC_BASE_URL`). Canal, fontes do Windows e cores configuráveis pelo painel (D35, D36).
- **Painel de saúde** com diagrama e estado de cada serviço, e `/health` público.

### Segurança

- Quatro containers com papéis separados (`web`, `api`, `worker-lives`, `worker-inscritos`), usuário não-root, `read_only`, `cap_drop: ALL`, `no-new-privileges`; a `api` não tem saída para a internet (rede `internal`).
- Segredos só em Docker secrets (nunca em `.env`, compose ou Git): chave da YouTube API, par de chaves dos cookies (a privada só no `worker-inscritos`), hash argon2id da senha do admin e o certificado TLS do admin.
- Cookies do Studio cifrados com SealedBox (PyNaCl) antes de chegar ao banco.
- Admin só na porta 8081, em HTTPS próprio, com lista de IPs (LAN e Tailscale); nenhuma rota de admin na porta pública 8080. Sessão `__Host-` HttpOnly/Secure/SameSite=Strict, CSRF, limite de tentativas de login.
- CSP estrita sem script/estilo inline; CORS `*` só nos GET públicos de dados.
- IP real do visitante pelo Nginx Proxy Manager (opção A, IP fixo confiável `/32`), para o limite de requisições contar por visitante.
- gitleaks e Trivy no pre-commit e no CI; histórico do Git reescrito antes da publicação para remover um falso positivo.

### Deploy

- Scripts em `deploy/` rodados do PC (`enviar.sh`): preflight, instalação com 1 senha SSH e segredos pela entrada padrão, conexão do NPM, status, rollback sem apagar volumes.
- Registro na tela inicial do ZimaOS (`enviar.sh registrar-zimaos`, C17): compose resolvido em `/var/lib/casaos/apps/livestats/` (root, 600), com checagem de que nenhum segredo vai para o arquivo.
- Compatibilidade com o ZimaOS: `DOCKER_CONFIG` próprio quando `~/.docker` é ilegível (a home do usuário é `/DATA`).

### Testes

- Suítes de api, workers, web (Playwright), infra, e2e e segurança, validadas por testes de mutação a cada tarefa. Na v1.0.0: api 403, worker_lives 154, worker_inscritos 212, web 225, infra 211+ e e2e/segurança 60+ testes. O único teste que falha no PC é o SEC-11 (isolamento de rede), por limitação conhecida do Docker Desktop.
