# Mapa de segurança — Live Stats e YT Inscritos

Código copiado de `/opt/live-stats` e `/opt/yt-inscritos` no ZimaOS (VitexServer) em 02/11/2026.
Ficaram de fora da cópia: `data/` dos dois apps e `perfil_bot/` (perfil do Chrome).
O código original está intacto em `codigo-original/`.

## Segredos e dados sensíveis

| # | O quê | Onde aparece | Gravidade | O que fazer |
|---|---|---|---|---|
| S1 | Chave da API do YouTube (Google Cloud) | `live-stats/live_worker.py:16` (valor padrão no código), `live-stats/docker-compose.yml:9`, YAML do app no painel ZimaOS | **Crítica** | Gerar chave nova no Google Cloud, restringir à YouTube Data API v3, revogar a atual. Só via `.env`, nunca no código. |
| S2 | Cookies de sessão do Google/YouTube Studio (`cookies.json`) | `/opt/yt-inscritos/data/cookies.json` no servidor (enviado pelo `/admin`) | **Crítica** | Esses cookies dão acesso à conta do canal. Nunca no Git; permissão `600`; considerar conta Google dedicada só de leitura (função "Visualizador" no Studio). |
| S3 | Print do YouTube Studio com receita estimada | `yt-inscritos/debug.png` e `/app/data/debug.png` (gerado a cada recarga) | Alta | Não versionar. Gerar só com flag de debug ligada. |
| S4 | Perfil do Chrome | volume `/opt/yt-inscritos/perfil_bot` | Alta | Não versionar. O código atual nem usa essa pasta (usa `cookies.json`): pode ser removida. |
| S5 | ID do canal | `yt-inscritos/scraper.py:18`, `docker-compose.yml:7` | Baixa (é público) | Mover para `.env` por organização, não por sigilo. |

## Brechas no código

| # | App | Problema | Onde | Risco |
|---|---|---|---|---|
| V1 | YT Inscritos | `/admin` e `POST /admin/cookies` **sem autenticação** | `server.py:84`, `server.py:330` | Quem alcançar a porta 8000 troca os cookies do bot. O `SETUP.md` manda expor essa porta inteira no Cloudflare Tunnel, então o painel pode estar público na internet. |
| V2 | Live Stats | `POST /config` **sem autenticação** | `live_server.py:52` | Qualquer um na rede troca os 4 slots e gasta a cota da sua chave de API. |
| V3 | Live Stats | **XSS armazenado**: título do vídeo e URL do slot vão direto para `innerHTML` | `live_admin.html:205-227` | Um vídeo com título malicioso, ou uma URL enviada via V2, executa script no navegador de quem abre o painel. |
| V4 | Ambos | CORS `allow_origins=["*"]` | `live_server.py:12`, `server.py:11` | Qualquer site pode chamar a API a partir do navegador de quem está na sua rede (combina com V1/V2). |
| V5 | Ambos | Painel admin e endpoints públicos no mesmo servidor/porta | `live_server.py`, `server.py` | Não dá para expor só o JSON público sem expor o admin junto. |
| V6 | Ambos | Containers rodam como `root`; dependências sem versão fixa (Live Stats) e Chrome `current` | `Dockerfile` | Build não reproduzível; impacto maior se algo for explorado. |
| V7 | YT Inscritos | Chrome com `--remote-debugging-port=9222` | `scraper.py:65` | Hoje só dentro do container (não publicado), mas desnecessário: quem acessa essa porta controla o navegador logado. |

## O que vai e o que não vai para o GitHub

Vai: `*.py`, `*.html`, `Dockerfile`, `docker-compose.yml` (com `${VARIAVEIS}`), `requirements.txt`, `entrypoint.sh`, `.env.example`, README.

Não vai (`.gitignore`): `.env`, `data/`, `perfil_bot/`, `cookies.json`, `debug.png`, `*.tar.gz`.

## Próximos passos sugeridos

1. Trocar a chave da API (S1) — antes de qualquer commit.
2. Descobrir o que está exposto hoje: rotas do Cloudflare Tunnel e do Nginx Proxy Manager para as portas 8000/8001.
3. Reestruturar cada app em: `frontend/` (HTML estático, só faz `fetch`), `api/` (FastAPI, só leitura pública + rotas admin com autenticação), `worker/` (coleta, único que conhece a chave/cookies), segredos em `.env`, só o front/proxy publicado.
4. Corrigir V1–V7 durante a reestruturação.
