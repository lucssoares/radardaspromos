# Instagram Auto-Follow Bot (Radar das Promos)

Aplicativo desktop com interface gráfica para seguir automaticamente os seguidores de um perfil no Instagram, usando **Playwright + CDP** para conectar ao Chrome real do usuário.

## Aviso Legal

Este projeto é **apenas para fins educacionais**. O uso de automação viola os [Termos de Serviço do Instagram](https://help.instagram.com/581066165581870). Use por sua conta e risco.

## Funcionalidades

- **Interface gráfica** moderna com CustomTkinter (tema escuro)
- **Login manual seguro**: o app abre o Chrome real do sistema, você faz login normalmente (sem salvar senha)
- **Filtro inteligente**: só segue perfis onde `seguindo > seguidores` (maior chance de follow-back)
- **Anti-detecção**: usa Chrome real via CDP (Chrome DevTools Protocol), não Chromium do Playwright
- **Sessão persistente**: login salvo em `chrome_bot_profile/` (só precisa logar uma vez)
- **Delays aleatórios** configuráveis para simular comportamento humano
- **Limite de follows** por sessão
- **Barra de progresso** e log em tempo real
- **Botão de parar** para interromper a qualquer momento
- **Dashboard de métricas**: acompanhe seguidores ganhos/perdidos via Instagram Graph API (requer conta profissional)

## Como funciona

### Bot de Follow

1. Clique em **"Abrir Chrome e Logar"** — abre o Chrome com perfil dedicado do bot
2. Faça login no Instagram (pode usar 2FA normalmente)
3. Defina o **perfil alvo** e clique em **"Iniciar Follow"**
4. O bot navega até o perfil alvo, abre a lista de seguidores e:
   - Para cada seguidor, verifica se `seguindo > seguidores`
   - Se sim, segue o perfil (maior chance de follow-back)
   - Se não, pula para o próximo

### Dashboard de Métricas

Para usar o dashboard, você precisa de uma **conta profissional** (Business/Creator) no Instagram:

1. Na aba **"Dashboard de Métricas"**, cole seu **Access Token** do Meta
2. Clique **"Conectar à API"** — seus dados são carregados automaticamente
3. Use **"Atualizar Métricas"** para registrar novos dados
4. Use **"Ver Histórico"** para acompanhar a evolução dos seguidores

Para obter o Access Token: acesse [Graph API Explorer](https://developers.facebook.com/tools/explorer/), selecione seu app e as permissões `instagram_business_basic` e `instagram_business_manage_insights`.

## Pré-requisitos

- Python 3.12 ou 3.13 (**NÃO** usar 3.14 beta)
- Google Chrome instalado
- pip
- Conta profissional no Instagram (para o dashboard de métricas)

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/lucssoares/radardaspromos.git
cd radardaspromos

# 2. Crie um ambiente virtual (recomendado)
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Instale o navegador do Playwright
playwright install chromium
```

## Uso

```bash
python app.py
```

**Nota**: Pode manter seu Chrome normal aberto — o bot abre uma janela separada.

## Gerar executável (.exe)

```bash
pyinstaller --onefile --windowed --name "InstaFollowBot" --collect-all customtkinter app.py
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
radardaspromos/
├── app.py                # Aplicativo GUI (CustomTkinter)
├── bot.py                # Lógica do bot (Playwright + CDP)
├── instagram_api.py      # Módulo de métricas (Instagram Graph API)
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
| Chrome não abre | Verifique se o Google Chrome está instalado |
| Porta de debug não responde | Feche todas as janelas do Chrome e tente novamente |
| Login não detectado | Aguarde a página carregar totalmente após logar |
| Perfil não encontrado | Verifique se o nome está correto (sem @ e sem URL) |
| Bloqueio temporário | Pare o bot e aguarde 24-48h |
| Erro de greenlet/compilação | Use Python 3.12 ou 3.13 (não 3.14) |
