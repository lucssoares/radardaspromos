"""Postagem de Story no app do Instagram via Android (uiautomator2/ADB).

Controla o app real do Instagram num emulador/dispositivo Android para
publicar um story com imagem + sticker de link clicável (meli.la).
A Graph API do Instagram não permite stickers; este é o único jeito
confiável de ter link clicável no story.

Requer:
  - Emulador Android (BlueStacks/LDPlayer/AVD) ou celular com depuração
    USB ligada.
  - Instagram instalado e logado no dispositivo.
  - ADB (platform-tools) acessível no PATH.
  - pip install uiautomator2
"""

from __future__ import annotations

import os
import re
import time
from typing import Callable

try:
    import uiautomator2 as u2
except ImportError:
    u2 = None  # type: ignore[assignment]

IG_PKG = "com.instagram.android"
IG_ACTIVITY = "com.instagram.mainactivity.LauncherActivity"

# Candidatos de texto/content-desc (PT + EN) pra cada passo.
# O bot tenta todos em ordem; o primeiro que existir na tela é clicado.
_CREATE_LABELS = ["nova publicação", "new post", "criar", "create"]
_STORY_LABELS = ["story", "stories", "história", "historia"]
_STICKER_LABELS = [
    "sticker", "stickers", "adesivo", "adesivos",
    "figurinha", "figurinhas",
]
_LINK_LABELS = ["link"]
_DONE_LABELS = [
    "concluído", "concluir", "done", "ok", "pronto", "aplicar",
]
_SHARE_LABELS = [
    "compartilhar", "share",
    "adicionar à sua história", "add to your story",
    "seu story", "your story", "enviar",
]
_GALLERY_LABELS = ["galeria", "gallery", "recentes", "recents"]
_NEXT_LABELS = ["avançar", "next", "próximo", "enviar"]


class AndroidStoryPoster:
    """Publica story via app Android do Instagram com sticker de link."""

    def __init__(
        self,
        serial: str | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.serial = serial
        self._log_fn = log or (lambda m: print(m))
        self.d: u2.Device | None = None  # type: ignore[name-defined]

    def _log(self, msg: str) -> None:
        self._log_fn(msg)

    # ── Conexão ──────────────────────────────────────────────────────────
    def connect(self) -> bool:
        if u2 is None:
            self._log(
                "uiautomator2 não instalado. Rode: pip install uiautomator2"
            )
            return False
        try:
            self.d = (
                u2.connect(self.serial) if self.serial else u2.connect()
            )
            ver = self.d.shell(
                "getprop ro.build.version.release"
            )[0].strip()
            self._log(f"Android conectado (v{ver}).")
            return True
        except Exception as exc:
            self._log(f"Não consegui conectar ao Android: {exc}")
            return False

    # ── Imagem → galeria ─────────────────────────────────────────────────
    def push_image(self, local_path: str) -> str:
        """Envia a imagem pro dispositivo e registra no MediaStore."""
        name = os.path.basename(local_path) or "radar_story.jpg"
        remote = f"/sdcard/Pictures/{name}"
        self.d.push(local_path, remote)
        self.d.shell(
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d file://{remote}"
        )
        self._log(f"  Imagem enviada ao dispositivo: {remote}")
        return remote

    # ── Diagnóstico ──────────────────────────────────────────────────────
    def dump_state(self, label: str = "diag") -> str:
        """Tira screenshot + dump da hierarquia (pra debug / iteração).

        Salva em /sdcard/Pictures/diag_*.png e retorna os textos/desc
        clicáveis visíveis pra log.
        """
        diag_img = f"/sdcard/Pictures/diag_{label}.png"
        try:
            self.d.screenshot(f"/tmp/diag_{label}.png")
            self.d.push(f"/tmp/diag_{label}.png", diag_img)
        except Exception:
            pass
        lines = []
        try:
            xml = self.d.dump_hierarchy()
            nodes = re.findall(
                r'(?:text|content-desc)="([^"]+)"', xml
            )
            seen = set()
            for n in nodes:
                n = n.strip()
                if n and n not in seen:
                    seen.add(n)
                    lines.append(n)
                    if len(lines) > 30:
                        break
        except Exception as exc:
            lines.append(f"(erro dump: {exc})")
        return "\n".join(lines)

    # ── Helpers pra achar/clicar elementos ───────────────────────────────
    def _find_and_click(
        self,
        labels: list[str],
        step_name: str,
        timeout: float = 6.0,
        dump_on_fail: bool = True,
    ) -> bool:
        """Tenta achar e clicar um elemento por texto ou content-desc.

        Tenta vários candidatos em ordem; retorna True se clicou.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for label in labels:
                # Tenta por texto parcial (case insensitive via contains).
                el = self.d(textContains=label)
                if el.exists:
                    el.click()
                    self._log(f"  [{step_name}] clicou (txt): '{label}'")
                    return True
                # Tenta por content-desc parcial.
                el = self.d(descriptionContains=label)
                if el.exists:
                    el.click()
                    self._log(f"  [{step_name}] clicou (desc): '{label}'")
                    return True
            time.sleep(0.8)
        if dump_on_fail:
            self._log(
                f"  [{step_name}] NÃO achei nenhum destes: {labels}. "
                "Textos visíveis abaixo (me envie):"
            )
            self._log(self.dump_state(step_name))
        return False

    def _wait_for_any(
        self, labels: list[str], timeout: float = 8.0
    ) -> bool:
        """Espera qualquer um dos labels aparecer na tela."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for label in labels:
                if (self.d(textContains=label).exists
                        or self.d(descriptionContains=label).exists):
                    return True
            time.sleep(0.5)
        return False

    # ── Fluxo completo ───────────────────────────────────────────────────
    def post_story(
        self, image_path: str, link: str = ""
    ) -> dict:
        """Publica um story com imagem (e sticker de link se fornecido).

        Retorna {'ok': bool, 'step': str, 'detail': str}.
        """
        stats: dict = {"ok": False, "step": "init", "detail": ""}

        if self.d is None and not self.connect():
            stats["detail"] = "sem conexão ao Android"
            return stats

        self._log("Postando story pelo app Android...")

        # 1) Enviar imagem pra galeria do dispositivo.
        stats["step"] = "push_image"
        try:
            self.push_image(image_path)
        except Exception as exc:
            stats["detail"] = f"push falhou: {exc}"
            self._log(f"  Erro ao enviar imagem: {exc}")
            return stats
        time.sleep(1)

        # 2) Abrir o Instagram.
        stats["step"] = "open_ig"
        try:
            self.d.app_start(IG_PKG, activity=IG_ACTIVITY, stop=False)
            self._log("  Instagram aberto.")
            time.sleep(3)
        except Exception as exc:
            stats["detail"] = f"app_start falhou: {exc}"
            self._log(f"  Erro ao abrir Instagram: {exc}")
            return stats

        # 3) Ir pra tela de criar story.
        stats["step"] = "create"
        if not self._open_story_composer():
            stats["detail"] = "não abriu o composer de story"
            return stats

        # 4) Selecionar a imagem da galeria.
        stats["step"] = "pick_image"
        if not self._pick_gallery_image():
            stats["detail"] = "não selecionou imagem da galeria"
            return stats

        # 5) Adicionar sticker de link (se fornecido).
        if link:
            stats["step"] = "sticker"
            if not self._add_link_sticker(link):
                self._log(
                    "  ⚠ Não consegui adicionar sticker. "
                    "Publicando sem sticker..."
                )
                stats["detail"] = "sticker falhou, publicando sem"

        # 6) Compartilhar/publicar.
        stats["step"] = "share"
        if not self._share_story():
            stats["detail"] = "não achou botão de compartilhar"
            return stats

        stats["ok"] = True
        stats["step"] = "done"
        self._log("Story publicado via app Android!")
        return stats

    # ── Sub-steps ────────────────────────────────────────────────────────
    def _open_story_composer(self) -> bool:
        """Navega: home → criar (+) → Story."""
        # Tenta o botão "+" de criar (bottom nav ou top).
        self._find_and_click(
            _CREATE_LABELS, "criar", timeout=6, dump_on_fail=False
        )
        time.sleep(1.5)

        # Tenta clicar em "Story" no menu que abriu.
        if self._find_and_click(
            _STORY_LABELS, "story", timeout=5, dump_on_fail=False
        ):
            time.sleep(2)
            return True

        # Plano B: clicar no avatar "Seu story" no topo do feed.
        alt = ["seu story", "your story", "adicionar ao story",
               "add to story", "add to your story"]
        if self._find_and_click(alt, "seu_story", timeout=4):
            time.sleep(2)
            return True

        # Se nada funcionou, mostra diagnóstico.
        self._log(
            "  [criar] Não encontrei entrada pra story. "
            "Textos na tela:"
        )
        self._log(self.dump_state("create_story"))
        return False

    def _pick_gallery_image(self) -> bool:
        """No composer, abre a galeria e seleciona a primeira imagem."""
        # O composer geralmente já mostra a galeria embaixo.
        # Tenta clicar no thumbnail da galeria (primeiro item recente).
        time.sleep(2)

        # Se houver um botão "Galeria" ou "Gallery", clica.
        self._find_and_click(
            _GALLERY_LABELS, "galeria", timeout=4, dump_on_fail=False
        )
        time.sleep(1.5)

        # Seleciona a primeira imagem (geralmente um ImageView clicável).
        try:
            images = self.d(
                className="android.widget.ImageView", clickable=True
            )
            if images.exists:
                images[0].click()
                self._log("  [galeria] primeira imagem selecionada.")
                time.sleep(2)
                return True
        except Exception:
            pass

        # Plano B: tenta resource-id do grid de mídia do IG.
        try:
            grid = self.d(resourceIdMatches=".*gallery.*|.*media.*|.*grid.*")
            if grid.exists:
                grid[0].click()
                self._log("  [galeria] imagem selecionada (resource-id).")
                time.sleep(2)
                return True
        except Exception:
            pass

        # Tenta "Avançar"/"Next" se a imagem já está selecionada.
        if self._find_and_click(
            _NEXT_LABELS, "next", timeout=3, dump_on_fail=False
        ):
            time.sleep(1.5)
            return True

        self._log(
            "  [galeria] Não selecionei imagem. Textos na tela:"
        )
        self._log(self.dump_state("pick_image"))
        return False

    def _add_link_sticker(self, link: str) -> bool:
        """No editor de story, adiciona sticker de link com a URL."""
        self._log(f"  Adicionando sticker de link: {link}")

        # Abre a barra de stickers.
        if not self._find_and_click(
            _STICKER_LABELS, "stickers", timeout=6
        ):
            return False
        time.sleep(1.5)

        # Procura e clica no sticker "Link".
        if not self._find_and_click(
            _LINK_LABELS, "link_sticker", timeout=5
        ):
            return False
        time.sleep(1.5)

        # Digita a URL no campo que apareceu.
        typed = False
        try:
            field = self.d(className="android.widget.EditText")
            if field.exists:
                field.click()
                time.sleep(0.5)
                field.set_text(link)
                typed = True
                self._log(f"  URL digitada: {link}")
        except Exception:
            pass

        if not typed:
            try:
                self.d.send_keys(link)
                typed = True
            except Exception:
                pass

        if not typed:
            self._log("  Não consegui digitar a URL.")
            self._log(self.dump_state("type_url"))
            return False

        time.sleep(0.8)

        # Confirma o sticker (botão "Concluído"/"Done").
        self._find_and_click(
            _DONE_LABELS, "confirmar_link", timeout=5, dump_on_fail=True
        )
        time.sleep(1.5)
        self._log("  Sticker de link adicionado.")
        return True

    def _share_story(self) -> bool:
        """Clica no botão de compartilhar/publicar o story."""
        if self._find_and_click(
            _SHARE_LABELS, "compartilhar", timeout=8
        ):
            time.sleep(4)
            # Pode ter um segundo passo (confirmar "Seu story").
            self._find_and_click(
                _SHARE_LABELS, "confirmar", timeout=4, dump_on_fail=False
            )
            time.sleep(3)
            return True
        return False

    # ── Diagnóstico standalone ───────────────────────────────────────────
    def run_diagnostic(self) -> str:
        """Abre o IG e captura a hierarquia completa (pra enviar ao dev).

        Retorna um relatório textual com os elementos visíveis.
        """
        if self.d is None and not self.connect():
            return "Sem conexão ao Android."
        try:
            self.d.app_start(IG_PKG, activity=IG_ACTIVITY, stop=False)
            time.sleep(4)
        except Exception as exc:
            return f"Erro ao abrir IG: {exc}"
        report = []
        report.append("=== DIAGNÓSTICO DO APP INSTAGRAM ===")
        report.append(f"Window: {self.d.window_size()}")
        report.append(f"URL IG: pkg={IG_PKG}")

        # Captura hierarquia completa.
        try:
            xml = self.d.dump_hierarchy()
            report.append(f"Hierarquia: {len(xml)} chars")
            # Extrai todos text + content-desc não vazios.
            items = re.findall(
                r'(text|content-desc)="([^"]+)"', xml
            )
            report.append("--- Textos/Descrições visíveis ---")
            seen = set()
            for kind, val in items:
                val = val.strip()
                if val and val not in seen:
                    seen.add(val)
                    report.append(f"  [{kind[:4]}] {val}")
                    if len(report) > 60:
                        report.append("  ... (truncado)")
                        break
        except Exception as exc:
            report.append(f"Erro dump: {exc}")

        # Screenshot pro dev.
        try:
            self.d.screenshot("/tmp/diag_ig.png")
            report.append("Screenshot: /tmp/diag_ig.png")
        except Exception:
            pass

        return "\n".join(report)
