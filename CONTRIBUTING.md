# Como contribuir com o LiveStats

## Antes de começar

Leia, nesta ordem:

1. `docs/PLANO.md`: o que é a aplicação e como o trabalho é dividido.
2. `docs/repo-layout.md`: cada arquivo tem um dono. Não edite arquivo de outra tarefa sem combinar antes.
3. `docs/ARCHITECTURE.md` e `docs/test-plan.md`: contrato, decisões e testes obrigatórios.

Os documentos em `docs/`, `db/`, `tests/contract/` e `docker-compose.yml` são o contrato. Mudar contrato é uma decisão de projeto, não uma edição "por conveniência".

## Ambiente

- Python 3.12 e Docker (com Docker Compose v2).
- Dependências de desenvolvimento:

  ```sh
  python -m pip install -r requirements-dev.txt
  ```

- Hooks de pre-commit (gitleaks, ruff e verificações de arquivo):

  ```sh
  pre-commit install
  ```

- Fins de linha são LF (`.gitattributes`, `.editorconfig`). Os golden files em `tests/contract/legacy/` têm bytes exatos e não podem ser reformatados.

## Regras de qualidade

As regras anti-burla do `docs/test-plan.md` valem para todo código e teste. Em resumo, é proibido:

1. Alterar, apagar ou enfraquecer um teste para ele passar.
2. Usar `skip`, `xfail`, `pytest.mark.skip`, `test.skip` ou `# pragma: no cover` em código novo. Teste que falta ambiente **falha**, não é pulado.
3. Criar caminho especial para teste no código de produção (`if TESTING`, `DISABLE_AUTH`, variável que desliga login, CSRF ou limite).
4. Simular a própria unidade testada. Só a fronteira externa pode ser simulada (HTTP do YouTube e relógio), e com respostas reais gravadas.
5. Colocar respostas fixas no código para casar com o teste.
6. Capturar exceção e engolir para o teste ficar verde.
7. Declarar que algo passou sem colar a saída real do comando.

Se uma funcionalidade não puder ser implementada direito, pare e relate. Não contorne.

`pytest.ini`, `pyproject.toml`, `.gitleaks.toml` e `.github/workflows/` controlam o que é verificado. Mudanças neles precisam de revisão própria, porque podem enfraquecer a suíte sem que ninguém perceba.

## Comandos de teste

Rode da raiz do repositório:

| Suíte | Comando |
|---|---|
| Lint | `ruff check .` |
| API | `python -m pytest tests/api` |
| Worker de lives | `python -m pytest tests/worker_lives` |
| Worker de inscritos | `python -m pytest tests/worker_inscritos` (requer `python -m playwright install chromium`) |
| Front | `python -m pytest tests/web` (requer Playwright) |
| Infra, estática | `python -m pytest tests/infra -m "not docker"` |
| Infra, com Docker | `python -m pytest tests/infra -m docker` |
| Ponta a ponta e segurança | `python -m pytest tests/e2e tests/security` (requer a pilha no compose) |

O CI roda ruff, as suítes por pasta, gitleaks e Trivy (repositório e imagens). Um PR só é aceito com o CI verde.

## Commits e pull requests

- Mensagens de commit em português, no imperativo ou no presente, com a primeira linha curta.
- Faça `git add` dos arquivos que você mudou de propósito. Nunca use `git add .` sem olhar o resultado.
- Nunca faça commit de `.env`, de `secrets/*.txt`, de chaves (`*.key`, `*.pem`) ou de bancos (`*.db`).
- Não reescreva histórico de branches compartilhados.
- Descreva no PR o que mudou, o que foi testado (com a saída real do comando) e o que não foi testado.

## Segredos

Segredos ficam só em `secrets/*.txt` (fora do Git) e são montados como Docker secrets. Veja [`secrets/README.md`](secrets/README.md). Se um segredo aparecer num commit, num log ou num chat, trate-o como vazado: revogue-o primeiro e gere outro.
