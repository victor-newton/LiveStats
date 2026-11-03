# LiveStats — Deploy no ZimaOS

Passo a passo para colocar o LiveStats no servidor (ZimaOS, `192.168.15.62`), trocar o Nginx Proxy Manager (NPM) para ele e desligar os apps antigos. Os scripts estão em [`../deploy/`](../deploy/). Contexto de arquitetura: [`ARCHITECTURE.md`](ARCHITECTURE.md) (D29, D33, P1, P3, P10).

## Resumo

| Item | Valor |
|---|---|
| Servidor | ZimaOS, `192.168.15.62`, usuário SSH `Vitex`, **senha digitada a cada comando** (sem chave SSH) |
| Pasta de instalação | `/DATA/AppData/livestats` |
| Público (8080) | `https://lives.vitex.xyz.br` (Cloudflare Tunnel → NPM → LiveStats) e `http://192.168.15.62:8080` na LAN |
| Admin (8081) | `https://192.168.15.62:8081`, HTTPS autoassinado, só LAN `192.168.15.0/24` e Tailscale `100.64.0.0/10` |
| Usuário do admin | `Vitex` (a senha você digita no seu PC; o servidor guarda só o hash) |
| Apps antigos | **parados**, continuam instalados (rollback) |

**Quantas senhas de SSH o deploy pede (caminho recomendado): 1**, no passo 2. Cada comando de `deploy/enviar.sh` abre uma conexão SSH nova e pede a senha de novo. O ssh do Windows não reaproveita conexões, então os passos foram agrupados:

| Passo | Roda onde | Senhas SSH |
|---|---|---|
| 1. Preflight (opcional) | seu PC → servidor | 1 |
| 2. Instalar (preflight + build + secrets + subir + checagens) | seu PC → servidor | **1** |
| 3. NPM, opção B (recomendada) | só telas do NPM | 0 |
| 3. NPM, opção A (alternativa) | seu PC → servidor | +1 (ou 0 se usar `--conectar-npm` já no passo 2) |
| 4–8. Navegador, painel, vMix/OBS, Google Cloud | seu PC | 0 |
| Status / rollback, se precisar | seu PC → servidor | 1 cada |

Total recomendado: **1 senha** (2 se rodar o preflight antes, que é opcional porque o passo 2 já o executa e para sem alterar nada se achar um bloqueio).

## O que roda onde

- **Seu PC (Git Bash do Windows)**: `deploy/enviar.sh`. Ele monta o pacote com `git archive` do **commit atual** (alterações não commitadas não vão), pede a chave do YouTube e a senha do admin **sem eco** e faz **um único** `ssh`. O pacote e os dois segredos seguem pela entrada padrão dessa conexão: nada vai em argumento de linha de comando (que apareceria no `ps`), nada é gravado em disco no servidor além do que o instalador grava de propósito (a chave do YouTube em `secrets/youtube_api_key.txt` e o **hash** da senha).
- **Servidor**: `deploy/install.sh` (chamado pelo `enviar.sh`), `preflight.sh`, `connect_npm.sh`, `status.sh`, `rollback.sh`. Todos em `sh` POSIX, idempotentes, em português, e nunca imprimem valores secretos.

## Antes de começar

1. **Chave nova do YouTube Data API v3** criada no Google Cloud (restrita à API YouTube Data v3). A antiga está comprometida (S1) e será revogada no passo 8.
2. **ID do canal** (`UC` + 22 caracteres).
3. No PC: Git Bash com `git` e `ssh`, e o repositório com tudo commitado (o branch que você quer instalar).
4. Escolha uma senha de admin de **pelo menos 12 caracteres**.
5. Abra o **Git Bash/mintty** (não um terminal embutido sem console): o `ssh` precisa do console para pedir a senha.
6. O usuário `Vitex` precisa falar com o Docker. Se o preflight disser que não, rode no servidor, uma vez, `sudo usermod -aG docker Vitex` e reconecte (ou configure `sudo` sem senha). Os scripts não pedem senha de `sudo`.

## Passo 1 — Preflight (opcional, 1 senha)

```sh
bash deploy/enviar.sh preflight
```

Só leitura. Mostra versões de Docker/Compose, espaço em disco (mínimo recomendado 5 GB para construir as imagens), se `/DATA/AppData` existe e é gravável, se 8080/8081 estão livres, o container e as redes do NPM e do cloudflared, se o usuário está no grupo `docker`, e quais containers de apps antigos existem. Termina com o número de bloqueios. A primeira conexão pergunta se você confia na chave do servidor: responda `yes`.

## Passo 2 — Instalar (1 senha)

```sh
bash deploy/enviar.sh instalar --channel-id UCxxxxxxxxxxxxxxxxxxxxxx
```

O script pergunta, sem eco, a chave do YouTube e a senha do admin (duas vezes) e depois a senha do SSH. Opções úteis:

| Opção | Para quê |
|---|---|
| `--extra-san 100.x.y.z` | colocar também o IP do Tailscale no certificado (repita para mais de um) |
| `--chave-youtube-arquivo ARQ` | ler a chave de um arquivo local (apague depois) em vez de digitar |
| `--admin-allow "..."` | outra lista da 8081 (padrão `192.168.15.0/24 100.64.0.0/10`) |
| `--public-base-url URL` | padrão `https://lives.vitex.xyz.br` |
| `--conectar-npm [NOME]` | já faz a opção A do passo 3 na mesma sessão (0 senhas extras) |
| `--sem-segredos` | reinstalar/atualizar quando os secrets já existem (não pergunta chave nem senha) |
| `--renew-tls`, `--skip-build` | certificado novo / não reconstruir as imagens |

O que acontece no servidor, nesta ordem:

1. **Preflight**; se achar bloqueio, para sem alterar nada.
2. Cria `/DATA/AppData/livestats` e extrai o código. Escreve o `.env` de produção (`ADMIN_BIND=0.0.0.0`, `ADMIN_ALLOW_CIDRS="192.168.15.0/24 100.64.0.0/10"`, `PUBLIC_BASE_URL`, `YOUTUBE_CHANNEL_ID`, `ADMIN_USERNAME=Vitex`, `TRUSTED_PROXY_CIDR` vazio). Nenhum segredo no `.env`.
3. `docker compose build` (a primeira vez leva vários minutos: Chromium do Playwright).
4. Gera o que falta em `secrets/` (nada existente é sobrescrito): par de chaves dos cookies (`tools.gen_cookie_keys`), hash argon2 da senha (`tools.hash_password`, a senha entra por stdin), certificado TLS autoassinado com `IP:192.168.15.62` e `IP:127.0.0.1` no SAN (825 dias) e a chave do YouTube.
5. **Permissões**: a pasta `secrets/` é `700` e os 6 arquivos são `644`, porque os containers rodam como uid `10001` (api/workers) e `101` (web) e precisam **ler** os arquivos (uma chave privada `0600` de outro dono impediu o worker-inscritos de subir no V2). Como a pasta é `700`, só o usuário `Vitex` chega a eles no host.
6. `docker compose up -d` e espera os 4 serviços ficarem `healthy` (até 6 minutos). Se algum não ficar, mostra as últimas linhas do log (com chaves, hashes e PEM ocultados) e sai com erro.
7. Checagens: `GET http://127.0.0.1:8080/health`, `/live1.json`, `/inscritos.json`, `/api/admin/session` (deve dar **404**: o admin não existe na 8080) e `GET https://192.168.15.62:8081/health` conferindo o certificado gerado. Se a 8081 devolver 403 ao testar do próprio servidor, o instalador só avisa: teste pelo seu PC (passo 5).

Pode rodar de novo à vontade: o `.env` e os secrets existentes são mantidos (o `TRUSTED_PROXY_CIDR` também).

## Passo 3 — Nginx Proxy Manager (você, nas telas do NPM)

Nada muda no Cloudflare: o túnel continua entregando `*.vitex.xyz.br` ao NPM em `http://192.168.15.62:80`.

Duas formas de o NPM alcançar o LiveStats. A opção B é a mais simples. **Em produção (v1.0.0) está a opção A**, porque os overlays são consumidos de fora da rede (vMix/OBS remotos pelo `lives.vitex.xyz.br`) e cada visitante precisa do próprio balde no limite de requisições.

### Opção B (recomendada) — NPM aponta para `192.168.15.62:8080`

No NPM, **Proxy Hosts**:

1. Edite **`lives.vitex.xyz.br`**: Scheme `http`, Forward Hostname/IP `192.168.15.62`, Forward Port `8080`. Mantenha o SSL/Let's Encrypt que já existe. Salve.
2. **`canaldobenja.vitex.xyz.br`**: use **Disable** (menu `⋮` do host). Não apague (rollback).

`TRUSTED_PROXY_CIDR` fica **vazio**: o Nginx do LiveStats ignora `CF-Connecting-IP` (comportamento seguro). Custo: o `limit_req` da 8080 (10 req/s por IP) conta o IP do NPM, ou seja, todos os visitantes que chegam pelo nome `lives.vitex.xyz.br` dividem um balde. O vMix/OBS na LAN, falando direto com `192.168.15.62:8080`, continuam com o IP real.

**Por que é a recomendada:** não mexe no container do NPM, não depende dele continuar conectado a uma rede, e não cria uma dependência de DNS interno: com a opção A, se o LiveStats estiver desligado (rollback, manutenção) o nome `livestats-web` deixa de resolver, e o Nginx do NPM **não recarrega a configuração (ou nem sobe)**, derrubando também os outros sites dele. Com IP e porta, o NPM só devolve 502 para este host. O que se perde (IP real por visitante) tem pouco valor aqui: o tráfego público são poucos clientes (vMix/OBS) e os JSON são baratos.

### Opção A (alternativa) — NPM na rede `livestats-edge` com IP fixo

Dá o IP real por visitante no `limit_req` (D29/D33). Roda no servidor:

```sh
bash deploy/enviar.sh conectar-npm           # 1 senha; ou use  instalar --conectar-npm  no passo 2
```

O `connect_npm.sh` acha o container do NPM, faz `docker network connect --ip 172.29.80.11 livestats-edge <npm>`, grava `TRUSTED_PROXY_CIDR=172.29.80.11/32` no `.env` e recria só o `web`. Depois, no NPM, edite `lives.vitex.xyz.br` para Forward Hostname `livestats-web`, porta `8080`, e desative `canaldobenja.vitex.xyz.br`.

**Reinicie o NPM uma vez depois de conectá-lo** (`docker restart nginxproxymanager`, alguns segundos fora do ar para todos os sites dele). O Nginx do NPM monta a lista de servidores DNS quando o container sobe; se ele subiu só na rede `bridge`, continua sem o DNS interno do Docker e o host dá **502** com `livestats-web could not be resolved` no log, mesmo com `getent hosts livestats-web` funcionando dentro do container. Depois do restart a ligação com a rede e o DNS ficam valendo, inclusive após reboot do servidor.

**Atenção:** o `docker network connect` feito à mão **some se o ZimaOS recriar o container do NPM** (atualização do app, reinstalação). Se isso acontecer, `livestats-web` deixa de resolver e o NPM pode falhar ao recarregar; rode `connect_npm.sh` de novo (ou volte o host para a opção B). Antes de qualquer rollback, volte o host `lives.vitex.xyz.br` ao destino antigo no NPM (o `rollback.sh` já solta o NPM da rede).

## Passo 4 — Primeiro acesso ao admin (você, no navegador do PC)

1. Abra `https://192.168.15.62:8081`. O navegador avisa que o certificado não é confiável (é autoassinado e o IP está no SAN): **Avançado → continuar**. Só precisa na primeira vez por navegador. O PC precisa estar na LAN `192.168.15.0/24` ou no Tailscale; fora disso a resposta é 403.
2. Entre com o usuário **`Vitex`** e a senha de admin que você digitou no `enviar.sh`.
3. Na aba **Lives**, cole as URLs das 4 lives (ou deixe em branco as que não usar) e salve; cada cartão tem o seu botão Salvar. O worker de lives atualiza a cada `LIVE_INTERVAL` segundos (30 s).
4. Na aba **Inscritos**, no cartão **Cookies**, cole o JSON de cookies do YouTube Studio (EditThisCookie, como no app antigo) e salve. **Só você faz isso**: os cookies são cifrados no navegador/api com a chave pública, e só o `worker-inscritos` tem a chave privada. Ninguém mais os vê. Confira no painel o selo "N cookies carregados" e que o contador de inscritos passa a atualizar.

## Passo 5 — Atualizar vMix e OBS

Os caminhos são os mesmos do legado (aliases byte a byte). Confirme no seu vMix qual porta cada app antigo usava; pelo NPM, `lives.vitex.xyz.br` era a `8001` e `canaldobenja.vitex.xyz.br` a `8000`.

| Uso | URL antiga | URL nova (internet) | URL nova (LAN, recomendada p/ vMix/OBS local) |
|---|---|---|---|
| Data source live 1..4 | `https://lives.vitex.xyz.br/live1.json` … `live4.json` (ou `http://192.168.15.62:8001/liveN.json`) | `https://lives.vitex.xyz.br/live1.json` … `live4.json` | `http://192.168.15.62:8080/live1.json` … `live4.json` |
| Contador de inscritos (JSON) | `https://canaldobenja.vitex.xyz.br/inscritos.json` (ou `http://192.168.15.62:8000/inscritos.json`) | `https://lives.vitex.xyz.br/inscritos.json` | `http://192.168.15.62:8080/inscritos.json` |
| Overlay do contador (Browser Source) | `https://canaldobenja.vitex.xyz.br/contador` (ou `:8000/contador`) | `https://lives.vitex.xyz.br/contador` | `http://192.168.15.62:8080/contador` |
| Saúde | `.../health` | `https://lives.vitex.xyz.br/health` | `http://192.168.15.62:8080/health` |

Dica: na LAN use o IP e a porta 8080, que não dependem do túnel nem do NPM. O `canaldobenja.vitex.xyz.br` foi desativado: nada novo aponta para ele.

## Passo 6 — Revogar a chave antiga do YouTube

No Google Cloud Console (projeto da chave antiga) → **APIs e serviços → Credenciais**: exclua/revogue a chave que estava no código do app antigo (S1). Só faça depois de confirmar que `/live1.json` já mostra dados com a chave nova (passo 7).

## Passo 7 — Checklist de verificação pós-deploy

Marque cada item. Comandos do servidor: `bash deploy/enviar.sh status` (1 senha).

- [ ] `status`: os 4 containers `healthy`, 0 ou poucos reinícios; heartbeat dos 2 workers "recente".
- [ ] `http://192.168.15.62:8080/health` responde 200 (do PC).
- [ ] `http://192.168.15.62:8080/live1.json` traz o objeto da live 1 (depois de configurar o slot; sem URL, `sem_url`).
- [ ] `http://192.168.15.62:8080/api/admin/session` e `/admin` dão **404** (o admin não existe na 8080).
- [ ] `https://lives.vitex.xyz.br/health` responde 200 pela internet (ex.: do celular com dados móveis).
- [ ] `https://lives.vitex.xyz.br/api/admin/session` dá 404 (o admin não vaza pelo nome público).
- [ ] `https://192.168.15.62:8081` abre (após aceitar o certificado) e o login funciona; de uma rede fora da LAN/Tailscale dá 403 ou não conecta.
- [ ] `http://192.168.15.62:8081` (sem TLS) **não** serve o painel.
- [ ] Contador de inscritos atualiza e `/contador` anima no OBS/vMix.
- [ ] As stats das lives atualizam (views/likes) e o painel mostra o título como texto.
- [ ] Seu PC reiniciando o servidor: `docker ps` mostra os 4 containers subindo sozinhos (`restart: unless-stopped`).
- [ ] Chave antiga do YouTube revogada; apps antigos parados; `canaldobenja` desativado no NPM.
- [ ] `git ls-files secrets` (no repositório) mostra só `secrets/README.md`.

## Aparecer na tela inicial do ZimaOS (opcional)

**Por quê.** O ZimaOS só lista na tela inicial os apps cujo compose está em `/var/lib/casaos/apps/<projeto>/docker-compose.yml` (pasta do root). O LiveStats roda de `/DATA/AppData/livestats` e funciona normalmente, mas não aparece como cartão. Este passo grava uma cópia "resolvida" do compose (caminhos absolutos, `.env` já aplicado, metadados `x-casaos` mantidos) naquela pasta e recria os containers a partir dela.

```sh
bash deploy/enviar.sh registrar-zimaos            # registra (ou atualiza) o cartão
bash deploy/enviar.sh registrar-zimaos --remover  # desfaz: volta a rodar pelo compose de /DATA/AppData/livestats
```

- **Senhas: 2.** A do SSH e a do `sudo` (o usuário `Vitex` tem sudo com senha; o modo usa `ssh -t`, então o sudo pergunta no seu terminal). O instalar (passo 2) deve ter sido feito antes: este modo roda o código que ele deixou no servidor.
- O arquivo registrado fica com dono root e modo 600. Antes de gravar, o script confere que o conteúdo de nenhum arquivo de `secrets/` aparece nele e aborta se aparecer. Os volumes nunca são apagados (nem no `--remover`).
- Na primeira vez os containers são recriados (alguns segundos fora do ar), porque o Docker não recria sozinho só porque o compose mudou de pasta. Rodar de novo sem mudanças não reescreve nada.
- **Depois de registrado**, as reinstalações respeitam o registro: `instalar` atualiza código, imagens, `.env` e segredos, mas **para antes de recriar os containers** (saída 2) e manda rodar `bash deploy/enviar.sh registrar-zimaos` (com `--recriar-web` se o certificado TLS foi renovado). `conectar-npm` e `rollback` passam a usar `ssh -t` e pedir a senha do sudo (o rollback derruba pelo arquivo registrado e deixa o cartão como app parado).
- **Alerta:** editar o app pela tela do ZimaOS (configurações do app, "salvar") pode reescrever esse YAML e tirar as proteções (usuário não-root, `read_only`, `cap_drop`, rede interna). Faça qualquer mudança pelos scripts (`instalar`, `registrar-zimaos`) e não pelo editor do ZimaOS. Em caso de dúvida, rode `registrar-zimaos` de novo: ele regrava o arquivo correto.

## Registro do deploy da v1.0.0 (2026-11-03)

O que foi feito no servidor, na ordem, e o resultado de cada passo:

| Passo | Comando | Resultado |
|---|---|---|
| Acesso ao Docker | `ssh -t Vitex@192.168.15.62 "sudo usermod -aG docker Vitex"` (feito por você) | `Vitex` no grupo `docker` (gid 104) |
| Preflight | `bash deploy/enviar.sh preflight` | 0 bloqueios depois da correção do `DOCKER_CONFIG` (Docker 27.5.1, Compose 2.32.4, 215 GB livres, 8080/8081 livres) |
| Instalação | `bash deploy/enviar.sh instalar --channel-id UC... --chave-youtube-arquivo secrets/youtube_api_key.txt --admin-allow "192.168.15.0/24 100.64.0.0/10" --public-base-url https://lives.vitex.xyz.br --conectar-npm` | 4 serviços `healthy`; NPM em `livestats-edge` com IP `172.29.80.11`; `TRUSTED_PROXY_CIDR=172.29.80.11/32` |
| NPM | `lives.vitex.xyz.br` → `http://livestats-web:8080`; `canaldobenja.vitex.xyz.br` desativado; `docker restart nginxproxymanager` | público `/health`, `/live1.json`, `/contador`, `/overlay/live.html` com 200; `/api/admin/*` com 404 |
| Cookies do Studio | colados pelo painel (aba Inscritos) | primeira leitura 753.890; sessão renovada pelo worker (`source='worker'`); quedas do contador aparecem |
| IP real | log da 8080 | aparecem o IP da LAN e o IP público do visitante; o IP do NPM não aparece |
| Tela inicial | `bash deploy/enviar.sh instalar ... --sem-segredos --skip-build` e `bash deploy/enviar.sh registrar-zimaos` | cartão LiveStats na tela inicial; containers vindos de `/var/lib/casaos/apps/livestats/docker-compose.yml` |

Os apps antigos (Live Stats e YT Inscritos) continuam instalados e **parados**, para rollback.

## Rollback

```sh
bash deploy/enviar.sh rollback        # confirma com "sim"; 1 senha
```

`rollback.sh` solta o NPM/cloudflared da rede `livestats-edge`, faz `docker compose down` **sem apagar os volumes** (`livestats-data` e `livestats-admin` ficam) e zera o `TRUSTED_PROXY_CIDR`. Depois, por você:

1. NPM: `lives.vitex.xyz.br` volta para `http://192.168.15.62:8001`; reative `canaldobenja.vitex.xyz.br` (`http://192.168.15.62:8000`).
2. Painel do ZimaOS: ligue os apps antigos **Live Stats** e **YT Inscritos**.
3. vMix/OBS: volte as URLs antigas (tabela do passo 5).

Para reinstalar o LiveStats depois: `bash deploy/enviar.sh instalar --channel-id UC... --sem-segredos` (dados e secrets continuam).

## Manutenção

- **Atualizar o código**: commite, rode `bash deploy/enviar.sh instalar --channel-id UC... --sem-segredos` (1 senha). Reconstrói as imagens e recria o que mudou. Se o app estiver registrado no painel do ZimaOS, termine com `bash deploy/enviar.sh registrar-zimaos` (veja a seção acima).
- **Trocar a senha do admin**: no servidor, `sh /DATA/AppData/livestats/deploy/install.sh --lan-ip 192.168.15.62 --reset-admin-password --skip-build` (pergunta no terminal; precisa de `ssh -t`) e depois `docker compose up -d --force-recreate api`.
- **Renovar o certificado** (825 dias): `instalar --renew-tls --sem-segredos`.
- **Trocar a chave do YouTube**: apague `secrets/youtube_api_key.txt` no servidor e rode `instalar` de novo (ele pergunta a chave), ou substitua o arquivo e `docker compose up -d --force-recreate worker-lives`.
- **Ver o estado**: `bash deploy/enviar.sh status`.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Preflight: "não consegue falar com o Docker" | `Vitex` fora do grupo `docker` e sem `sudo` sem senha (veja "Antes de começar", item 6). Correção usada em produção, uma vez: `ssh -t Vitex@192.168.15.62 "sudo usermod -aG docker Vitex"` |
| Preflight: "Plugin 'docker compose' (v2) não encontrado", com `WARNING: Error loading config file: /DATA/.docker/config.json: permission denied` | no ZimaOS a home do `Vitex` é `/DATA`, e `/DATA/.docker` é do root (`710`); o CLI não lê a configuração e não acha o plugin do sistema. Os scripts já tratam isso desde a v1.0.0 (`deploy/lib.sh` usa um `DOCKER_CONFIG` próprio). Para comandos manuais: `export DOCKER_CONFIG=$(mktemp -d)` antes do `docker compose` |
| `lives.vitex.xyz.br` dá 502 logo após `conectar-npm` (opção A), log do NPM com `could not be resolved` | o NPM guardou o DNS de antes de entrar na rede `livestats-edge`; `docker restart nginxproxymanager` (veja o passo 3) |
| O LiveStats roda mas não aparece na tela inicial do ZimaOS | ele não está registrado em `/var/lib/casaos/apps`; rode `bash deploy/enviar.sh registrar-zimaos` |
| `web` não sobe, log "certificado/chave" | falta ou permissão errada em `secrets/admin_tls_*.pem` (devem ser `644`); rode `instalar --renew-tls --sem-segredos` |
| worker-inscritos reiniciando, "permission denied" na chave | `secrets/cookie_private_key.txt` com modo `600` (precisa `644`; o instalador já faz isso) |
| 8081 devolve 403 | seu IP não está em `ADMIN_ALLOW_CIDRS` (edite o `.env` e `docker compose up -d web`) |
| 429 em `lives.vitex.xyz.br` com muitos clientes (opção B) | todos dividem o balde do IP do NPM; passe para a opção A |
| NPM não recarrega após rollback (opção A) | o host ainda aponta para `livestats-web`; volte o destino antigo |
