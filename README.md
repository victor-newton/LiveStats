# LiveStats

Aplicação única que reúne duas funções que antes eram dois apps separados:

- **Estatísticas de lives**: acompanha até quatro lives do YouTube (status, visualizações, curtidas e espectadores simultâneos), lidas pela YouTube Data API v3 e servidas em JSON para OBS e vMix.
- **Contador de inscritos**: lê o número de inscritos do canal com um navegador automatizado (Playwright), usando cookies de sessão colados pelo administrador, e entrega o valor em JSON e num overlay animado.

Tudo roda em Docker Compose, com segredos isolados e só a parte pública exposta.

> **Status:** em desenvolvimento. Os documentos em `docs/` são o contrato que a implementação segue. Leia `docs/PLANO.md` e `docs/ARCHITECTURE.md` antes de mexer no código.

## Arquitetura (resumo)

Quatro containers, cada um com um papel e sem acesso ao que não precisa:

| Serviço | O que faz | Segredo que tem acesso |
|---|---|---|
| `web` (Nginx) | Duas portas: **8080** pública (overlays e JSON) e **8081** admin (painel). Cabeçalhos de segurança, CSP e limite de requisições. | nenhum |
| `api` (FastAPI) | Lê e grava o SQLite. Único com login, sessão e CSRF do admin. Cifra os cookies com a chave pública. Sem acesso à internet. | chave pública dos cookies e hash da senha do admin |
| `worker-lives` | Consulta a YouTube Data API v3 e grava as estatísticas das lives. | chave da YouTube Data API |
| `worker-inscritos` | Lê o contador de inscritos com Playwright e Chromium. Único que decifra os cookies. | chave privada dos cookies |

Detalhes e decisões: `docs/ARCHITECTURE.md`. Plano e tarefas: `docs/PLANO.md`. Mapa de segurança: `docs/MAPA-DE-SEGURANCA.md`.

## Pré-requisitos

- Docker e Docker Compose (v2).
- Uma chave da YouTube Data API v3, criada no Google Cloud e restrita à API.
- O ID do canal do YouTube (formato `UC` seguido de 22 caracteres).
- Python 3.12, apenas para desenvolvimento e testes.

## Configuração

1. Crie o arquivo de variáveis a partir do modelo e preencha os valores:

   ```sh
   cp .env.example .env
   ```

   Preencha pelo menos `ADMIN_USERNAME` e `YOUTUBE_CHANNEL_ID`. Os demais têm padrões seguros.

2. Crie os quatro arquivos de segredo em `secrets/`. Instruções completas em [`secrets/README.md`](secrets/README.md).

Segredos nunca vão para o `.env`, para o `docker-compose.yml` nem para o Git.

## Subindo a aplicação

```sh
docker compose up -d --build
docker compose ps
```

Espere os serviços ficarem `healthy`. A ordem de subida é `api`, depois `web` e os workers.

Para parar sem apagar os dados:

```sh
docker compose down
```

Os dados ficam no volume `livestats-data` (SQLite). O estado do admin fica no volume `livestats-admin`, que só a `api` enxerga.

## Endpoints públicos (OBS e vMix)

Porta **8080**. Substitua `localhost` pelo endereço do servidor ou pelo domínio público.

| Endereço | Para quê |
|---|---|
| `http://localhost:8080/live1.json` … `/live4.json` | Estatísticas de cada slot de live (formato legado, um item por arquivo) |
| `http://localhost:8080/inscritos.json` | Número de inscritos do canal |
| `http://localhost:8080/contador` | Overlay animado do contador de inscritos (fonte de navegador no OBS) |
| `http://localhost:8080/overlay/inscritos` | Mesmo overlay, outro caminho |
| `http://localhost:8080/api/public/lives/{n}` e `/api/public/subscribers` | Mesmos dados no formato novo da API |
| `http://localhost:8080/health` | Saúde do serviço |

Os JSON públicos respondem com `Access-Control-Allow-Origin: *` e aceitam apenas `GET`. Nenhuma rota de admin existe na porta 8080: `/admin` e `/api/admin/*` respondem 404 ali.

## Painel administrativo (porta 8081)

- A porta 8081 **não** é publicada na internet. Ela serve o painel e a API administrativa, em **HTTPS próprio** (o Nginx do `web` termina o TLS com um certificado autoassinado, que traz o IP da LAN no SAN). Sem o certificado e a chave em `secrets/`, o `web` não sobe.
- Acesso: `https://<IP-da-LAN>:8081`, direto, sem nome e sem Nginx Proxy Manager. O navegador pede para aceitar o certificado uma vez.
- Quem pode usar a porta é decidido pela lista `ADMIN_ALLOW_CIDRS` (padrão `127.0.0.1/32`; no deploy `192.168.15.0/24 100.64.0.0/10`, ou seja, LAN e Tailscale). O resto recebe 403. A 8081 não confia em nenhum cabeçalho de IP.
- Por padrão (`ADMIN_BIND=127.0.0.1`) ela só ouve na própria máquina; no servidor use `ADMIN_BIND=0.0.0.0` e deixe a lista filtrar.
- O Nginx Proxy Manager, se existir, só cuida da porta pública 8080 (veja `docs/DEPLOY.md`).
- Login com o usuário de `ADMIN_USERNAME` e a senha cujo hash está em `secrets/admin_password_hash.txt`. A sessão expira em 8 horas, ou após 30 minutos sem uso. Cinco tentativas erradas em 60 segundos bloqueiam novas tentativas por um minuto.
- No painel você configura as URLs das quatro lives, cola os cookies do Studio para o contador e acompanha o estado dos workers.

## Desenvolvimento e testes

Veja [`CONTRIBUTING.md`](CONTRIBUTING.md) para ambiente, regras de qualidade e comandos de teste. Resumo:

```sh
python -m pip install -r requirements-dev.txt
pre-commit install
ruff check .
```

## Segurança

Vulnerabilidades: siga o procedimento em [`SECURITY.md`](SECURITY.md). Nunca faça commit de `.env`, de `secrets/` ou de chaves. O pre-commit e o CI rodam gitleaks e Trivy para barrar isso.

## Licença

Projeto privado. Nenhuma licença de uso ou redistribuição é concedida.
