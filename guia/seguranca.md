# Segurança

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · **Segurança**

- O painel só responde na porta 8081, com HTTPS próprio, e só para as redes da lista `--admin-allow`. Nunca publique a porta 8081 diretamente na internet; para acessar de fora use o Nginx Proxy Manager, como descrito em [Acessar o painel de fora da rede](acesso-externo.md) (que exige senha forte e um proxy confiável).
- Só o mestre cria contas; cada usuário enxerga apenas os próprios dados, e o login do mestre pode ser restrito a certas redes (`MASTER_ALLOW_CIDRS`).
- A senha do painel nunca é gravada, só o hash (argon2id). O login limita tentativas e protege as ações com token CSRF.
- Os cookies do YouTube Studio são cifrados com uma chave pública; só o serviço do contador tem a chave privada. Trate esses cookies como uma senha da sua conta.
- Os segredos ficam em arquivos na pasta `secrets/` do servidor, montados como Docker secrets só no serviço que precisa, e nunca em variáveis de ambiente nem no Git.
- Os containers rodam sem privilégios (usuário comum, sistema de arquivos somente leitura, sem capacidades extras), e a API não tem acesso à internet.

---

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · **Segurança**
