# Plano — aplicação unificada (Live Stats + YT Inscritos)

Uma única aplicação (um repositório, um `docker-compose.yml`, um ícone no ZimaOS) com **as mesmas funcionalidades de hoje**, mas com segredos isolados, admin autenticado e só o mínimo exposto. Base: `MAPA-DE-SEGURANCA.md` e o código em `codigo-original/`.

## 1. Arquitetura

```
 Internet ── Cloudflare Tunnel ──┐            LAN / Tailscale ──┐
                                 ▼                              ▼
                         ┌── :8080 pública ──────────┬── :8081 admin ───────┐
                         │ overlays, /api/public,    │ painel admin,        │
                         │ URLs legadas. Rota de     │ /api/admin, login.   │
                         │ admin NÃO existe aqui.    │ Só LAN/Tailscale.    │
                         ├───────────── web (Nginx) ────────────────────────┤
                         │ cabeçalhos de segurança, CSP, limit_req          │
                         └──────────────┬───────────────────────────────────┘
                              rede interna (sem internet)
                         ┌──────────────▼──────────────┐
                         │ api (FastAPI)               │  NÃO conhece chave nem cookies
                         │ /api/public/*  só leitura   │
                         │ /api/admin/*   login+CSRF   │
                         └──────────────┬──────────────┘
                                 volume /data (SQLite)
                 ┌──────────────────────┴──────────────────────┐
     ┌───────────▼───────────┐                     ┌───────────▼────────────┐
     │ worker-lives          │                     │ worker-inscritos       │
     │ secret: youtube_key   │                     │ secret: chave privada  │
     │ YouTube Data API v3   │                     │ Playwright + Chromium  │
     └───────────────────────┘                     └────────────────────────┘
          rede de saída (só internet, sem acesso à api/web)
```

Princípios:
- **Cada segredo existe em um só container.** A chave da API só no `worker-lives`; os cookies só são legíveis pelo `worker-inscritos`.
- **Segredos via Docker secrets (arquivos), não variáveis de ambiente.** Variáveis aparecem no YAML do painel do ZimaOS (foi assim que a chave ficou visível); secrets montados em `/run/secrets` não aparecem.
- **Cookies cifrados com caixa selada (libsodium/PyNaCl).** A api só tem a chave pública: ela cifra o que o admin colou e grava no banco, mas não consegue ler de volta. Só o worker tem a chave privada.
- **Front 100% estático**, sem script inline, sem `innerHTML` com dados externos (só `textContent`). CSP estrita bloqueia o resto.
- **Compatibilidade com OBS/vMix:** `/live1.json`…`/live4.json`, `/inscritos.json` e `/contador` continuam respondendo no mesmo formato (aliases para `/api/public/*` e `/overlay/*`).

Endurecimento de todos os containers: usuário não-root, `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, imagens base fixadas por digest, dependências com versão e hash fixos, sem porta de debug do Chrome, CORS fechado (mesma origem), exceto nos GET públicos de dados, que respondem `Access-Control-Allow-Origin: *` sem credenciais (decisão do usuário no V0, D10 do ARCHITECTURE).

Admin: login com senha (hash argon2 em secret), sessão em cookie `HttpOnly; Secure; SameSite=Strict`, token CSRF, logout, expiração. Limite de tentativas em duas camadas: `limit_req` no Nginx (por IP) e contador na própria api (por IP e por usuário), para que o limite continue valendo mesmo se o Nginx for mal configurado. Além disso, o Nginx tem duas portas: a pública (8080, para onde o Cloudflare Tunnel aponta) simplesmente não tem rota de admin; a de admin (8081) tem HTTPS próprio (certificado autoassinado gerado no servidor) e só aceita a LAN e o Tailscale, por lista de IPs no próprio Nginx (`ADMIN_ALLOW_CIDRS`; decisão D33, sem Nginx Proxy Manager no caminho do admin). Na porta pública o IP real do visitante vem do cabeçalho `CF-Connecting-IP`, aceito só quando a conexão vem do IP fixo /32 do proxy confiável (`set_real_ip_from`; no host real, o Nginx Proxy Manager que recebe o Cloudflare Tunnel, ou o cloudflared se estiver neste host), para o `limit_req` contar por visitante e não tratar todo mundo como um IP só.

### Stack

| Componente | Tecnologia |
|---|---|
| api | Python 3.12, FastAPI, Pydantic, argon2-cffi, PyNaCl |
| worker-lives | Python 3.12, httpx |
| worker-inscritos | Python 3.12, Playwright (Chromium com versão fixa pela biblioteca) — substitui o Selenium + Chrome `current` |
| web | Nginx (imagem `nginx-unprivileged`), HTML + CSS + JavaScript puro em ES modules com `// @ts-check`, sem framework e sem build |
| dados | SQLite (modo WAL) no volume `/data` |
| testes | pytest, respx (só fronteira HTTP do YouTube), Playwright para E2E |
| CI | GitHub Actions: ruff, pytest, gitleaks, Trivy |

## 2. Tarefas e modelos

Modelo por custo: **Haiku** = tarefas mecânicas; **Sonnet** = implementação; **Opus** = só validação (portões V0–V3), sem escrever código de produção.

| ID | Tarefa | Modelo | Depende de | Entrega |
|---|---|---|---|---|
| P0 | Pré-requisitos (você): trocar a chave da API, criar repositório no GitHub, conferir o que o Tunnel/NPM publica hoje | — | — | chave nova guardada fora do Git |
| T1 | Contrato: `ARCHITECTURE.md`, OpenAPI dos endpoints, esquema SQLite, formato dos JSON legados, esqueleto do compose | Sonnet | — | contrato que todas as outras tarefas seguem |
| **V0** | **Validar contrato** (cobre tudo de hoje? segredo vaza em algum ponto?) | **Opus** | T1 | aprovado / lista de correções |
| T2 | Higiene do repositório: `.gitignore`, `.env.example`, `secrets/README.md`, pre-commit com gitleaks, CI no GitHub Actions (lint, testes, gitleaks, Trivy), README | Haiku | V0 | repositório pronto para receber código |
| T3 | api: armazenamento SQLite, endpoints públicos, admin (login argon2, sessão, CSRF, rate limit), cifragem dos cookies com chave pública, aliases legados + testes | Sonnet | V0 | `api/` + `tests/api/` |
| T4 | worker-lives: porte do `live_worker.py`, chave lida de `/run/secrets`, backoff em erro/cota, grava no SQLite + testes (HTTP do YouTube simulado com respostas gravadas) | Sonnet | V0 | `worker_lives/` + testes |
| T5 | worker-inscritos: porte do `scraper.py` de Selenium para Playwright, decifra cookies, sem porta 9222, print de debug só com flag e nunca em pasta servida + testes (página local que reproduz o `yta-smooth-counter`) | Sonnet | V0 | `worker_inscritos/` + testes |
| T6 | Front: painel admin (login, 4 slots, colar cookies, status) e overlays (contador animado idêntico ao atual) sem script inline nem `innerHTML` + testes Playwright | Sonnet | V0 | `web/` + testes |
| T7 | Infra: Dockerfiles multi-stage não-root, compose com secrets/redes/read_only, `nginx.conf` com duas portas (pública 8080 sem admin, admin 8081), CSP, `limit_req` e IP real via `set_real_ip_from`, metadados `x-casaos` | Sonnet | V0 | sobe com `docker compose up` |
| **V1** | **Validar cada tarefa T2–T7** ao terminar (revisão do diff + rodar os testes + checklist anti-burla) | **Opus** | cada T | aprovado / devolvido ao mesmo subagente |
| T8 | Integração: stack completa, suíte E2E e suíte de segurança (seção 4) | Sonnet | V1 de T3–T7 | relatório de testes |
| **V2** | **Validação final**: rodar tudo de novo, teste real com a chave nova, comparar saídas com os apps atuais (inclusive o número de inscritos lido pelo Playwright × Selenium lado a lado), scan de segredos no histórico | **Opus** | T8 | liberado para deploy |
| T9 | Deploy no ZimaOS (só com seu OK): build no servidor, migrar slots atuais, trocar rota do Tunnel, manter os apps antigos parados para rollback | Sonnet | V2 | app novo no ar |
| **V3** | **Conferência pós-deploy** e push para o GitHub (com seu OK) | **Opus** | T9 | repositório publicado |

T2–T7 rodam em paralelo depois do V0, cada uma em seu próprio worktree/branch, sem tocar nos arquivos das outras.

## 3. Regras anti-burla (vão no briefing de todo subagente)

Proibido, e o validador reprova a tarefa se encontrar:
1. Alterar, apagar ou enfraquecer um teste para ele passar (trocar `==` por `in`, afrouxar asserção, reduzir casos).
2. `skip`, `xfail`, `pytest.mark.skip`, `test.skip`, `# pragma: no cover` em código novo, ou teste que não verifica nada.
3. Caminho especial para teste no código de produção: `if TESTING`, `DISABLE_AUTH`, variável que desliga login/CSRF/rate limit, usuário/senha fixos.
4. Simular (mock) a própria unidade testada. Só pode simular a **fronteira externa** (HTTP do YouTube, relógio), e com respostas reais gravadas.
5. Respostas fixas no código para casar com o teste (hardcode do valor esperado).
6. Capturar exceção e engolir para o teste ficar verde.
7. Declarar "passou" sem colar a saída real do comando de teste.

Se a funcionalidade não puder ser implementada direito, o subagente deve **parar e relatar**, não contornar.

**Exceção aprovada no V0 (regra 4):** respostas de erro do YouTube que não se consegue provocar na API real (`quotaExceeded` 403, 429, 5xx, timeout) podem usar fixtures marcadas `"synthetic": true`, com o corpo no formato documentado pelo Google, listadas no relatório da T4. Todo caminho de sucesso e o erro 400 de chave inválida usam respostas reais gravadas.

Checagem do validador (Opus) em cada V: ler o diff inteiro, `git diff` dos testes contra a versão aprovada no contrato, busca por padrões proibidos acima, rodar os testes ele mesmo do zero (container limpo) e conferir que cada teste falha quando a funcionalidade é removida (teste de mutação manual em amostra).

## 4. Testes de segurança obrigatórios (T8, revalidados no V2)

- Toda rota `/api/admin/*` sem sessão → 401; com sessão e sem CSRF → 403.
- 6ª tentativa de login errada em 1 min → 429.
- Título de vídeo `<img src=x onerror=alert(1)>` aparece como texto no painel (Playwright verifica que nenhum alert dispara).
- Na porta pública (8080), `/admin`, `/api/admin/*` e `/api/admin/login` → 404 (a rota não existe lá).
- `limit_req` na porta pública conta por IP real (dois `CF-Connecting-IP` diferentes não compartilham limite; o cabeçalho enviado por quem não é o cloudflared é ignorado).
- Cabeçalhos presentes: CSP sem `unsafe-inline`, `X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors`.
- `docker inspect`: nenhum container como root, rootfs somente leitura, sem capabilities.
- A chave da API não aparece em `docker history`, em `docker inspect` (env), nos logs nem no container da api.
- A api não consegue decifrar os cookies (teste tenta e precisa falhar).
- gitleaks e Trivy sem achados no repositório e nas imagens.
- Formatos legados idênticos: `/live1.json` e `/inscritos.json` comparados campo a campo com a saída dos apps atuais.
