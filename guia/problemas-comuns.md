# Problemas comuns

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · **Problemas comuns** · [Segurança](seguranca.md)

| Sintoma | O que fazer |
|---|---|
| O preflight diz que não consegue falar com o Docker | O usuário do SSH não está no grupo `docker`. No servidor: `sudo usermod -aG docker USUARIO` e entre de novo |
| "Plugin 'docker compose' (v2) não encontrado" com `permission denied` em `.docker/config.json` | Comum no ZimaOS. Os scripts já contornam; para comandos manuais, use `export DOCKER_CONFIG=$(mktemp -d)` antes do `docker compose` |
| O painel dá **403** na porta 8081 | O IP do seu computador não está em `--admin-allow` (variável `ADMIN_ALLOW_CIDRS` do `.env`). Ajuste e rode `docker compose up -d web` na pasta de instalação |
| O navegador não confia no certificado do painel | É esperado (autoassinado). Aceite uma vez. Se o IP do servidor mudou, rode `instalar --renew-tls --sem-segredos` com o IP novo |
| O `web` não sobe e o registro fala em certificado ou chave | Falta ou há permissão errada em `secrets/admin_tls_*.pem` (devem ser `644`). Rode `instalar --renew-tls --sem-segredos` |
| O contador lê o canal errado (por exemplo o pessoal, e mostra "Contador nao encontrado na pagina") | Em **Contador**, preencha **Canal para ler** com o link do Studio ou o ID do canal certo e salve |
| O contador de inscritos não atualiza e a tela mostra aviso de cookies | A sessão do Studio venceu ou os cookies são de outra conta. Exporte os cookies de novo, logado no canal certo, e cole na aba **Contador** |
| A live fica "aguardando" ou "erro" | Confira o link (deve ser da transmissão ao vivo, não do canal) e a tela **Saúde**: ela mostra o motivo e o tempo de resposta. O Kick às vezes exige o navegador interno (`KICK_BROWSER=1`, o padrão) |
| O endereço público dá **502** logo depois de `conectar-npm` | O NPM guardou o DNS de antes de entrar na rede do LiveStats. Reinicie o container do NPM (`docker restart nginxproxymanager`) |
| O endereço público dá **429** com muitos visitantes (opção B) | Todos dividem o limite do IP do NPM. Passe para a opção A |
| O NPM não recarrega depois do `rollback` | Algum *Proxy Host* ainda aponta para o IP do LiveStats (`172.29.80.2`). Desative-o ou mude o destino |
| No ZimaOS o LiveStats funciona mas não aparece na tela inicial | Rode `bash deploy/enviar.sh registrar-zimaos --host IP --user USUARIO` |
| O `instalar` parou dizendo para rodar o `registrar-zimaos` | Com o app registrado no painel do ZimaOS, o código e as imagens são atualizados e a recriação dos containers é feita por esse comando |

Para ver o que está acontecendo: `bash deploy/enviar.sh status --host IP --user USUARIO`.

---

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · **Problemas comuns** · [Segurança](seguranca.md)
