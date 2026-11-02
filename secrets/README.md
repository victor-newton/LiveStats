# Segredos do LiveStats

Os segredos são arquivos de texto nesta pasta. Eles são montados pelo Docker Compose como Docker secrets, em `/run/secrets/<nome>`, e só no container indicado. Nunca são variáveis de ambiente.

**Nenhum arquivo `*.txt` desta pasta vai para o Git.** O `.gitignore` ignora `secrets/*` e deixa só este `README.md` rastreado.

## Os quatro arquivos

| Arquivo | Container | Conteúdo |
|---|---|---|
| `youtube_api_key.txt` | `worker-lives` | Chave da YouTube Data API v3, criada no Google Cloud e restrita à API |
| `cookie_private_key.txt` | `worker-inscritos` | Chave privada do par de cookies (Curve25519, 32 bytes em base64) |
| `cookie_public_key.txt` | `api` | Chave pública do mesmo par (32 bytes em base64). A `api` só cifra, nunca decifra |
| `admin_password_hash.txt` | `api` | Hash argon2id (formato PHC) da senha do admin. A senha em si não é guardada |

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

Os valores que as ferramentas imprimem são segredos. Não os deixe no histórico do shell de forma compartilhada nem os cole em lugar nenhum fora desses arquivos.

Depois de trocar um segredo, reinicie os serviços que o usam: `docker compose up -d --force-recreate api` (ou o worker correspondente).

## Permissões

- Pasta `secrets/`: somente o seu usuário deve ter acesso (`chmod 700` no Linux ou macOS; no Windows, restrinja pelas propriedades da pasta).
- Arquivos `*.txt`: precisam ser legíveis pelo usuário do container (uid 10001). Por isso o padrão é `chmod 644` no Linux ou macOS (decisão D22 em `docs/ARCHITECTURE.md`). Os containers não têm permissão de escrita nesses arquivos.

## Regras

- Nunca escreva um valor real de segredo em `.env`, em `docker-compose.yml`, em `environment:`, em `build args`, em `Dockerfile`, em logs ou em respostas HTTP.
- Se um segredo vazar, revogue ou rotacione no serviço de origem antes de qualquer outra coisa. Depois gere um novo arquivo e reinicie os serviços.
- Confira que nada de sensível foi rastreado:

  ```sh
  git ls-files secrets
  ```

  A saída deve ser somente `secrets/README.md`.
