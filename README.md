# Online na Promo

Aplicativo desktop para automação de follow/unfollow no Instagram e postagem de ofertas de afiliados via **LDPlayer** (emulador Android).

## Aviso Legal

Este projeto é **apenas para fins educacionais**. O uso de automação viola os [Termos de Serviço do Instagram](https://help.instagram.com/581066165581870). Use por sua conta e risco.

## Funcionalidades

- **Interface gráfica** moderna com CustomTkinter (tema escuro)
- **Bot de Follow**: segue automaticamente seguidores de um perfil (filtro inteligente)
- **Limpar Desumildes**: deixa de seguir quem não segue de volta (via LDPlayer)
- **Ofertas**: busca ofertas do Mercado Livre, gera links meli.la de afiliado e posta stories no Instagram via LDPlayer
- **Conexão automática** ao emulador LDPlayer (sem cabo, sem celular)
- **Login ML persistente**: loga uma vez no Mercado Livre e fica logado por meses
- **Status visual**: indicadores LED de conexão (Instagram/LDPlayer e Mercado Livre)

## Como funciona

### Bot de Follow

1. Clique em **"Abrir Chrome e Logar"** — abre o Chrome com perfil dedicado
2. Faça login no Instagram
3. Defina o **perfil alvo** e clique em **"Iniciar Follow"**
4. O bot segue perfis onde `seguindo > seguidores` (maior chance de follow-back)

### Limpar Desumildes (Unfollow)

1. Na aba **"Limpar Desumildes"**, coloque seu @
2. Clique **"Limpar Desumildes"**
3. O programa coleta quem você segue e quem te segue pelo app Instagram no LDPlayer
4. Faz unfollow automático de quem não te segue de volta

### Ofertas (Mercado Livre → Instagram)

1. Clique **"Login ML"** (uma vez só — sessão persiste por meses)
2. Clique **"Buscar Ofertas"** (com ou sem palavra-chave)
3. Selecione produtos e clique **"Postar Selecionado"**
4. O programa gera link meli.la, monta a imagem do story e posta via LDPlayer

## Pré-requisitos

- Python 3.12 ou 3.13
- LDPlayer instalado com Instagram logado
- ADB ativado no LDPlayer (Configurações > Outros > Conexão ADB via rede local)
- pip

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

# 4. Instale o navegador do Playwright (para scraping ML)
playwright install chromium
```

## Uso

```bash
python app.py
```

O programa conecta automaticamente ao LDPlayer ao abrir.

## Gerar executável (.exe)

```bash
pyinstaller --onefile --windowed --name "OnlineNaPromo" --collect-all customtkinter --add-data "assets;assets" app.py
```

> No Windows o separador de `--add-data` é `;` (ponto e vírgula).
> No Linux/macOS use `:` → `--add-data "assets:assets"`.

O executável será gerado na pasta `dist/`.

## Configurações LDPlayer (leve)

| Parâmetro | Valor recomendado |
|-----------|-------------------|
| CPU | 1 core |
| RAM | 2 GB |
| Resolução | 540x960 |
| ADB | Ativado |

## Estrutura do Projeto

```
onlinenapromo/
├── app.py                # Aplicativo GUI (CustomTkinter)
├── bot.py                # Lógica do bot de follow (Playwright + CDP)
├── android_story.py      # Postagem de stories e unfollow via LDPlayer
├── story_image.py        # Composição da imagem do story
├── mercadolivre.py       # Scraping de ofertas + geração de link meli.la
├── instagram_api.py      # Módulo legado (Instagram Graph API)
├── instagram_follow.py   # Versão CLI (linha de comando)
├── requirements.txt      # Dependências Python
├── assets/               # Recursos (fundo do story, fontes)
└── README.md             # Este arquivo
```

## Dicas de Segurança

- **Comece com poucos follows** (5-10) e aumente gradualmente
- **Use delays maiores** (8-15s) para parecer mais humano
- **Não execute mais de 1-2 vezes por dia**
- **Máximo de 50-100 follows por dia** no total
- **Monitore sua conta** para sinais de restrição
