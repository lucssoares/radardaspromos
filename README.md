# Instagram Auto-Follow Bot

Aplicativo desktop com interface gráfica para seguir automaticamente os seguidores de um perfil no Instagram, usando **Playwright** para simular interações reais no navegador.

## Aviso Legal

Este projeto é **apenas para fins educacionais**. O uso de automação viola os [Termos de Serviço do Instagram](https://help.instagram.com/581066165581870). Use por sua conta e risco.

## Funcionalidades

- **Interface gráfica** moderna com CustomTkinter (tema escuro)
- **Login manual seguro**: o app abre o Instagram no navegador, você faz login normalmente (sem salvar senha)
- **Filtro inteligente**: só segue perfis onde `seguindo > seguidores` (maior chance de follow-back)
- **Delays aleatórios** configuráveis para simular comportamento humano
- **Limite de follows** por sessão
- **Barra de progresso** e log em tempo real
- **Botão de parar** para interromper a qualquer momento

## Como funciona

1. Você clica em **"Fazer Login no Instagram"** — o app abre o navegador
2. Você faz login manualmente (pode usar 2FA, sem problemas)
3. Depois de logado, define o **perfil alvo** e clica em **"Iniciar Follow"**
4. O bot navega até o perfil alvo, abre a lista de seguidores e:
   - Para cada seguidor, verifica se `seguindo > seguidores`
   - Se sim, segue o perfil (maior chance de follow-back)
   - Se não, pula para o próximo

## Pré-requisitos

- Python 3.9+
- pip

## Instalação

```bash
# 1. Clone o repositório
git clone <url-do-repo>
cd instagram-auto-follow

# 2. Crie um ambiente virtual (recomendado)
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Instale o navegador Chromium do Playwright
playwright install chromium
```

## Uso

### Interface gráfica (recomendado)

```bash
python app.py
```

### Linha de comando (versão simples)

Configure o arquivo `.env` (copie de `.env.example`) e execute:

```bash
python instagram_follow.py
```

## Gerar executável (.exe / binário)

```bash
pyinstaller --onefile --windowed --name "InstaFollowBot" app.py
```

O executável será gerado na pasta `dist/`.

## Configurações

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| Perfil alvo | — | Perfil de quem pegar os seguidores |
| Máx. follows | 20 | Limite de follows por sessão |
| Delay mínimo | 3s | Tempo mínimo entre ações |
| Delay máximo | 8s | Tempo máximo entre ações |

## Estrutura do Projeto

```
instagram-auto-follow/
├── app.py                # Aplicativo GUI (CustomTkinter)
├── bot.py                # Lógica do bot (Playwright)
├── instagram_follow.py   # Versão CLI (linha de comando)
├── requirements.txt      # Dependências Python
├── .env.example          # Template de configuração (para CLI)
├── .gitignore            # Arquivos ignorados pelo git
└── README.md             # Este arquivo
```

## Dicas de Segurança

- **Comece com poucos follows** (5-10) e aumente gradualmente
- **Use delays maiores** (8-15s) para parecer mais humano
- **Não execute mais de 1-2 vezes por dia**
- **Máximo de 50-100 follows por dia** no total
- **Monitore sua conta** para sinais de restrição

## Solução de Problemas

| Problema | Solução |
|----------|---------|
| Navegador não abre | Execute `playwright install chromium` |
| Login não detectado | Aguarde a página carregar totalmente após logar |
| Perfil não encontrado | Verifique se o nome está correto (sem @ e sem URL) |
| Bloqueio temporário | Pare o bot e aguarde 24-48h |
