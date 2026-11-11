# Acessar o painel de fora da rede

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · **Acesso externo** · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)

Por padrão o painel só abre na rede local ou na VPN. Para abri-lo de qualquer lugar, em `https://painel.seudominio.com`, use o mesmo Nginx Proxy Manager (NPM) e o mesmo túnel do Cloudflare que já publicam os overlays. O painel continua protegido por HTTPS, senha, token CSRF e limite de tentativas; o que muda é que a lista de IPs deixa de valer.

1. **Ligue o NPM à rede do LiveStats e ligue o acesso externo.** Um comando só (o IP fixo do NPM aqui é um exemplo):

   ```sh
   bash deploy/enviar.sh instalar --host 192.168.0.10 --user meu-usuario-ssh --sem-segredos --skip-build \
     --admin-externo --admin-trusted-proxy 172.29.80.11 --public-admin-url https://painel.seudominio.com --conectar-npm
   ```

   Sem `--admin-trusted-proxy` (ou um IP já gravado por `conectar-npm`) o instalador **recusa** a opção, e o servidor do painel também não sobe: sem saber qual é o proxy, o LiveStats não conhece o IP real de quem entra e o limite de tentativas valeria para todos juntos. Se o ZimaOS recriar o container do NPM, rode `bash deploy/enviar.sh conectar-npm --host IP --user USUARIO` de novo.
2. **Crie o *Proxy Host* no NPM.** O `conectar-npm` imprime estes passos com os seus valores reais:
   - *Domain Names:* `painel.seudominio.com`;
   - *Scheme:* **https**; *Forward Hostname / IP:* **`172.29.80.2`** (o IP fixo do web na rede do LiveStats, variável `WEB_EDGE_IP`; **não use o nome `livestats-web`**: o Nginx do NPM não resolve nomes do Docker e responde 502; também **não use o IP do servidor** (`192.168.x.x`), pois assim a conexão não vem do NPM e o limite de logins vira um só para todos); *Forward Port:* **8081**;
   - o certificado interno do painel é autoassinado e o NPM não o valida, então não há nada a configurar nesse ponto;
   - *Websockets Support:* não é necessário;
   - aba **SSL:** escolha o certificado do domínio (ou emita um com a Let’s Encrypt) e ligue *Force SSL*;
   - cabeçalhos: o NPM já envia `X-Forwarded-For`; não o desligue. O painel só confia no cabeçalho do IP quando a conexão vem do IP fixo do NPM. Pelo túnel do Cloudflare o último salto de `X-Forwarded-For` é só o do túnel; use então `--admin-real-ip-header CF-Connecting-IP` (variável `ADMIN_REAL_IP_HEADER`; aceita `X-Forwarded-For`, o padrão, `CF-Connecting-IP` ou `X-Real-IP`; outro valor impede o painel de subir), para o limite de login e o bloqueio por tentativas valerem por visitante.
3. **Túnel do Cloudflare:** em *Zero Trust › Tunnels*, no mesmo túnel do site, adicione o *Public Hostname* `painel.seudominio.com` com o mesmo serviço do hostname do site (o NPM). Para o limite de login valer por pessoa (e não pelo túnel inteiro), na aba **Advanced** do *Proxy Host* cole `real_ip_header CF-Connecting-IP;` e `set_real_ip_from IP-DO-CLOUDFLARED;` (o `conectar-npm` imprime o IP quando encontra o container).
4. **Confira:** `bash deploy/enviar.sh status --host IP --user USUARIO` mostra se o acesso externo está ligado e avisa se falta o IP do proxy.

Cuidados ao abrir o painel para a internet:

- **Use uma senha forte e única** (8 caracteres no mínimo, mas prefira uma frase longa). É ela que protege o painel agora, não a lista de IPs.
- **Só o mestre cria usuários:** não há cadastro aberto. Dê senhas provisórias por um canal seguro e apague ou suspenda contas que não usa mais.
- **Limite do mestre:** defina `MASTER_ALLOW_CIDRS` (por exemplo só a sua rede e a VPN) para que o login do mestre e as telas do mestre não funcionem de fora, mesmo com o painel aberto.
- O login tem limite de **5 tentativas por minuto por IP**, e o bloqueio por tentativas erradas vale por IP e por usuário.
- Para voltar ao modo só-rede-local: `instalar --admin-interno --sem-segredos --skip-build` (a lista `--admin-allow` volta a valer).

---

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · **Acesso externo** · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)
