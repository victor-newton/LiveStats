# Formatos legados — o que OBS/vMix recebem hoje

Extraído do código em `codigo-original/` (`live_worker.py`, `live_server.py`, `server.py`, `scraper.py`, `contador.html`). A nova aplicação precisa devolver **os mesmos bytes** nesses endpoints. Os arquivos-ouro estão em [`tests/contract/legacy/`](../tests/contract/legacy/) (somente leitura; os testes `API-LEG-*` comparam byte a byte).

## 0. Regras de serialização (valem para todos os JSON legados)

O FastAPI atual responde com `JSONResponse`, que serializa assim:

- `json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":"))` → **compacto, sem espaços, sem quebra de linha no fim**;
- **ordem das chaves = ordem de inserção** (ver 1.2: a ordem muda entre slot ativo e slot sem URL);
- caracteres não ASCII em UTF-8 literal (`é`, `—`, `ç`), **nunca** como escape `\uXXXX`;
- `<`, `>`, `&` não são escapados;
- cabeçalhos das respostas 200 de `/live{n}.json` e `/inscritos.json`: `Content-Type: application/json` (sem `charset`) e `Cache-Control: no-store`; as respostas de erro (404/503) têm só `Content-Type: application/json`, sem `Cache-Control` (a nova aplicação pode acrescentar `no-store` nelas);
- CORS: o legado respondia `Access-Control-Allow-Origin: *` (CORSMiddleware, GET/POST). A nova aplicação **mantém** `Access-Control-Allow-Origin: *` e `Access-Control-Allow-Methods: GET` nos GET públicos de dados (`/live{n}.json`, `/inscritos.json`, `/api/public/*`), emitidos pelo Nginx, com preflight `OPTIONS` → 204 e sem `Allow-Credentials` (decisão D10, que substitui o CORS fechado do PLANO por pedido do usuário); `/contador`, `/health` e as rotas de admin não têm CORS.

## 1. `GET /live{n}.json` (n = 1..4)

### 1.1 Corpo

**Array JSON com exatamente um objeto.** Campos e tipos:

| Campo | Tipo JSON | Valores / formato |
|---|---|---|
| `slot` | inteiro | 1..4 |
| `ativo` | **string** | `"true"` ou `"false"` (**não** é booleano) |
| `video_id` | string | 11 caracteres `[A-Za-z0-9_-]`; `""` quando `sem_url` |
| `titulo` | string | título do vídeo, texto livre (pode conter `<`, `"`, emoji); `""` quando `sem_url` |
| `status` | string | `"ao_vivo"`, `"agendada"`, `"encerrada"` ou `"sem_url"` |
| `views` | inteiro | espectadores simultâneos se `ao_vivo` e `concurrentViewers` existe e é > 0; senão `viewCount`; `0` se ausente |
| `likes` | inteiro | `likeCount`; `0` se ausente (likes ocultos) |
| `views_formatado` | string | ver 1.3 |
| `likes_formatado` | string | ver 1.3 |
| `atualizado_em` | string | `YYYY-MM-DDTHH:MM:SS.ffffffZ` (UTC; microssegundos presentes, exceto no raro caso de serem exatamente zero, quando o `isoformat` os omite) |

### 1.2 Ordem das chaves (bytes!)

- **Slot com vídeo (`"ativo":"true"`)** — o objeto vem das estatísticas e `slot`/`ativo` são acrescentados no fim:
  `video_id, titulo, status, views, likes, views_formatado, likes_formatado, atualizado_em, slot, ativo`
- **Slot sem URL (`"ativo":"false"`)** — o objeto é montado já com `slot` e `ativo` na frente:
  `slot, ativo, video_id, titulo, status, views, likes, views_formatado, likes_formatado, atualizado_em`

Exemplos exatos (cada um é um arquivo-ouro, sem quebra de linha no final):

`live1_ao_vivo.json`
```
[{"video_id":"dQw4w9WgXcQ","titulo":"Sorteio ao vivo — açaí & café <b>teste</b>","status":"ao_vivo","views":1234,"likes":56,"views_formatado":"1.2K","likes_formatado":"56","atualizado_em":"2026-11-02T14:32:00.123456Z","slot":1,"ativo":"true"}]
```
`live4_sem_url.json`
```
[{"slot":4,"ativo":"false","video_id":"","titulo":"","status":"sem_url","views":0,"likes":0,"views_formatado":"0","likes_formatado":"0","atualizado_em":"2026-11-02T14:32:00.456789Z"}]
```
Também em `tests/contract/legacy/`: `live2_agendada.json`, `live3_encerrada.json`.

### 1.3 Origem dos valores (para o worker-lives)

- Uma chamada `GET https://www.googleapis.com/youtube/v3/videos` com `id=<ids únicos separados por vírgula>` e `part=statistics,snippet,liveStreamingDetails` por ciclo (modo batch). Na versão nova a chave vai no cabeçalho `X-Goog-Api-Key`, **não** na query (decisão D18).
- `status`: `snippet.liveBroadcastContent == "live"` → `ao_vivo`; `"upcoming"` → `agendada`; qualquer outro valor/ausente → `encerrada`. `sem_url` é só do slot sem vídeo.
- `views = int(statistics.viewCount or 0)`; `likes = int(statistics.likeCount or 0)`; se `ao_vivo` e `liveStreamingDetails.concurrentViewers` for verdadeiro (existe e não é `0`/vazio) → `views = int(concurrentViewers)`.
- `formatar(n)`: `n >= 1_000_000` → `f"{n/1_000_000:.1f}M"`; `n >= 1_000` → `f"{n/1_000:.1f}K"`; senão `str(n)`. Vetores (saída do código original):

| n | resultado |
|---|---|
| 0 | `"0"` |
| 5 | `"5"` |
| 999 | `"999"` |
| 1000 | `"1.0K"` |
| 1234 | `"1.2K"` |
| 1250 | `"1.2K"` |
| 1999 | `"2.0K"` |
| 9999 | `"10.0K"` |
| 99950 | `"100.0K"` |
| 999949 | `"999.9K"` |
| 999950 | `"1000.0K"` |
| 999999 | `"1000.0K"` |
| 1000000 | `"1.0M"` |
| 1250000 | `"1.2M"` |
| 12345678 | `"12.3M"` |

  (`999950 → "1000.0K"` é comportamento do legado e deve ser mantido; Pendência P7.)

- `extrair_video_id(url)`: `re.search` com `(?:v=|youtu\.be/|/live/)([a-zA-Z0-9_-]{11})` e, se não casar, `^([a-zA-Z0-9_-]{11})$`; a URL é usada após `strip()`. Vetores:

| Entrada | ID |
|---|---|
| `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | `dQw4w9WgXcQ` |
| `https://youtu.be/dQw4w9WgXcQ?t=5` | `dQw4w9WgXcQ` |
| `https://www.youtube.com/live/dQw4w9WgXcQ` | `dQw4w9WgXcQ` |
| `dQw4w9WgXcQ` | `dQw4w9WgXcQ` |
| `https://youtube.com/watch?list=x&v=dQw4w9WgXcQ&t=1` | `dQw4w9WgXcQ` |
| `https://www.youtube.com/watch?v=dQw4w9WgXcQextra` | `dQw4w9WgXcQ` (pega os 11 primeiros) |
| `https://www.youtube.com/shorts/dQw4w9WgXcQ` | nenhum (não suportado no legado) |
| `https://www.youtube.com/embed/dQw4w9WgXcQ` | nenhum |
| `abc` | nenhum |

  A `api` aplica o **mesmo** padrão ao salvar slots (devolve 422 quando não há ID, decisão D6) e o `worker-lives` ao usar; os dois conjuntos de testes usam estes vetores.

- Mesmo vídeo em dois slots → um só ID na chamada; cada slot recebe sua linha com seu `slot`.

### 1.4 Comportamento do ciclo (worker-lives), a preservar

1. Lê os 4 slots a cada ciclo (intervalo de 30 s no compose atual; padrão de código era 15 s — decisão D17).
2. Slot vazio ou URL sem ID → grava a linha `sem_url` (todo ciclo).
3. Se houver IDs válidos: uma única chamada. Em erro, **não** altera as linhas dos slots ativos (continuam com o dado anterior). Vídeo não retornado → linha daquele slot inalterada.
4. Antes do primeiro ciclo não há dado → 503 (1.5).

### 1.5 Erros (corpo = `{"detail": "..."}`, mesma serialização)

| Situação | Status | Corpo exato |
|---|---|---|
| `n` fora de 1..4 (`/live0.json`, `/live5.json`) | 404 | `{"detail":"Slot inválido (1-4)"}` |
| sem dado ainda | 503 | `{"detail":"Dados ainda não disponíveis"}` |
| `n` não numérico | 422 no legado; na nova aplicação o Nginx só encaminha `^/live[0-9]+\.json$`, o resto é 404 do Nginx (diferença aceita, P11) | — |

## 2. `GET /inscritos.json`

Corpo: **objeto** (não array), 3 campos, nesta ordem:

| Campo | Tipo | Formato |
|---|---|---|
| `inscritos` | **string** | número já filtrado por `filtrar_numero` (ver 2.1); ex.: `"460600"`, `"460.600"` |
| `raw` | string | texto cru lido da página |
| `atualizado_em` | string | `YYYY-MM-DDTHH:MM:SS.ffffffZ`; **muda a cada leitura bem-sucedida** (~10 s), mesmo com o mesmo número |

Exemplo exato (`tests/contract/legacy/inscritos.json`):
```
{"inscritos":"460600","raw":"460600","atualizado_em":"2026-11-02T14:32:10.654321Z"}
```
Cabeçalhos: `Content-Type: application/json`, `Cache-Control: no-store`.

Erro sem dado: 503 `{"detail":"Dados ainda não disponíveis. Scraper inicializando..."}`. (O 500 do legado para arquivo corrompido deixa de existir: o dado vem do banco.)

O `SETUP.md` mostra `"inscritos": "460.6K"`; é documentação antiga. O código real grava o número por extenso (ver 2.1).

### 2.1 `filtrar_numero(texto)` (worker-inscritos) — vetores do código original

Regra: `lower = texto.lower().strip()`; se terminar em `b`/`m`/`k` (nesta ordem de teste) → `str(int(float(lower[:-1].replace(',', '.')) * mult))` (mult 1e9/1e6/1e3; se `float()` falhar segue adiante); senão remove tudo que não é dígito ou `.`; vazio → `None`; se a divisão por `.` tiver mais de uma parte e a segunda parte tiver >= 3 caracteres → `partes[0] + "." + resto[0:2] + resto[-1]` (descarta as partes seguintes). O valor só é gravado se `len(valor) >= 2`.

| Entrada | Saída |
|---|---|
| `460600` | `"460600"` |
| `460.600` | `"460.600"` |
| `460,600` | `"460600"` |
| `460.6K` | `"460600"` |
| `1.2M` | `"1200000"` |
| `3B` | `"3000000000"` |
| `2.5B` | `"2500000000"` |
| `12k` | `"12000"` |
| `1,5k` | `"1500"` |
| `12` | `"12"` |
| `1` | `"1"` (gravação rejeitada: tamanho < 2) |
| `1.234.567` | `"1.234"` (descarta `567`; bug do legado, P7) |
| `1.2345` | `"1.235"` |
| `1234.5678` | `"1234.568"` |
| `10.000` | `"10.000"` |
| `1.5.5k` | `"1.5.5"` |
| `1,234` | `"1234"` |
| `1 234` | `"1234"` |
| `  460600 ` | `"460600"` |
| `abc`, ``, `K` | `None` |

### 2.2 Como o contador é lido (worker-inscritos)

Página: `https://studio.youtube.com/channel/{CHANNEL_ID}/analytics/tab-overview/period-default/explore?c={CHANNEL_ID}&entity_type=CHANNEL&entity_id={CHANNEL_ID}&time_period=4_weeks&explore_type=SUBSCRIBERS` (CHANNEL_ID vem do `.env`, nunca do código). Três estratégias em ordem, dentro da página, sobre os elementos `yta-smooth-counter`; cada uma só vale se o texto montado casar `/\d{3,}/`:

1. concatenar o texto de `.counter-value.last` de cada contador;
2. concatenar o texto do **último `div`** de cada `.counter-sequence-inner`;
3. `innerText` (ou `textContent`) do contador inteiro.

Fluxo: abrir `https://www.youtube.com`, esperar 3 s, apagar cookies, inserir os cookies, abrir a URL do Studio, esperar `readyState == "complete"` (até 30 s, segue em frente se estourar), esperar mais 12 s. URL final contendo `accounts.google.com` ou `signin` → login perdido. Lê a cada `SCRAPE_INTERVAL` (10 s). 30 falhas seguidas (contador ausente ou valor inválido) → recarrega a página e zera o contador de falhas. Cookies novos → recarrega. Navegador: headless, janela 1280x900, `--disable-gpu`, `--disable-dev-shm-usage`, `--disable-blink-features=AutomationControlled`, user-agent `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36`, `navigator.webdriver` indefinido. **Sem** `--remote-debugging-port` (V7).

## 3. `GET /contador` (página HTML)

Resposta: `200`, `text/html; charset=utf-8`, o `contador.html`. Fundo **transparente**, pensado para Browser Source do OBS/vMix. Cópia fiel do original em `tests/contract/legacy/contador.original.html`.

Comportamento a preservar (o teste `WEB-OV-*` compara com o original):

- container `#contador`: `font-family 'YouTube Sans','Roboto',sans-serif; font-size 80px; font-weight 700; color #FFFFFF; letter-spacing -2px; display:flex; gap 0; align-items:center; filter: drop-shadow(0 2px 8px rgba(0,0,0,0.5))`; `body` transparente, centralizado, `overflow:hidden`, `min-height:100vh`.
- cada dígito = `.digito-wrap` (altura `1em`, `overflow:hidden`) com `.digito-inner` contendo 10 `span` (0..9, altura `1em`); o valor aparece por `transform: translateY(-Nem)`. `.` e `,` viram `span.separador` (`padding: 0 1px`).
- consulta o JSON a cada **5000 ms** com `?t=<Date.now()>`; ignora resposta não-OK; ignora valor vazio ou igual ao atual; exceções só geram `console.warn`.
- 1ª carga: monta o carrossel e anima cada dígito com `transition: transform 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94)` escalonado em `50 + idx*60` ms.
- valor com a mesma estrutura (mesma posição de separadores): só troca o `translateY` com `0.45s` e a mesma curva, sem reconstruir.
- estrutura diferente (mais ou menos dígitos): reconstrói e anima com atraso `idx*40` ms e `0.5s`.

Diferenças inevitáveis na versão nova (decisão D12), sem efeito visual: o HTML **não é idêntico byte a byte** porque `<script>` e `<style>` inline são proibidos pela CSP; CSS e JS passam a ser arquivos do mesmo domínio, o JS busca `/api/public/subscribers` (no original, `./inscritos.json`), e o carrossel é montado sem `innerHTML`. Também é servida em `/overlay/inscritos`.

## 4. `/health` (legado)

- live-stats: `{"status":"ok","server_time":"<iso>Z"}`.
- yt-inscritos: `{"status":"ok","data_file_exists":bool,"inscritos":<str|null>,"last_update":<str|null>,"cookies_file_exists":bool,"server_time":"<iso>Z"}` (`last_update` = `atualizado_em`).

Novo `/health` (um só; D8): `{"status":"ok","server_time":"…Z","data_file_exists":bool,"inscritos":str|null,"last_update":str|null}`. Superset do live-stats e do yt-inscritos **sem** `cookies_file_exists` (isso passa a ser informação de admin: `GET /api/admin/workers/status`). `data_file_exists` = existe leitura de inscritos. Sempre 200 enquanto a api responder (nunca exige dados).

## 5. Rotas do legado que mudam de lugar (não são consumidas por OBS/vMix)

| Legado | Novo | Observação |
|---|---|---|
| `GET /` (live-stats: HTML do painel) | `GET /` na porta 8081 | agora exige login |
| `GET /config` | `GET /api/admin/slots` | autenticado |
| `POST /config` `{"slots":[...]}` → `{"ok":true,"slots":[...]}` | `PUT /api/admin/slots` | autenticado + CSRF |
| `GET /admin` (yt-inscritos: HTML) | `GET /` e `GET /admin` na 8081 | autenticado |
| `POST /admin/cookies` (array) | `POST /api/admin/cookies` | autenticado + CSRF; corpo continua sendo o array do EditThisCookie |
| `GET /` (yt-inscritos: JSON de índice) | removido (404 em 8080) | P4 |
