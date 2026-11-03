# Estrutura do repositório e propriedade de arquivos

Contrato T1. As tarefas T2–T7 rodam **em paralelo**, cada uma em seu worktree/branch. Para não haver conflito de merge, cada arquivo tem **um único dono**. Uma tarefa só cria ou altera o que está na sua lista; o resto é somente leitura. Se precisar de uma mudança num arquivo de outro dono (ou no contrato), **pare e relate** — não contorne.

## 1. Árvore final

```
LiveStats/
├─ .gitignore                    existente — NINGUÉM altera
├─ .gitattributes                T2
├─ .editorconfig                 T2
├─ .dockerignore                 T7
├─ .env.example                  T2
├─ .gitleaks.toml                T2
├─ .pre-commit-config.yaml       T2
├─ .github/
│  ├─ workflows/ci.yml           T2
│  └─ dependabot.yml             T2
├─ README.md                     T2
├─ CONTRIBUTING.md               T2
├─ SECURITY.md                   T2
├─ pyproject.toml                T2   (somente configuração de ruff)
├─ pytest.ini                    T2
├─ requirements-dev.txt          T2   (agrega os requirements-dev de cada pasta com `-r`)
├─ docker-compose.yml            T1 (esqueleto) → T7 (completa; ver §4)
├─ secrets/
│  └─ README.md                  T2   (os *.txt de segredo são ignorados pelo Git)
├─ db/
│  ├─ schema.sql                 T1  — somente leitura (livestats.db compartilhado)
│  └─ admin_schema.sql           T1  — somente leitura (admin.db; volume exclusivo da api)
├─ docs/                         T1  — somente leitura (PLANO, MAPA, ARCHITECTURE, openapi, legacy-formats,
│                                      data-model, repo-layout, test-plan)
├─ api/                          T3
│  ├─ Dockerfile                 T7
│  ├─ requirements.in            T3
│  ├─ requirements.txt           T3   (versões fixas + --hash)
│  ├─ requirements-dev.txt       T3
│  ├─ pyproject.toml             T3   (metadados do pacote; sem config de ruff/pytest)
│  ├─ app/                       T3   (pacote `app`; entrada `app.main:app`; `app/healthcheck.py`)
│  └─ tools/                     T3   (`hash_password.py`, `gen_cookie_keys.py`)
├─ worker_lives/                 T4   (pacote `worker_lives`; `__main__.py`, `healthcheck.py`)
│  ├─ Dockerfile                 T7
│  ├─ requirements.in / .txt / requirements-dev.txt   T4
│  └─ *.py                       T4
├─ worker_inscritos/             T5   (pacote `worker_inscritos`; `__main__.py`, `healthcheck.py`)
│  ├─ Dockerfile                 T7
│  ├─ requirements.in / .txt / requirements-dev.txt   T5
│  └─ *.py                       T5
├─ web/
│  ├─ Dockerfile                 T7
│  ├─ nginx/                     T7   (nginx.conf, templates/*.template, snippets/)
│  └─ static/                    T6
│     ├─ overlay/                      contador.html, contador.css, contador.js
│     └─ admin/                        index.html, css/, js/ (módulos ES com // @ts-check), fonts/
├─ tests/
│  ├─ contract/legacy/           T1  — somente leitura (golden files)
│  ├─ api/                       T3
│  ├─ worker_lives/              T4   (inclui record_fixtures.py e fixtures/ com respostas REAIS gravadas do YouTube)
│  ├─ worker_inscritos/          T5   (inclui fixtures/ com a página derivada da estrutura que o scraper original lê)
│  ├─ web/                       T6   (Playwright; inclui o servidor-dublê validado contra o openapi)
│  ├─ infra/                     T7
│  ├─ e2e/                       T8
│  └─ security/                  T8
├─ scripts/                      T8   (comparação com os apps atuais, varredura anti-burla)
└─ deploy/                       T9   (criado só no deploy; `docs/DEPLOY.md` também é T9)
```

`codigo-original/` e `servidor-opt.tar.gz` existem só localmente (ignorados pelo Git; contêm a chave antiga). Nenhuma tarefa os lê para copiar valores secretos nem os referencia em código, Dockerfile ou teste.

Cuidado com nomes: o `.gitignore` ignora qualquer pasta chamada `data/` e arquivos `*.db`, `*.key`, `*.pem`, `cookies.json`, `debug.png`. Não crie pastas ou arquivos versionáveis com esses nomes.

## 2. O que cada tarefa pode criar ou alterar

### T2 — Higiene do repositório (Haiku)
Cria/altera exatamente:
`.gitattributes`, `.editorconfig`, `.env.example`, `.gitleaks.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `pyproject.toml`, `pytest.ini`, `requirements-dev.txt`, `secrets/README.md`.

Requisitos de conteúdo vindos do contrato:
- `.env.example` com **somente placeholders** e variáveis não secretas: `ADMIN_USERNAME`, `PUBLIC_BASE_URL`, `YOUTUBE_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxxxxxx`, `LIVE_INTERVAL`, `SCRAPE_INTERVAL`, `DEBUG_SCREENSHOT`, `ADMIN_BIND=127.0.0.1` (padrão seguro; comentário: o acesso normal ao admin é pelo Nginx Proxy Manager com HTTPS entrando na rede `edge` com IP fixo; alternativa: Tailscale Serve → `127.0.0.1:8081`), `TRUSTED_PROXY_CIDR=` (**vazio** por padrão; se houver cloudflared na rede `edge`, apenas o IP fixo dele em `/32`, nunca a sub-rede nem o gateway), `ADMIN_TRUSTED_PROXY_IP=` (**vazio** por padrão; IP fixo do NPM em `/32`; mesma regra), `EDGE_SUBNET`.
- `secrets/README.md`: os 4 arquivos esperados (`youtube_api_key.txt`, `cookie_private_key.txt`, `cookie_public_key.txt`, `admin_password_hash.txt`), como gerá-los (`docker compose run --rm --no-deps -T api python -m tools.gen_cookie_keys` / `python -m tools.hash_password`), permissões, e que o conteúdo nunca vai para o Git.
- `pytest.ini`: `pythonpath = . api`, marcadores `docker`, `e2e`, `slow`, `xfail_strict = true`, **sem** `--ignore`, `-k`, `-m "not ..."` ou `--deselect` em `addopts`. Ninguém além da T2 altera este arquivo (impede enfraquecer a suíte).
- CI: ruff, pytest (por pasta: `tests/api`, `tests/worker_lives`, `tests/worker_inscritos`, `tests/web`, `tests/infra`), gitleaks e Trivy (repositório e imagens), sem `continue-on-error` e sem `|| true`; Trivy sem arquivo de ignorados.
- Não cria nem edita código de produção nem testes das outras tarefas.

### T9 — Deploy (fora de T2–T7; só após V2 e com OK do usuário)
Configura o Nginx Proxy Manager (HTTPS por desafio DNS no Cloudflare, lista de acesso `192.168.15.0/24` + `100.64.0.0/10`, ligação à rede `livestats-edge` com IP fixo), o Pi-hole (nome → IP da LAN), o DNS do Tailscale e o cloudflared; confirma as premissas de `ARCHITECTURE.md` P1/P3 e preenche `TRUSTED_PROXY_CIDR` e `ADMIN_TRUSTED_PROXY_IP`. Arquivos: `deploy/**` e `docs/DEPLOY.md`. Nada disso é escrito por T2–T7.

### T3 — api (Sonnet)
Cria/altera: tudo em `api/` **exceto** `api/Dockerfile`; tudo em `tests/api/`.
Não toca em: `db/schema.sql` e `db/admin_schema.sql` (usa-os: aplica o primeiro em `DB_PATH` e o segundo em `ADMIN_DB_PATH`; o caminho dos arquivos dentro da imagem é configurável, o COPY no Dockerfile é da T7), `docs/openapi.yaml`, `tests/contract/**`.
Entregas obrigatórias: `app.main:app`, `app.healthcheck`, `tools.gen_cookie_keys`, `tools.hash_password`; dois bancos (`livestats.db` e `admin.db`, sessões e tentativas de login **só** no segundo); autorizador SQLite como defesa contra bug (ver `data-model.md` §2.1); serialização legada byte a byte; handler de 422 sem `input`; rota pega-tudo `/api/admin/{path}` (401 sem sessão, 404 com sessão; `ARCHITECTURE.md` D30).

### T4 — worker-lives (Sonnet)
Cria/altera: tudo em `worker_lives/` **exceto** `worker_lives/Dockerfile`; tudo em `tests/worker_lives/`.
Entregas obrigatórias: `python -m worker_lives`, `python -m worker_lives.healthcheck`; chave lida de `YOUTUBE_API_KEY_FILE`; o worker não acessa `admin.db` (não monta o volume).

**Fixtures reais (regra anti-burla 4; correção C5 do V0).** As fixtures do `videos.list` são **gravadas da API real**, nunca escritas à mão:
- script `tests/worker_lives/record_fixtures.py`, executado pelo desenvolvedor/T4 com a **chave real nova** lida de `secrets/youtube_api_key.txt` (fora do Git; argumento opcional de caminho), chamando `GET videos?part=statistics,snippet,liveStreamingDetails` com a chave no header `X-Goog-Api-Key`;
- IDs a gravar passados por argumento/arquivo de configuração (um vídeo ao vivo agora, um agendado, um encerrado, um sem `likeCount`, um inexistente/removido) e, para erros, requisições reais que o YouTube recusa de verdade (ex.: chave inválida → 400 `API key not valid`);
- antes de salvar: grava só o corpo da resposta e metadados (`recorded_at`, status HTTP, `Content-Type`), descartando **todos** os demais cabeçalhos e qualquer campo sensível; **falha** (exit ≠ 0, nada salvo) se o valor da chave aparecer em qualquer parte do arquivo a gravar;
- respostas que não se consegue provocar na API real (`quotaExceeded` 403, 429, 5xx, timeout) usam o corpo de erro no formato documentado pelo Google e ficam em arquivos marcados `"synthetic": true` e listados no relatório da T4; o resto das fixtures é gravado de verdade;
- **sem a chave disponível (ou sem um vídeo ao vivo/agendado para gravar), a T4 PARA e relata**: não inventa fixtures nem usa a chave antiga.

### T5 — worker-inscritos (Sonnet)
Cria/altera: tudo em `worker_inscritos/` **exceto** `worker_inscritos/Dockerfile`; tudo em `tests/worker_inscritos/`.
Entregas obrigatórias: `python -m worker_inscritos`, `python -m worker_inscritos.healthcheck`; página de fixture local reproduzindo `yta-smooth-counter` (três variantes, uma para cada estratégia de leitura), **derivada da estrutura que o scraper original lê** (`yta-smooth-counter` com `.counter-value.last`, `.counter-sequence-inner` e `div`s empilhados, conforme `docs/legacy-formats.md` §2.2); o Studio real só é validado no V2; o worker não acessa `admin.db`; sem porta de debug; screenshot só com `DEBUG_SCREENSHOT=1`, em `/tmp/debug`.

### T6 — Front (Sonnet)
Cria/altera: tudo em `web/static/`; tudo em `tests/web/`.
Contrato de URLs (para o T7 mapear no Nginx): overlay em `web/static/overlay/` servido em `/overlay/` (arquivos `contador.html`, `contador.css`, `contador.js`; `/contador` entrega `contador.html`; `/overlay/inscritos` não existe, D36); admin em `web/static/admin/` — `index.html` servido em `/` e `/admin` (8081), e o restante em `/admin-assets/` (8081 apenas). O HTML referencia assets com caminho absoluto (`/overlay/contador.js`, `/admin-assets/js/main.js`).
Regras: sem `<script>` inline, sem atributos `on*=`, sem `style=`, sem `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write`/`eval`, sem requisições a terceiros.

### T7 — Infra (Sonnet)
Cria/altera exatamente: `docker-compose.yml`, `.dockerignore`, `api/Dockerfile`, `worker_lives/Dockerfile`, `worker_inscritos/Dockerfile`, `web/Dockerfile`, tudo em `web/nginx/`, tudo em `tests/infra/`.
Pode **acrescentar** (metadados `x-casaos`, comentários, `labels`), mas não alterar o que o contrato fixa (§4).
Os Dockerfiles: multi-stage, imagem base por digest, usuário numérico não-root, `HEALTHCHECK` coerente com o compose, `COPY` só do necessário (a api também copia `db/schema.sql` e `db/admin_schema.sql`), `/data` criado como `root:10001` modo `2770` nas três imagens Python e `/admin-state` como `10001` modo `0700` somente na imagem da api, nenhum `ARG`/`ENV` com segredo.

## 3. Arquivos compartilhados — somente leitura para T2–T7

| Arquivo | Dono |
|---|---|
| `docs/PLANO.md`, `docs/MAPA-DE-SEGURANCA.md`, `docs/ARCHITECTURE.md`, `docs/openapi.yaml`, `docs/legacy-formats.md`, `docs/data-model.md`, `docs/repo-layout.md`, `docs/test-plan.md` | T1 / coordenação |
| `db/schema.sql`, `db/admin_schema.sql` | T1 / coordenação |
| `tests/contract/legacy/*` (golden files) | T1 — alterar é violação da regra anti-burla 1 |
| `.gitignore` | usuário/coordenação |
| `pytest.ini`, `pyproject.toml` (raiz) | T2 |

Mudança de contrato = pedido ao coordenador, nova revisão do V0, nunca edição "por conveniência".

## 4. `docker-compose.yml` — o que T7 não pode mudar

Nomes dos 4 serviços; nomes das redes e do volume; `internal: true` na rede `internal`; membros de cada rede (§3 do `ARCHITECTURE.md`); mapeamento secret → container; `user` não-root numérico; `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges:true` em todos; portas publicadas = apenas `8080` e `8081` no `web` (`ADMIN_BIND` com padrão `127.0.0.1`); volume `livestats-admin` montado somente na `api`; `TRUSTED_PROXY_CIDR` e `ADMIN_TRUSTED_PROXY_IP` com padrão vazio; CORS (`Access-Control-Allow-Origin: *`, `Allow-Methods: GET`, preflight 204) emitido pelo Nginx **somente** nos GET públicos de dados (ver `ARCHITECTURE.md` §7); nenhum valor secreto e nenhuma variável de segredo em `environment`; `shm_size` ≥ 256mb no `worker-inscritos`. `tests/infra` verifica cada item.

## 5. Comandos canônicos (executados da raiz do repositório)

| Suíte | Comando | Requisito |
|---|---|---|
| T3 | `python -m pytest tests/api` | Python 3.12 |
| T4 | `python -m pytest tests/worker_lives` | Python 3.12 |
| T5 | `python -m pytest tests/worker_inscritos` | Python 3.12 + `playwright install chromium` |
| T6 | `python -m pytest tests/web` | Python 3.12 + Playwright |
| T7 | `python -m pytest tests/infra -m "not docker"` (estáticos, sem Docker) e `python -m pytest tests/infra -m docker` (com Docker) | os estáticos precisam rodar sem Docker |
| T8 | `python -m pytest tests/e2e tests/security` | Docker + compose |
| Estático | `ruff check .`, `gitleaks detect`, `trivy fs .` | — |

O marcador `docker`/`e2e` só indica requisito de ambiente; um teste marcado jamais é pulado silenciosamente — se o ambiente não existe, a suíte **falha** com mensagem clara (regra anti-burla 2).

## 6. Fronteira de módulos (quem pode importar quem)

Nenhuma pasta importa código de outra (`api`, `worker_lives`, `worker_inscritos`, `web` são independentes). O que é comum (padrão de extração de ID, `formatar`, nomes de tabela/coluna) está **especificado** no contrato e implementado de forma independente em cada lado, com os mesmos vetores de teste (`legacy-formats.md`). Isso evita que uma tarefa dependa do código da outra e mantém o merge limpo.
