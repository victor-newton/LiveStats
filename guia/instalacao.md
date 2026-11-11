# Instalação

[Início](../README.md) · **Instalação** · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)

Neste guia: [Requisitos](#requisitos) · [Instalação](#instalação) · [Passo a passo](#passo-a-passo) · [Outros comandos](#outros-comandos-do-enviarsh) · [Publicar na internet](#publicar-na-internet-opcional)

## Requisitos

- **Servidor** com **ZimaOS** ou **Linux** com Docker (versão recente) e o plugin `docker compose` (v2). Reserve pelo menos **5 GB livres** para construir as imagens (o Chromium faz parte delas).
- **Rede:** o servidor precisa de saída HTTPS para `youtube.com`, `twitch.tv` e `kick.com`. As portas **8080** (pública) e **8081** (painel) ficam livres no servidor.
- **Seu computador** com `bash`, `git` e `ssh` (no Windows, use o Git Bash) para rodar o instalador, que envia o código ao servidor por SSH.
- **Opcional:** [Nginx Proxy Manager](https://nginxproxymanager.com/) e [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) para publicar os endereços na internet com seu próprio domínio. Dentro da rede local não precisa de nada disso.
- **Para o contador de inscritos:** acesso ao YouTube Studio do canal e uma extensão de navegador que exporte cookies em JSON, como o EditThisCookie.

## Instalação

A instalação é feita do seu computador, com um único comando. Baixe o código e, na pasta dele, rode o instalador:

```sh
git clone https://github.com/victor-newton/LiveStats.git
cd LiveStats
bash deploy/enviar.sh instalar --host 192.168.0.10 --user meu-usuario-ssh
```

Troque `192.168.0.10` pelo IP do servidor e `meu-usuario-ssh` pelo usuário com que você entra nele por SSH. Se preferir, defina as variáveis `LIVESTATS_HOST` e `LIVESTATS_USER` e omita `--host` e `--user`.

### Passo a passo

1. **Usuário com acesso ao Docker.** O usuário do SSH precisa falar com o Docker. Se o instalador reclamar disso, rode uma vez no servidor `sudo usermod -aG docker SEU-USUARIO` e entre de novo.
2. **(Opcional) Preflight.** `bash deploy/enviar.sh preflight --host IP --user USUARIO` só faz leitura: confere Docker, espaço em disco, portas 8080/8081 livres e se há Nginx Proxy Manager ou cloudflared no servidor. O próprio `instalar` já roda essa checagem antes de mudar qualquer coisa.
3. **Instalar.** Rode o comando acima. Ele pergunta, sem mostrar na tela:
   - a **senha do painel** (mínimo de 8 caracteres, digitada duas vezes). Ela nunca é gravada: o servidor guarda só o hash;
   - a **senha do SSH** do servidor (uma única vez).

   O instalador então cria a pasta `/DATA/AppData/livestats` no servidor, escreve o `.env`, constrói as imagens (a primeira vez leva alguns minutos), gera os segredos (chaves dos cookies, hash da senha e certificado HTTPS autoassinado do painel) e sobe os quatro serviços, esperando todos ficarem saudáveis. No fim ele testa os endereços e mostra o endereço do painel.
4. **Conferir.** `bash deploy/enviar.sh status --host IP --user USUARIO` mostra os serviços e os últimos registros.


<details>
<summary>Opções do <code>instalar</code> (tabela completa)</summary>

Opções do `instalar`:

| Opção | Para quê |
|---|---|
| `--host IP` / `--user NOME` | Servidor e usuário do SSH (obrigatórios, ou use as variáveis `LIVESTATS_HOST` e `LIVESTATS_USER`) |
| `--admin-user NOME` | Usuário do painel (padrão `admin`) |
| `--admin-allow "REDES"` | Quem pode abrir o painel na porta 8081, em IPv4/CIDR separados por espaço (padrão `192.168.0.0/16 100.64.0.0/10`: rede local e Tailscale) |
| `--public-base-url URL` | Endereço público usado nos links que o painel mostra e copia (ex.: `https://live.exemplo.com`) |
| `--admin-externo` | Liga o acesso ao painel de fora da rede (veja [Acessar o painel de fora da rede](acesso-externo.md)). Exige `--admin-trusted-proxy` (ou o IP já gravado pelo `conectar-npm`); sem ele o instalador recusa. `--admin-interno` desliga |
| `--admin-trusted-proxy IP` | IP fixo do Nginx Proxy Manager na rede do LiveStats (ex.: `172.29.80.11`) |
| `--public-admin-url URL` | Endereço externo do painel, mostrado na tela Conta (ex.: `https://painel.exemplo.com`) |
| `--master-allow "REDES"` | Só estas redes (IPv4/CIDR) podem entrar como mestre e usar as telas do mestre. Vazio = sem filtro |
| `--extra-san IP` | Inclui outro IP (por exemplo o do Tailscale) no certificado do painel; pode repetir |
| `--conectar-npm [NOME]` | Já liga o Nginx Proxy Manager à rede do LiveStats (veja a opção A abaixo) |
| `--sem-segredos` | Reinstalar ou atualizar sem pedir a senha de novo (os segredos já existem) |
| `--renew-tls` | Gera um certificado novo para o painel |
| `--skip-build` | Não reconstrói as imagens |

</details>

Exemplo completo:

```sh
bash deploy/enviar.sh instalar --host 192.168.0.10 --user meu-usuario-ssh \
  --admin-user meu-usuario --public-base-url https://live.exemplo.com
```

### Outros comandos do `enviar.sh`

| Comando | O que faz |
|---|---|
| `instalar` | Instala ou atualiza (veja acima) |
| `registrar-zimaos` | No **ZimaOS**, faz o LiveStats aparecer como cartão na tela inicial. Pede a senha do SSH e a do `sudo`. Rode depois do `instalar` e de cada atualização. `--remover` desfaz |
| `status` | Mostra os serviços, o batimento dos workers e os últimos registros (sem segredos) |
| `preflight` | Checagem do servidor, só leitura |
| `conectar-npm` | Liga o Nginx Proxy Manager à rede do LiveStats (opção A, abaixo) |
| `rollback` | Desliga o LiveStats **sem apagar os dados** (pede para digitar `sim`) |

### Publicar na internet (opcional)

Para usar os endereços fora da sua rede, aponte o Nginx Proxy Manager (que o Cloudflare Tunnel já alcança) para o LiveStats. Há dois jeitos:

- **Opção B, a mais simples:** no Nginx Proxy Manager, crie um *Proxy Host* para o seu domínio com destino `http://IP-DO-SERVIDOR:8080` e SSL habilitado. Não mexe em nada no container do NPM. O limite de requisições por visitante passa a contar o IP do NPM.
- **Opção A:** rode `bash deploy/enviar.sh conectar-npm --host IP --user USUARIO` (ou use `--conectar-npm` no `instalar`). O NPM entra na rede do LiveStats com IP fixo e o *Proxy Host* aponta para o IP fixo do web, **`172.29.80.2`**, na porta `8080` (**não** use o nome `livestats-web`: o Nginx do NPM não resolve nomes do Docker e responde 502). Cada visitante é contado pelo IP real (via `CF-Connecting-IP`). Reinicie o container do NPM uma vez depois de conectá-lo. Se o ZimaOS recriar o container do NPM, rode `conectar-npm` de novo.

Em qualquer das duas, **nunca** publique a porta 8081 (o painel) na internet: ela fica só na rede local ou na VPN.

---

[Início](../README.md) · **Instalação** · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)
