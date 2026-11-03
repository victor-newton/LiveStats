# Segredos do LiveStats

Os segredos são arquivos de texto nesta pasta. Eles são montados pelo Docker Compose como Docker secrets, em `/run/secrets/<nome>`, e só no container indicado. Nunca são variáveis de ambiente.

**Nenhum arquivo desta pasta (exceto este README) vai para o Git.** O `.gitignore` ignora `secrets/*` e deixa só este `README.md` rastreado.

## Os seis arquivos

| Arquivo | Container | Conteúdo |
|---|---|---|
| `youtube_api_key.txt` | `worker-lives` | Chave da YouTube Data API v3, criada no Google Cloud e restrita à API |
| `cookie_private_key.txt` | `worker-inscritos` (somente) | Chave privada do par de cookies (Curve25519, 32 bytes em base64) |
| `cookie_public_key.txt` | `api` e `worker-inscritos` | Chave pública do mesmo par (32 bytes em base64). A `api` só cifra, nunca decifra; o `worker-inscritos` (D37) a usa só para cifrar a sessão renovada do Studio (já tem a privada, então não ganha nenhum poder novo) |
| `admin_password_hash.txt` | `api` | Hash argon2id (formato PHC) da senha do admin. A senha em si não é guardada |
| `admin_tls_cert.pem` | `web` | Certificado TLS autoassinado da porta 8081 (admin, D33), com o IP no SAN |
| `admin_tls_key.pem` | `web` | Chave privada do certificado acima |

## Como gerar

Antes, crie o `.env` a partir do modelo (`cp .env.example .env`) e preencha `ADMIN_USERNAME` e `YOUTUBE_CHANNEL_ID`. O Compose exige essas variáveis mesmo quando só uma ferramenta é executada.

1. **Chave da YouTube:** copie a chave para `secrets/youtube_api_key.txt`. Não a cole em chat, issue ou commit.

2. **Par de chaves dos cookies:** gere o par com a ferramenta da própria imagem da `api`:

   ```sh
   docker compose run --rm --no-deps -T api python -m tools.gen_cookie_keys
   ```

   Copie a chave pública para `cookie_public_key.txt` e a privada para `cookie_private_key.txt`. Cada arquivo deve conter só o valor, sem linhas extras.

3. **Senha do admin:** gere o hash argon2 com a ferramenta da `api`. Ela pede a senha no terminal, por isso não use `-T`:

   ```sh
   docker compose run --rm --no-deps api python -m tools.hash_password
   ```

   Copie só o hash (começa com `$argon2id$`) para `admin_password_hash.txt`.

4. **Certificado TLS do admin (D33):** o admin fica em `https://<IP-da-LAN>:8081`, com certificado autoassinado. Gere o par **no servidor**, com o IP da LAN (e, se quiser, o do Tailscale) no SAN. O navegador pedirá para confiar no certificado uma vez; o IP no SAN evita o erro de nome.

   ```sh
   openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=livestats-admin"      -addext "subjectAltName=IP:192.168.15.10,IP:127.0.0.1"      -keyout secrets/admin_tls_key.pem -out secrets/admin_tls_cert.pem
   chmod 644 secrets/admin_tls_cert.pem secrets/admin_tls_key.pem
   ```

   Troque `192.168.15.10` pelo IP real (repita `IP:...` para mais de um). Sem esses dois arquivos o `web` **não sobe** (o log diz qual falta); ele nunca cai para HTTP. Para renovar, gere um par novo e rode `docker compose up -d --force-recreate web`.

Os valores que as ferramentas imprimem são segredos. Não os deixe no histórico do shell de forma compartilhada nem os cole em lugar nenhum fora desses arquivos.

Depois de trocar um segredo, reinicie os serviços que o usam: `docker compose up -d --force-recreate api` (ou o worker correspondente).

## Permissões

- Pasta `secrets/`: somente o seu usuário deve ter acesso (`chmod 700` no Linux ou macOS; no Windows, restrinja pelas propriedades da pasta).
- Arquivos `*.txt`: precisam ser legíveis pelo usuário do container (uid 10001). Por isso o padrão é `chmod 644` no Linux ou macOS (decisão D22 em `docs/ARCHITECTURE.md`). Os containers não têm permissão de escrita nesses arquivos.
- Arquivos `*.pem` (TLS): o Nginx do `web` roda como uid `101` e precisa **ler** os dois. Com secrets de arquivo do Compose o arquivo é montado com o dono e o modo do host, então use `chmod 644` nos dois (inclusive a chave): como a pasta `secrets/` é `700`, só o seu usuário chega a eles no host, e a chave é montada só no `web`. Com `640` e dono diferente de `101` o Nginx não leria a chave e o `web` não subiria (o log aponta a permissão).

## Lista de IPs do admin

`ADMIN_ALLOW_CIDRS` (no `.env`) lista, separados por espaço, os IPv4 ou IPv4/N (N de 8 a 32) que podem usar a 8081; os demais recebem 403. Padrão `127.0.0.1/32`. No deploy: `192.168.15.0/24 100.64.0.0/10`. Valores perigosos (`0.0.0.0/0`, texto, IPv6, bits de host preenchidos) impedem a subida do `web`.

## Regras

- Nunca escreva um valor real de segredo em `.env`, em `docker-compose.yml`, em `environment:`, em `build args`, em `Dockerfile`, em logs ou em respostas HTTP.
- Se um segredo vazar, revogue ou rotacione no serviço de origem antes de qualquer outra coisa. Depois gere um novo arquivo e reinicie os serviços.
- Confira que nada de sensível foi rastreado:

  ```sh
  git ls-files secrets
  ```

  A saída deve ser somente `secrets/README.md`.
