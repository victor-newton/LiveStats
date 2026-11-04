# Changelog

Todas as mudanças relevantes do LiveStats ficam neste arquivo. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto usa [Versionamento Semântico](https://semver.org/lang/pt-BR/).

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
