# Política de segurança

## Como reportar uma vulnerabilidade

Não abra issue pública com detalhes de vulnerabilidade. Use o aviso privado do GitHub: na aba **Security** do repositório, clique em **Report a vulnerability**.

No relato, inclua:

- o que foi encontrado e onde (arquivo, rota, serviço do compose);
- os passos para reproduzir;
- o impacto que você imagina.

Não inclua segredos reais (chaves, cookies, senhas, IDs de canal privados) no relato. Se precisar mostrar uma evidência, use um valor sentinela.

## Escopo

Dentro do escopo: o código deste repositório, as imagens Docker geradas por ele, a configuração do Docker Compose e do Nginx, e os contratos em `docs/`.

Fora do escopo: serviços de terceiros (YouTube, Google Cloud, Cloudflare, Tailscale, Nginx Proxy Manager) e a configuração de infraestrutura que não está neste repositório.

## Versões suportadas

Só a branch `main` recebe correções.

## Segredos vazados

Se uma chave ou cookie vazou:

1. Revogue ou rotacione o segredo no serviço de origem (por exemplo, a chave da YouTube Data API no Google Cloud).
2. Gere novos arquivos em `secrets/` e reinicie os serviços afetados.
3. Remova o valor do histórico do Git antes de publicar o repositório.

## Modelo de ameaças e controles

O modelo completo está em `docs/MAPA-DE-SEGURANCA.md` e `docs/ARCHITECTURE.md`. Em resumo:

- Cada segredo existe em um único container, montado como Docker secret (nunca como variável de ambiente).
- O admin não é publicado na internet: a porta 8081 só fica acessível pela LAN ou pelo Tailscale.
- Login com argon2, sessão em cookie `HttpOnly; Secure; SameSite=Strict`, CSRF em todo método que altera estado e limite de tentativas em duas camadas.
- Cookies do Studio são cifrados com a chave pública na `api`; só o `worker-inscritos` tem a chave privada.
- Containers rodam como usuário não-root, com sistema de arquivos somente leitura e sem capabilities Linux.
- Gitleaks e Trivy rodam no pre-commit e no CI.
