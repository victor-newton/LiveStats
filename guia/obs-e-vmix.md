# Usar no OBS e no vMix

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · **OBS e vMix** · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)

**Overlays no OBS**

1. Em **Fontes**, clique em **+** e escolha **Navegador**.
2. Em **URL**, cole o link do overlay copiado do painel (por exemplo `http://IP-DO-SERVIDOR:8080/overlay/live.html?conjunto=1&metrica=views`).
3. Ajuste largura e altura (por exemplo 800 x 200). O fundo já é transparente. Para o overlay do chat, algo como 400 x 800 (o tema Rodapé fica melhor largo e baixo, por exemplo 1920 x 120).

**Overlays no vMix**

1. **Add Input**, aba **Web Browser**.
2. Cole o link do overlay e defina a largura e a altura. Use a entrada numa camada (*layer*) sobre o vídeo para o fundo transparente aparecer.

**Dados em artes do vMix (GT Title) e do OBS**

Use `/lives/{id}.json`, `/conjuntos/{id}.json` ou `/inscritos.json` como **Data Source** (no vMix: *Data Sources*, tipo JSON, com atualização a cada poucos segundos). Os nomes dos campos são os do exemplo acima.

Dica: dentro da rede local use o IP e a porta 8080, que não dependem de internet, do túnel nem do domínio.

---

[Início](../README.md) · [Instalação](instalacao.md) · [Primeiros passos](primeiros-passos.md) · [Painel](painel.md) · [Usuários](usuarios.md) · [Acesso externo](acesso-externo.md) · [Endereços públicos](enderecos-publicos.md) · **OBS e vMix** · [Manutenção](manutencao.md) · [Problemas comuns](problemas-comuns.md) · [Segurança](seguranca.md)
