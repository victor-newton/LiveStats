# Atualizar, backup e voltar versão

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · **Manutenção** · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)

**Atualizar.** Baixe a versão nova (`git pull`) e rode de novo no seu computador:

```sh
bash deploy/enviar.sh instalar --host IP --user USUARIO --sem-segredos
```

Isso reconstrói as imagens e recria só o que mudou, mantendo os dados e os segredos. No ZimaOS, termine com `bash deploy/enviar.sh registrar-zimaos --host IP --user USUARIO`. Só o código **commitado** é enviado.

**Backup.** Os dados ficam em dois volumes do Docker, `livestats-data` (conjuntos, lives, histórico, leitura de inscritos e cookies cifrados) e `livestats-admin` (sessões do painel), e os segredos ficam na pasta `secrets/` da instalação (`/DATA/AppData/livestats/secrets`). Para guardar uma cópia, no servidor:

```sh
cd /DATA/AppData/livestats
docker run --rm -v livestats-data:/data:ro -v "$PWD":/backup alpine tar czf /backup/livestats-data.tgz -C /data .
tar czf secrets-backup.tgz secrets
```

Guarde o `secrets-backup.tgz` em lugar protegido: ele tem as chaves dos cookies. Para restaurar, desfaça o `tar` nos mesmos lugares e rode `docker compose up -d`.

**Voltar versão.** Escolha a versão antiga no seu computador (`git checkout NOME-DA-VERSAO`) e rode o `instalar --sem-segredos` de novo. Os dados são mantidos.

**Desligar sem perder nada.** `bash deploy/enviar.sh rollback --host IP --user USUARIO` desliga os serviços e solta o Nginx Proxy Manager da rede do LiveStats, sem apagar os volumes. Antes, no NPM, desative ou aponte para outro destino qualquer *Proxy Host* que use `172.29.80.2` (um destino que não resolve impede o NPM de recarregar). Para religar: `instalar --sem-segredos`. Só apague os dados se tiver certeza (`docker volume rm livestats-data livestats-admin`).

**Trocar a senha do painel.** No servidor, `sh /DATA/AppData/livestats/deploy/install.sh --lan-ip IP --reset-admin-password --skip-build` (rode por `ssh -t`, pois pergunta no terminal) e depois `docker compose up -d --force-recreate api`.

**Renovar o certificado do painel** (vale 825 dias): `instalar --renew-tls --sem-segredos`.

---

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · [OBS e vMix](obs-e-vmix.md) · **Manutenção** · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)
