# Changelog

Todas as mudanças relevantes do LiveStats ficam neste arquivo. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto usa [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [3.2.0] - 2026-11-11

### Adicionado

- **YouTube por canal ou por vídeo**: o link do canal (`youtube.com/@canal` ou `/channel/UC…`, com ou sem `/live`) acompanha sempre a live que estiver ao vivo no canal; o link do vídeo lê aquela live exata. O pop-up Adicionar live mostra qual modo será usado.
- Overlay de espectadores com ícone da plataforma (`&icone=1`) e, no conjunto, soma de uma plataforma só (`&plataforma=youtube`, `twitch` ou `kick`).
- Botão **Icone** ao lado do Copiar nos links de espectadores, e uma linha de espectadores por plataforma em **Links do conjunto**.
- Chat do YouTube agora funciona também com link de canal (`@canal` ou `/channel/UC...`): acompanha a live ativa.

- **Histórico da live inteira**: as estatísticas ficam guardadas pelo tempo todo da live (12 h de live, 12 h no gráfico). Nova faixa "Toda a live" no gráfico do conjunto. Resolução completa nas últimas 24 h e um ponto por minuto depois disso; sessões encerradas ficam por `HISTORY_RETENTION_DAYS` dias (padrão 14).
- **Exportar PNG**: botão ao lado do seletor do gráfico para baixar um período específico (1600x800, no mesmo estilo do gráfico).

### Alterado

- Com todas as lives off (depois de 10 min de folga) o LiveStats para de gravar pontos e o gráfico fica parado, com o aviso "parado desde HH:MM".

## [3.1.0] - 2026-11-11

### Adicionado

- **Chat unificado em overlay**: junta num overlay só o chat das lives do YouTube, da Twitch e do Kick de um conjunto, no estilo dos widgets de chat para OBS, com o logo da plataforma ao lado do nome de quem escreveu e os emotes de cada plataforma. Endereço: `/overlay/chat.html?conjunto={id}` (com `&tema=` opcional para trocar o tema só naquela cena).
- **11 temas**: Cartoon, Adesivo, Pixel, Post-it, Chiclete, Clássico, Balão, Vidro, Minimal, Terminal e Rodapé, com fontes hospedadas no próprio servidor.
- **Painel**: chave "Chat do conjunto" em Configurar conjunto (desligada por padrão; conjunto sem chat não gasta processamento) e cartão "Chat do conjunto" no fim da página do conjunto, para escolher quais lives entram, o tema, o tamanho do texto, quanto tempo cada mensagem fica, o ícone da plataforma, a cor do nome, cargos, comandos, limite de mensagens por segundo e bots ignorados.
- **Serviço `worker-chat`**: lê o chat só enquanto a live está ao vivo e o overlay está aberto; Twitch por conexão anônima, Kick pelo tempo real do próprio site e YouTube pela página do chat ao vivo, sem chave de API. Mensagens apagadas pela moderação somem do overlay; em chat muito rápido, moderadores, VIPs, inscritos e membros passam na frente.

### Segurança

- Nenhuma mensagem de chat é guardada (nem em banco, nem em log): ela passa pela memória do serviço, aparece no overlay e some.
- O `worker-chat` fica numa rede própria com o web e não alcança a api; o overlay do chat libera imagens só dos servidores de emotes das três plataformas.

## [3.0.0] - 2026-11-10

### Adicionado

- **Vários usuários**: o mestre cria usuários (senha provisória com troca obrigatória no primeiro acesso), define limites de conjuntos e lives por usuário, suspende, redefine senha e apaga. Cada usuário vê só os próprios dados e tem o próprio contador de inscritos (`/inscritos/{id}.json`, `/api/public/subscribers/{id}`, `/overlay/contador.html?contador={id}`).
- **Aba Mestre** (usuários, dados de cada usuário, atividade e uso) e **Saúde** com a memória usada por cada serviço; "Limpar meus dados" para todos.
- **Acesso externo ao painel** opcional (`ADMIN_EXTERNAL_ACCESS`) atrás do Nginx Proxy Manager, com IP real do visitante (`ADMIN_REAL_IP_HEADER`) e limite de 5 logins por minuto por pessoa; o web tem IP fixo na rede de borda (`WEB_EDGE_IP`).
- **Contador de inscritos**: escolha do canal a ler (ID ou link do Studio) quando a conta dos cookies tem mais de um canal, estatísticas (ganho, quedas) e cartão "Conectando" enquanto a sessão do Studio é reaberta.
- **Painel novo**: navegação por abas (cabeçalho no desktop, barra inferior no celular), endereços próprios por tela (`/inicio`, `/conjuntos`, `/contador`…), seletor de cor e de fonte próprios, dicas por ícone, animações de abrir e fechar, sem zoom automático ao digitar no celular.

### Alterado

- Senha com no mínimo 8 caracteres (antes 12); argon2id e limite de tentativas mantidos.
- O overlay do contador no endereço do painel passou a `/overlay/contador.html`; no endereço público `/contador` continua igual.

### Corrigido

- Lista de fontes e seletor de cor não apareciam dentro de pop-ups e a rolagem voltava ao topo.
- O painel externo dava erro 429 ao abrir (muitos arquivos de uma vez): limite de rajada ajustado.

## [1.0.0] - 2026-11-04

Primeira versão pública.

### Adicionado

- **Conjuntos de lives** do YouTube, da Twitch e do Kick, lidas das páginas públicas, sem chave de API, em ciclos independentes de 5 a 15 segundos. Cada conjunto tem nome, meta de likes, cor, fonte e cor do texto; cada live tem a sua cor (paleta por plataforma), meta, nome no painel e estado (ao vivo, agendada, encerrada, offline ou erro).
- **Painel web** em português: visão geral, tela do conjunto com gráfico de espectadores e likes (de 5 minutos a 2 horas), rosca de participação por live, links com botão **Copiar** e prévia do overlay, pop-ups de **Novo conjunto**, **Configurar conjunto** e **Adicionar live**, e menu lateral em gaveta no celular.
- **Contador de inscritos** do canal do YouTube, lido do YouTube Studio com cookies colados no painel (cifrados; o canal é descoberto sozinho e a sessão é renovada pelo serviço), com ganho de inscritos dos últimos 7 dias, do dia e da última hora, e aparência (fonte e cor) com prévia.
- **Tela Saúde**: estado de cada serviço, tempo de resposta por minuto na última hora, eventos recentes e a leitura de cada live.
- **Endereços públicos** sem login e com CORS aberto: `/lives/{id}.json`, `/conjuntos/{id}.json`, `/api/public/lives/{id}`, `/api/public/sets/{id}`, `/inscritos.json`, `/api/public/subscribers`, `/health`, e os overlays `/overlay/live.html` (likes e meta ou espectadores, de uma live ou de um conjunto) e `/contador`, prontos para OBS e vMix.
- **Instalação por SSH** com `deploy/enviar.sh` (`instalar`, `preflight`, `status`, `conectar-npm`, `registrar-zimaos`, `rollback`): gera chaves, hash da senha e certificado HTTPS do painel, constrói as imagens e sobe os serviços; integração opcional com Nginx Proxy Manager e Cloudflare Tunnel; cartão na tela inicial do ZimaOS.
- **Segurança**: painel só na porta 8081, com HTTPS próprio e lista de redes permitidas; senha com hash argon2id, sessões com CSRF e limite de tentativas; segredos como Docker secrets; containers sem privilégios, com sistema de arquivos somente leitura; limite de requisições nos endereços públicos; política de segurança de conteúdo (CSP) restritiva.
