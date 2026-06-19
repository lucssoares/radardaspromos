"""Composição da imagem do story do Radar das Promos.

Monta um story 1080x1920 com o fundo fixo + a imagem do produto
(pequena, centralizada) + o link curto logo abaixo da imagem.
"""

import io
import os
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

STORY_W = 1080
STORY_H = 1920


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Carrega uma fonte TrueType; cai no default se não achar."""
    try:
        if path and os.path.isfile(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    return ImageFont.load_default()


def _download_image(url: str) -> Image.Image | None:
    """Baixa a imagem do produto e retorna como objeto PIL (RGBA)."""
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    start_size: int,
    max_width: int,
    min_size: int = 18,
) -> ImageFont.FreeTypeFont:
    """Acha o maior tamanho de fonte que faz o texto caber em max_width."""
    size = start_size
    while size > min_size:
        font = _font(font_path, size)
        width = draw.textlength(text, font=font)
        if width <= max_width:
            return font
        size -= 2
    return _font(font_path, min_size)


def build_story_image(
    bg_path: str,
    out_path: str,
    product_image_url: str = "",
    link: str = "",
    font_regular: str = "",
    font_bold: str = "",
) -> str:
    """Compõe o story (fundo + produto pequeno + link curto) e salva.

    Retorna o caminho do arquivo gerado (``out_path``).
    """
    if os.path.isfile(bg_path):
        bg = Image.open(bg_path).convert("RGB")
        if bg.size != (STORY_W, STORY_H):
            bg = bg.resize((STORY_W, STORY_H), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (STORY_W, STORY_H), (15, 15, 15))

    canvas = bg.convert("RGBA")
    draw = ImageDraw.Draw(canvas)

    # ── Imagem do produto (pequena, em um card branco arredondado) ────────
    card_max = 460  # lado máximo do card do produto (pequeno)
    prod = _download_image(product_image_url)
    # Produto fica no TOPO do story pra que o sticker de link
    # (que nasce no centro) fique naturalmente ABAIXO.
    block_top = 200

    card_bottom = block_top
    if prod is not None:
        prod.thumbnail((card_max, card_max), Image.LANCZOS)
        pad = 24
        card_w = prod.width + pad * 2
        card_h = prod.height + pad * 2
        card_x = (STORY_W - card_w) // 2
        card_y = block_top

        # Card branco arredondado (fundo da foto do produto).
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(card)
        cdraw.rounded_rectangle(
            [(0, 0), (card_w - 1, card_h - 1)],
            radius=28,
            fill=(255, 255, 255, 255),
        )
        card.paste(prod, (pad, pad), prod)
        canvas.alpha_composite(card, (card_x, card_y))
        card_bottom = card_y + card_h

    # ── Link curto logo abaixo da imagem do produto ───────────────────────
    if link:
        max_width = int(STORY_W * 0.86)
        link_font = _fit_font(
            draw, link, font_regular, 36, max_width, min_size=18
        )
        text_w = draw.textlength(link, font=link_font)
        text_x = (STORY_W - text_w) // 2
        text_y = card_bottom + 28

        # Sombra para contraste sobre o fundo escuro.
        draw.text(
            (text_x + 2, text_y + 2), link, font=link_font,
            fill=(0, 0, 0, 200),
        )
        draw.text(
            (text_x, text_y), link, font=link_font,
            fill=(255, 255, 255, 255),
        )

    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path
