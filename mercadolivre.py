"""Módulo para extrair dados de produtos do Mercado Livre.

Usa o Playwright (CDP) para navegar ao ML e capturar título, preço,
imagem e gerar link de afiliado.  Inclui busca automática de ofertas
na página de promoções do dia.
"""

import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

OFFERS_URL = "https://www.mercadolivre.com.br/ofertas"


def extract_product_data(page, product_url: str, on_log=None) -> dict | None:
    """Abre a URL do produto no Chrome via CDP e extrai os dados.

    Retorna dict com: title, price, original_price, discount, image_url, url
    ou None em caso de erro.
    """
    log = on_log or (lambda msg: None)

    log(f"Abrindo produto: {product_url}")
    try:
        page.goto(product_url, wait_until="load", timeout=30000)
    except Exception as exc:
        log(f"Erro ao abrir página: {exc}")
        return None

    time.sleep(3)

    data = page.evaluate("""() => {
        const result = {
            title: '',
            price: '',
            original_price: '',
            discount: '',
            image_url: '',
        };

        // Título
        const titleEl = document.querySelector(
            'h1.ui-pdp-title, h1[class*="title"]'
        );
        if (titleEl) result.title = titleEl.innerText.trim();

        // Preço atual
        const priceEl = document.querySelector(
            'span.andes-money-amount__fraction'
        );
        if (priceEl) {
            const cents = document.querySelector(
                'span.andes-money-amount__cents'
            );
            result.price = priceEl.innerText.trim();
            if (cents) result.price += ',' + cents.innerText.trim();
        }

        // Preço original (riscado)
        const origPriceEl = document.querySelector(
            's.andes-money-amount, '
            + 'del .andes-money-amount__fraction, '
            + '.ui-pdp-price__original-value .andes-money-amount__fraction'
        );
        if (origPriceEl) {
            result.original_price = origPriceEl.innerText.trim();
        }

        // Desconto
        const discountEl = document.querySelector(
            'span.ui-pdp-price__second-line__label, '
            + 'span[class*="discount"], '
            + '.andes-money-amount__discount'
        );
        if (discountEl) {
            result.discount = discountEl.innerText.trim();
        }

        // Imagem principal
        const imgEl = document.querySelector(
            'img.ui-pdp-image, '
            + 'img[data-zoom], '
            + '.ui-pdp-gallery__figure img, '
            + 'figure img[src*="mlstatic"]'
        );
        if (imgEl) {
            result.image_url = imgEl.src || imgEl.getAttribute('data-src') || '';
        }

        return result;
    }""")

    if not data or not data.get("title"):
        log("Não foi possível extrair dados do produto.")
        log("Tentando método alternativo via meta tags...")

        data = page.evaluate("""() => {
            const result = {
                title: '',
                price: '',
                original_price: '',
                discount: '',
                image_url: '',
            };

            const ogTitle = document.querySelector('meta[property="og:title"]');
            if (ogTitle) result.title = ogTitle.content || '';

            const ogImage = document.querySelector('meta[property="og:image"]');
            if (ogImage) result.image_url = ogImage.content || '';

            // Tentar pegar preço do texto visível
            const bodyText = document.body.innerText || '';
            const priceMatch = bodyText.match(
                /R\\$\\s*([\\d.,]+)/
            );
            if (priceMatch) result.price = priceMatch[1];

            return result;
        }""")

    if not data or not data.get("title"):
        log("Falha ao extrair dados do produto.")
        return None

    data["url"] = product_url
    log(f"Produto: {data['title']}")
    log(f"Preço: R$ {data.get('price', 'N/A')}")
    if data.get("original_price"):
        log(f"Preço original: R$ {data['original_price']}")
    if data.get("discount"):
        log(f"Desconto: {data['discount']}")

    return data


def generate_affiliate_link(product_url: str, affiliate_tag: str) -> str:
    """Gera o link de afiliado a partir da URL do produto.

    affiliate_tag pode ser:
    - Número direto do matt_tool: "38524122"
    - Formato matt: "matt:USERNAME:TOOLID" ou "matt::TOOLID"
    - Formato simples: "SEUNOME-20"
    """
    parsed = urlparse(product_url)

    # Remover parâmetros de tracking existentes
    existing_params = parse_qs(parsed.query)
    clean_params = {
        k: v[0]
        for k, v in existing_params.items()
        if not k.startswith("matt_") and k != "tag"
    }

    tag = affiliate_tag.strip()

    if tag.startswith("matt:"):
        parts = tag.split(":")
        tool_id = parts[-1]
        clean_params["matt_tool"] = tool_id
        clean_params["matt_word"] = ""
        clean_params["matt_source"] = ""
        clean_params["matt_campaign_id"] = ""
    elif tag.isdigit():
        clean_params["matt_tool"] = tag
        clean_params["matt_word"] = ""
        clean_params["matt_source"] = ""
        clean_params["matt_campaign_id"] = ""
    else:
        clean_params["tag"] = tag

    new_query = urlencode(clean_params)
    new_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        "",
    ))

    return new_url


def format_whatsapp_message(product_data: dict, affiliate_link: str) -> str:
    """Formata mensagem para compartilhar via WhatsApp."""
    title = product_data.get("title", "Produto")
    price = product_data.get("price", "")
    original_price = product_data.get("original_price", "")
    discount = product_data.get("discount", "")

    lines = [
        f"*{title}*",
        "",
    ]

    if original_price and discount:
        lines.append(f"~De R$ {original_price}~")
        lines.append(f"*Por R$ {price}* ({discount})")
    elif price:
        lines.append(f"*R$ {price}*")

    lines.extend([
        "",
        f"Compre aqui: {affiliate_link}",
        "",
        "Aproveite essa oferta!",
    ])

    return "\n".join(lines)


def scrape_offers(
    page,
    max_items: int = 20,
    category: str = "",
    on_log=None,
) -> list[dict]:
    """Navega na página de ofertas do ML e extrai produtos em promoção.

    Retorna lista de dicts com: title, price, original_price, discount,
    image_url, url.
    """
    log = on_log or (lambda msg: None)

    url = OFFERS_URL
    if category:
        url += f"#{category}"

    log(f"Abrindo página de ofertas: {url}")
    try:
        page.goto(url, wait_until="load", timeout=30000)
    except Exception as exc:
        log(f"Erro ao abrir ofertas: {exc}")
        return []

    time.sleep(4)

    # Scroll para carregar mais produtos (lazy loading)
    log("Carregando ofertas...")
    for _ in range(3):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(1.5)

    items = page.evaluate("""(maxItems) => {
        const products = [];
        const seen = new Set();

        // Selecionar cards de produto (ofertas do dia)
        const cards = document.querySelectorAll(
            'a.promotion-item__link-container, '
            + 'a.poly-component__title--link, '
            + 'li.promotion-item, '
            + 'div.andes-card[data-testid], '
            + 'div.poly-card, '
            + 'section.deal-card, '
            + 'div[class*="promotion-item"], '
            + 'a[href*="/MLB"]'
        );

        for (const card of cards) {
            if (products.length >= maxItems) break;

            let link = '';
            let title = '';
            let price = '';
            let originalPrice = '';
            let discount = '';
            let imageUrl = '';

            // Extrair link
            if (card.tagName === 'A') {
                link = card.href || '';
            } else {
                const a = card.querySelector('a[href]');
                if (a) link = a.href || '';
            }

            if (!link || seen.has(link)) continue;
            if (!link.includes('mercadolivre.com.br')
                && !link.includes('mercadolibre.com')) continue;

            seen.add(link);

            // Escopo de busca
            const scope = card.tagName === 'A' ? card.parentElement : card;

            // Título
            const titleEl = scope.querySelector(
                'p.promotion-item__title, '
                + 'a.poly-component__title--link, '
                + 'h2, h3, p[class*="title"], '
                + 'span[class*="title"]'
            );
            if (titleEl) title = titleEl.innerText.trim();
            if (!title && card.tagName === 'A') {
                title = card.getAttribute('title')
                     || card.innerText.trim().split('\\n')[0];
            }

            // Preço
            const priceEl = scope.querySelector(
                'span.andes-money-amount__fraction, '
                + 'span[class*="price"] span[class*="fraction"]'
            );
            if (priceEl) {
                price = priceEl.innerText.trim();
                const cents = scope.querySelector(
                    'span.andes-money-amount__cents'
                );
                if (cents) price += ',' + cents.innerText.trim();
            }

            // Preço original
            const origEl = scope.querySelector(
                's span.andes-money-amount__fraction, '
                + 'del span.andes-money-amount__fraction, '
                + 'span[class*="previous"] span[class*="fraction"]'
            );
            if (origEl) originalPrice = origEl.innerText.trim();

            // Desconto
            const discEl = scope.querySelector(
                'span.promotion-item__discount-text, '
                + 'span[class*="discount"], '
                + 'span.poly-component__discount, '
                + 'label[class*="rebate"]'
            );
            if (discEl) discount = discEl.innerText.trim();

            // Imagem
            const imgEl = scope.querySelector(
                'img[src*="mlstatic"], img[data-src*="mlstatic"], img'
            );
            if (imgEl) {
                imageUrl = imgEl.src || imgEl.getAttribute('data-src') || '';
            }

            if (title && link) {
                products.push({
                    title, price, original_price: originalPrice,
                    discount, image_url: imageUrl, url: link,
                });
            }
        }

        return products;
    }""", max_items)

    log(f"Encontradas {len(items)} ofertas.")
    return items


def scrape_offers_by_keyword(
    page,
    keyword: str,
    max_items: int = 20,
    on_log=None,
) -> list[dict]:
    """Busca produtos por palavra-chave no ML e extrai os resultados.

    Retorna lista de dicts com: title, price, original_price, discount,
    image_url, url.
    """
    log = on_log or (lambda msg: None)

    search_url = (
        f"https://lista.mercadolivre.com.br/{keyword.replace(' ', '-')}"
        f"_OrderId_PRICE*DESC_PriceRange_0-0_DEAL_yes"
    )

    log(f"Buscando ofertas para: {keyword}")
    try:
        page.goto(search_url, wait_until="load", timeout=30000)
    except Exception as exc:
        log(f"Erro ao buscar: {exc}")
        return []

    time.sleep(3)

    items = page.evaluate("""(maxItems) => {
        const products = [];
        const seen = new Set();

        const cards = document.querySelectorAll(
            'li.ui-search-layout__item, '
            + 'div.ui-search-result, '
            + 'div.poly-card'
        );

        for (const card of cards) {
            if (products.length >= maxItems) break;

            let link = '';
            let title = '';
            let price = '';
            let originalPrice = '';
            let discount = '';
            let imageUrl = '';

            const a = card.querySelector(
                'a.ui-search-link, a.poly-component__title--link, '
                + 'a[href*="/MLB"]'
            );
            if (a) link = a.href || '';

            if (!link || seen.has(link)) continue;
            seen.add(link);

            const titleEl = card.querySelector(
                'h2.ui-search-item__title, '
                + 'a.poly-component__title--link, '
                + 'h2, h3'
            );
            if (titleEl) title = titleEl.innerText.trim();

            const priceEl = card.querySelector(
                'span.andes-money-amount__fraction'
            );
            if (priceEl) {
                price = priceEl.innerText.trim();
                const cents = card.querySelector(
                    'span.andes-money-amount__cents'
                );
                if (cents) price += ',' + cents.innerText.trim();
            }

            const origEl = card.querySelector(
                's span.andes-money-amount__fraction, '
                + 'del span.andes-money-amount__fraction'
            );
            if (origEl) originalPrice = origEl.innerText.trim();

            const discEl = card.querySelector(
                'span[class*="discount"], '
                + 'label[class*="rebate"]'
            );
            if (discEl) discount = discEl.innerText.trim();

            const imgEl = card.querySelector(
                'img[src*="mlstatic"], img'
            );
            if (imgEl) {
                imageUrl = imgEl.src
                        || imgEl.getAttribute('data-src') || '';
            }

            if (title && link) {
                products.push({
                    title, price, original_price: originalPrice,
                    discount, image_url: imageUrl, url: link,
                });
            }
        }

        return products;
    }""", max_items)

    log(f"Encontrados {len(items)} produtos com ofertas.")
    return items


def format_instagram_caption(
    product_data: dict, affiliate_link: str
) -> str:
    """Formata legenda para post no Instagram com dados do produto."""
    title = product_data.get("title", "Produto")
    price = product_data.get("price", "")
    original_price = product_data.get("original_price", "")
    discount = product_data.get("discount", "")

    lines = [title, ""]

    if original_price and discount:
        lines.append(f"De R$ {original_price}")
        lines.append(f"Por R$ {price} ({discount})")
    elif price:
        lines.append(f"R$ {price}")

    lines.extend([
        "",
        f"Link na bio ou acesse: {affiliate_link}",
        "",
        "#ofertas #promoção #mercadolivre #desconto "
        "#radardaspromos #ofertadodia",
    ])

    return "\n".join(lines)
