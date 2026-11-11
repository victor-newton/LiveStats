# LiveStats

Estatísticas de lives do **YouTube**, da **Twitch** e do **Kick** reunidas em conjuntos, com JSON e overlays prontos para o **OBS** e o **vMix**, chat unificado e contador de inscritos. Tudo é administrado por um painel web protegido por senha e roda em Docker no seu próprio servidor.

![Visão geral do painel](docs/imagens/visao-geral.png)

## Recursos

- **Conjuntos de lives:** agrupe lives de qualquer plataforma, some os espectadores e acompanhe os likes contra uma meta.
- **Sem chave de API:** as lives são lidas das páginas públicas, a cada 5 a 15 segundos.
- **JSON e overlays:** endereços prontos de likes, meta e espectadores, com a cor e a fonte que você escolher.
- **Chat unificado:** o chat das lives do YouTube, da Twitch e do Kick num overlay só, com 11 temas. Nenhuma mensagem é guardada.
- **Contador de inscritos:** o número de inscritos do seu canal do YouTube, em JSON e num overlay animado.
- **Vários usuários:** o mestre cria as contas; cada pessoa vê só o que é seu.
- **Painel de qualquer lugar (opcional):** pela rede local, pela VPN ou pela internet atrás do Nginx Proxy Manager.
- **Seguro por padrão:** HTTPS, senha com hash, cookies cifrados e containers sem privilégios.

## Início rápido

1. **Requisitos:** um servidor com Docker (ZimaOS ou Linux, com pelo menos 5 GB livres) e, no seu computador, `bash`, `git` e `ssh` (no Windows, o Git Bash).
2. **Instale** do seu computador, com um único comando (troque o IP e o usuário do SSH):

   ```sh
   git clone https://github.com/victor-newton/LiveStats.git
   cd LiveStats
   bash deploy/enviar.sh instalar --host 192.168.0.10 --user meu-usuario-ssh
   ```

3. **Abra o painel** em `https://IP-DO-SERVIDOR:8081` e entre com a senha que você digitou na instalação (o navegador avisa do certificado autoassinado: continue uma vez).
4. **Crie um conjunto**, adicione as lives e copie o link do overlay para o OBS. O passo a passo está em [Primeiros passos](guia/primeiros-passos.md).

## Guias

| Quero... | Guia |
|---|---|
| Instalar no meu servidor | [Instalação](guia/instalacao.md) |
| Fazer o primeiro acesso e colocar uma live no OBS | [Primeiros passos](guia/primeiros-passos.md) |
| Conhecer cada tela do painel | [Telas do painel](guia/painel.md) |
| Criar contas para outras pessoas | [Usuários e painel mestre](guia/usuarios.md) |
| Abrir o painel de fora da rede | [Acesso externo](guia/acesso-externo.md) |
| Usar os endereços JSON e overlays | [Endereços públicos](guia/enderecos-publicos.md) |
| Colocar os overlays no OBS ou no vMix | [OBS e vMix](guia/obs-e-vmix.md) |
| Atualizar, fazer backup ou voltar versão | [Manutenção](guia/manutencao.md) |
| Resolver um erro | [Problemas comuns](guia/problemas-comuns.md) |
| Entender como o LiveStats se protege | [Segurança](guia/seguranca.md) |

---

Histórico de versões em [CHANGELOG.md](CHANGELOG.md).
