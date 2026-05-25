"""Módulo para extrair dados de produtos do Mercado Livre.

Usa o Playwright (CDP) para navegar ao ML e capturar título, preço,
imagem e gerar link de afiliado.
"""

import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


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
    - Formato matt: "matt:USERNAME:TOOLID"
    - Formato simples: "SEUNOME-20"
    - Formato URL com params já prontos
    """
    parsed = urlparse(product_url)

    # Remover parâmetros de tracking existentes
    existing_params = parse_qs(parsed.query)
    clean_params = {
        k: v[0]
        for k, v in existing_params.items()
        if not k.startswith("matt_") and k != "tag"
    }

    if affiliate_tag.startswith("matt:"):
        parts = affiliate_tag.split(":")
        if len(parts) >= 3:
            clean_params["matt_tool"] = parts[2]
            clean_params["matt_word"] = ""
            clean_params["matt_source"] = ""
            clean_params["matt_campaign_id"] = ""
    else:
        clean_params["tag"] = affiliate_tag

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
