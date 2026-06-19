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

# resource-ids reais do editor de story (descobertos via diagnóstico).
_RID_ASSET = f"{IG_PKG}:id/asset_button"          # Figurinhas (stickers)
_RID_TEXT = f"{IG_PKG}:id/add_text_button"        # Texto
_RID_MUSIC = f"{IG_PKG}:id/music_button"          # Músicas
_RID_OVERFLOW = f"{IG_PKG}:id/overflow_button"    # Mostrar mais ferramentas
_RID_CAPTION = f"{IG_PKG}:id/add_caption_textview"
_RID_SHARE_BAR = f"{IG_PKG}:id/story_share_controls_action_bar"
_RID_GALLERY_THUMB = f"{IG_PKG}:id/gallery_grid_item_thumbnail"
# Marcadores que confirmam que estamos no EDITOR de story.
_EDITOR_RIDS = [_RID_ASSET, _RID_TEXT, _RID_MUSIC, _RID_CAPTION, _RID_SHARE_BAR]

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
            self._grant_media_permissions()
            return True
        except Exception as exc:
            self._log(f"Não consegui conectar ao Android: {exc}")
            return False

    # ── Wi-Fi ADB ─────────────────────────────────────────────────────────
    @staticmethod
    def enable_wifi_adb() -> tuple[bool, str]:
        """Ativa ADB via TCP/IP na porta 5555 (requer USB conectado).

        Returns (ok, message).
        """
        import subprocess

        try:
            result = subprocess.run(
                ["adb", "tcpip", "5555"],
                capture_output=True, text=True, timeout=10,
            )
            out = (result.stdout + result.stderr).strip()
            if result.returncode == 0 or "restarting" in out.lower():
                return True, "ADB Wi-Fi ativado na porta 5555."
            return False, f"Falha ao ativar: {out}"
        except FileNotFoundError:
            return False, "ADB não encontrado no PATH."
        except Exception as exc:
            return False, f"Erro: {exc}"

    @staticmethod
    def get_device_ip() -> str | None:
        """Retorna o IP do dispositivo Android conectado via USB."""
        import subprocess

        try:
            result = subprocess.run(
                ["adb", "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    ip = line.split()[1].split("/")[0]
                    return ip
        except Exception:
            pass
        return None

    @staticmethod
    def adb_connect_wifi(ip: str, port: int = 5555) -> tuple[bool, str]:
        """Conecta via ADB a um dispositivo pelo IP (Wi-Fi).

        Returns (ok, message).
        """
        import subprocess

        addr = f"{ip}:{port}"
        try:
            result = subprocess.run(
                ["adb", "connect", addr],
                capture_output=True, text=True, timeout=10,
            )
            out = (result.stdout + result.stderr).strip()
            if "connected" in out.lower() and "cannot" not in out.lower():
                return True, f"ADB conectado via Wi-Fi: {addr}"
            return False, f"Falha: {out}"
        except FileNotFoundError:
            return False, "ADB não encontrado no PATH."
        except Exception as exc:
            return False, f"Erro: {exc}"

    def _grant_media_permissions(self) -> None:
        """Concede ao Instagram acesso às fotos/mídia (evita o bloqueio
        'Permitir acesso a fotos e vídeos' que trava a galeria)."""
        perms = [
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VIDEO",
            "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
        ]
        granted = 0
        for p in perms:
            try:
                out = self.d.shell(f"pm grant {IG_PKG} {p}")
                txt = out[0] if isinstance(out, (list, tuple)) else str(out)
                if "Exception" not in txt and "Error" not in txt:
                    granted += 1
            except Exception:
                pass
        if granted:
            self._log(
                f"  Permissões de mídia concedidas ao Instagram ({granted})."
            )

    # ── Imagem → galeria ─────────────────────────────────────────────────
    def push_image(self, local_path: str) -> str:
        """Envia a imagem pro dispositivo e registra no MediaStore."""
        base = os.path.basename(local_path) or "radar_story.jpg"
        root, ext = os.path.splitext(base)
        # Nome único por postagem: evita pegar um _id antigo do MediaStore
        # quando o mesmo nome já foi enviado em postagens anteriores.
        name = f"{root}_{int(time.time())}{ext or '.jpg'}"
        remote = f"/sdcard/Pictures/{name}"
        self.d.push(local_path, remote)
        self.d.shell(
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d file://{remote}"
        )
        self._log(f"  Imagem enviada ao dispositivo: {remote}")
        return remote

    def _media_id(self, remote: str) -> str | None:
        """Descobre o _id do MediaStore da imagem (pra montar a URI)."""
        name = os.path.basename(remote)
        try:
            out = self.d.shell(
                "content query --uri content://media/external/images/media "
                "--projection _id "
                f"--where \"_display_name='{name}'\""
            )
            text = out[0] if isinstance(out, (list, tuple)) else str(out)
            ids = re.findall(r"_id=(\d+)", text)
            if ids:
                return ids[-1]  # o mais recente (última inserção)
        except Exception as exc:
            self._log(f"  [intent] não obtive media id: {exc}")
        return None

    def _open_story_via_intent(self, remote: str) -> bool:
        """Abre o editor de story já com a imagem (intent ADD_TO_STORY).

        É bem mais confiável que navegar pela galeria: o Instagram abre
        direto no editor com a imagem como fundo.
        """
        mid = self._media_id(remote)
        if not mid:
            self._log("  [intent] sem media id; vou tentar pela interface.")
            return False
        uri = f"content://media/external/images/media/{mid}"
        try:
            self.d.shell(
                "am start -a com.instagram.share.ADD_TO_STORY "
                "-t image/jpeg "
                f"-d {uri} "
                "--es source_application com.radardaspromos "
                "--grant-read-uri-permission"
            )
        except Exception as exc:
            self._log(f"  [intent] falhou: {exc}")
            return False
        # O editor pode demorar a montar; espera por ele aparecer.
        return self._wait_story_editor(timeout=12)

    def _in_story_editor(self) -> bool:
        """Detecta o editor de story pelos resource-ids reais dos botões."""
        for rid in _EDITOR_RIDS:
            try:
                if self.d(resourceId=rid).exists:
                    return True
            except Exception:
                pass
        # Reforço por texto (algumas versões).
        for lbl in ["figurinhas", "reestilizar"]:
            try:
                if self.d(textContains=lbl).exists:
                    return True
            except Exception:
                pass
        return False

    def _wait_story_editor(self, timeout: float = 12.0) -> bool:
        """Espera o editor de story aparecer (polling)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._in_story_editor():
                return True
            time.sleep(0.6)
        return False

    def _allow_media_permission(self) -> None:
        """Libera o pop-up de permissão de fotos do Instagram, se aparecer."""
        labels = [
            "permitir o acesso", "permitir acesso", "permitir tudo",
            "permitir", "allow all", "allow",
        ]
        for lbl in labels:
            try:
                el = self.d(textContains=lbl)
                if el.exists:
                    el.click()
                    self._log(f"  [perm] liberei acesso: '{lbl}'")
                    time.sleep(1.2)
                    # Pode abrir o diálogo do sistema com outro 'Permitir'.
                    for sysl in ["permitir", "allow"]:
                        s = self.d(textContains=sysl)
                        if s.exists:
                            s.click()
                            time.sleep(1.0)
                    return
            except Exception:
                pass

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
                    if len(lines) > 50:
                        break
            # Lista os elementos CLICÁVEIS com resource-id/classe — ajuda a
            # achar botões que são só ícone (sem texto), como o do sticker.
            lines.append("--- clicáveis (id | desc | classe) ---")
            clicks = re.findall(r"<node[^>]*clickable=\"true\"[^>]*>", xml)
            cseen = set()
            for node in clicks:
                rid = re.search(r'resource-id="([^"]*)"', node)
                desc = re.search(r'content-desc="([^"]*)"', node)
                txt = re.search(r'\btext="([^"]*)"', node)
                cls = re.search(r'class="([^"]*)"', node)
                rid_v = rid.group(1) if rid else ""
                desc_v = (desc.group(1) if desc else "") or (
                    txt.group(1) if txt else "")
                cls_v = (cls.group(1) if cls else "").split(".")[-1]
                key = f"{rid_v}|{desc_v}|{cls_v}"
                if key in cseen:
                    continue
                cseen.add(key)
                rid_short = rid_v.split("/")[-1] if rid_v else "-"
                lines.append(f"  • {rid_short} | {desc_v or '-'} | {cls_v}")
                if len(cseen) > 40:
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
            remote = self.push_image(image_path)
        except Exception as exc:
            stats["detail"] = f"push falhou: {exc}"
            self._log(f"  Erro ao enviar imagem: {exc}")
            return stats
        time.sleep(1)

        # 2) Carregar a imagem no editor de story.
        # Caminho A (mais confiável): intent ADD_TO_STORY com a imagem.
        stats["step"] = "open_story"
        if self._open_story_via_intent(remote):
            self._log(
                "  Editor de story aberto via intent (imagem carregada)."
            )
        else:
            # Caminho B: abrir o app e navegar pela interface.
            self._log(
                "  Intent não abriu o editor; tentando pela interface..."
            )
            try:
                self.d.app_start(IG_PKG, activity=IG_ACTIVITY, stop=False)
                self._log("  Instagram aberto.")
                time.sleep(3)
            except Exception as exc:
                stats["detail"] = f"app_start falhou: {exc}"
                self._log(f"  Erro ao abrir Instagram: {exc}")
                return stats

            stats["step"] = "create"
            if not self._open_story_composer():
                stats["detail"] = "não abriu o composer de story"
                return stats

            # Dump da câmera de story (pra eu ver onde fica a galeria).
            self._log("  [diag câmera de story]:")
            self._log(self.dump_state("camera"))

            stats["step"] = "pick_image"
            if not self._pick_gallery_image():
                stats["detail"] = "não selecionou imagem da galeria"
                return stats

            # Dump do editor (pra eu achar o botão de sticker).
            self._log("  [diag editor de story]:")
            self._log(self.dump_state("editor"))

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
        """Abre a CÂMERA de criar story (e não o visualizador).

        Usa o botão exato 'Adicionar ao story' do topo do feed — clicar em
        'stories' genérico abria o visualizador (Turbinar/Destacar/etc.).
        """
        # Garante que estamos no feed (onde fica 'Adicionar ao story').
        try:
            self.d(descriptionContains="Página inicial").click_exists(
                timeout=3
            )
        except Exception:
            pass
        time.sleep(1.2)

        # Espera o feed carregar o botão de adicionar story.
        self._wait_for_any(
            ["Adicionar ao story", "Add to story", "Seu story"], timeout=8
        )

        # Clica EXATAMENTE no 'Adicionar ao story' (abre a câmera/editor).
        exact_desc = [
            "Adicionar ao story", "Add to story", "Add to your story",
        ]
        for desc in exact_desc:
            try:
                el = self.d(description=desc)
                if el.exists:
                    el.click()
                    self._log(f"  [criar] cliquei (exato): '{desc}'")
                    time.sleep(2.5)
                    return True
            except Exception:
                pass

        # Plano B: o avatar 'Seu story' (também abre a câmera).
        try:
            el = self.d(textContains="Seu story")
            if el.exists:
                el.click()
                self._log("  [criar] cliquei em 'Seu story'.")
                time.sleep(2.5)
                return True
        except Exception:
            pass

        self._log(
            "  [criar] Não encontrei 'Adicionar ao story'. Tela atual:"
        )
        self._log(self.dump_state("create_story"))
        return False

    def _pick_gallery_image(self) -> bool:
        """No composer, abre a galeria e seleciona a primeira imagem."""
        time.sleep(2)

        # Se aparecer o pedido de permissão de fotos, libera.
        self._allow_media_permission()

        # Na câmera do story, deslizar pra cima abre a galeria completa.
        try:
            self.d.swipe_ext("up", scale=0.8)
            time.sleep(1.2)
        except Exception:
            pass
        self._allow_media_permission()

        # A imagem que enviamos é a mais recente → primeiro item do grid.
        try:
            thumbs = self.d(resourceId=_RID_GALLERY_THUMB)
            if thumbs.exists:
                thumbs[0].click()
                self._log(
                    "  [galeria] miniatura mais recente selecionada."
                )
                if self._wait_story_editor(timeout=10):
                    return True
                # Algumas versões pedem 'Avançar' depois de selecionar.
                self._find_and_click(
                    _NEXT_LABELS, "next", timeout=3, dump_on_fail=False
                )
                if self._wait_story_editor(timeout=8):
                    return True
        except Exception as exc:
            self._log(f"  [galeria] erro ao tocar miniatura: {exc}")

        self._log(
            "  [galeria] Não cheguei no editor. Tela atual:"
        )
        self._log(self.dump_state("pick_image"))
        return False

    def _add_link_sticker(self, link: str) -> bool:
        """No editor de story, adiciona sticker de link com a URL."""
        self._log(f"  Adicionando sticker de link: {link}")

        # A barra de ferramentas (Texto/Figurinhas/Músicas) renderiza um
        # instante DEPOIS do editor abrir — espera o asset_button surgir.
        opened = False
        try:
            if self.d(resourceId=_RID_ASSET).wait(timeout=5):
                self.d(resourceId=_RID_ASSET).click()
                opened = True
                self._log("  [sticker] abri Figurinhas (asset_button).")
        except Exception:
            pass
        # Plano B: clicar pelo texto/descrição 'Figurinhas'.
        if not opened:
            for q in ["Figurinhas", "Sticker", "Stickers"]:
                try:
                    el = self.d(textContains=q)
                    if not el.exists:
                        el = self.d(descriptionContains=q)
                    if el.exists:
                        el.click()
                        opened = True
                        self._log(f"  [sticker] abri por texto: '{q}'.")
                        break
                except Exception:
                    pass
        if not opened:
            opened = self._find_and_click(
                _STICKER_LABELS, "stickers", timeout=5, dump_on_fail=False
            )
        if not opened:
            self._log("  [sticker] não achei o botão Figurinhas. Tela:")
            self._log(self.dump_state("sticker_btn"))
            return False
        time.sleep(2)

        # 2) Clica DIRETO na figurinha de link (NÃO usar a busca — digitar na
        #    busca colava a URL no lugar errado). A figurinha tem
        #    resource-id 'sticker_sheet_redesign_item' e desc com 'link'.
        sticker_id = f"{IG_PKG}:id/sticker_sheet_redesign_item"
        clicked_link = False
        for desc in [
            "Figurinha de link", "Link sticker", "Adicionar link",
        ]:
            try:
                el = self.d(resourceId=sticker_id, description=desc)
                if el.exists:
                    el.click()
                    clicked_link = True
                    self._log(f"  [sticker] cliquei na figurinha '{desc}'.")
                    break
            except Exception:
                pass
        if not clicked_link:
            # Qualquer item de figurinha cujo desc contenha 'link'.
            try:
                items = self.d(
                    resourceId=sticker_id, descriptionMatches="(?i).*link.*"
                )
                if items.exists:
                    items[0].click()
                    clicked_link = True
                    self._log("  [sticker] cliquei na figurinha de link.")
            except Exception:
                pass
        if not clicked_link:
            self._log("  [sticker] não achei a figurinha de link. Tela:")
            self._log(self.dump_state("sticker_tray"))
            return False
        time.sleep(2)

        # 3) Tela de inserir o link: digita a URL no campo do link.
        url_id = f"{IG_PKG}:id/link_sticker_list_web_url_edit_text"
        typed = False
        try:
            field = self.d(resourceId=url_id)
            if field.wait(timeout=6):
                field.click()
                time.sleep(0.4)
                field.set_text(link)
                typed = True
                self._log(f"  [sticker] URL digitada no campo do link: {link}")
        except Exception as exc:
            self._log(f"  [sticker] erro ao digitar URL (id): {exc}")
        if not typed:
            # Fallback: qualquer EditText que não seja a busca.
            try:
                fields = self.d(className="android.widget.EditText")
                n = fields.count if hasattr(fields, "count") else 0
                for i in range(n):
                    f = fields[i]
                    try:
                        rid = (f.info or {}).get("resourceName") or ""
                    except Exception:
                        rid = ""
                    if "row_search_edit_text" not in rid:
                        f.click()
                        time.sleep(0.4)
                        f.set_text(link)
                        typed = True
                        break
            except Exception:
                pass
        if not typed:
            self._log("  [sticker] não consegui digitar a URL. Tela:")
            self._log(self.dump_state("link_entry"))
            return False
        time.sleep(0.8)

        # 4) Confirma no botão 'Concluir' pelo id real (texto era 'Concluir'
        #    com maiúscula e a busca por texto falhava).
        done_id = f"{IG_PKG}:id/link_sticker_list_done_button"
        confirmed = False
        try:
            done = self.d(resourceId=done_id)
            if done.exists:
                done.click()
                confirmed = True
                self._log("  [sticker] cliquei em 'Concluir' (id).")
        except Exception:
            pass
        if not confirmed:
            confirmed = self._find_and_click(
                ["concluir", "concluído", "done", "pronto"],
                "confirmar_link", timeout=5, dump_on_fail=True,
            )
        if not confirmed:
            self._log("  [sticker] não confirmei o link.")
            return False
        time.sleep(2.5)
        self._log("  [sticker] sticker de link adicionado.")

        # Arrasta o sticker pra parte inferior do story.
        self._position_link_sticker()
        return True

    def _position_link_sticker(self) -> None:
        """Arrasta a figurinha de link pra parte inferior do story."""
        try:
            w, h = self.d.window_size()
        except Exception as exc:
            self._log(f"  [pos] não obtive tamanho da tela: {exc}")
            return
        self._log(f"  [pos] tela: {w}x{h}")

        # Primeiro: fechar o teclado (se ficou aberto após Concluir).
        try:
            self.d.press("back")
            time.sleep(0.8)
        except Exception:
            pass

        # Localizar sticker na tela.
        src_x, src_y, sticker_info = self._find_sticker_broad()
        if src_x is None:
            # Se não achou, assumir centro do canvas do story.
            src_x, src_y = w // 2, h // 2
            self._log(
                f"  [pos] não localizei sticker; assumo ({src_x},{src_y})."
            )
        else:
            self._log(
                f"  [pos] sticker encontrado em ({src_x},{src_y})"
                f" [{sticker_info}]"
            )

        # Destino: parte inferior do story (~80% da altura da tela).
        dst_x = w // 2
        dst_y = int(h * 0.78)
        self._log(f"  [pos] destino: ({dst_x},{dst_y})")

        time.sleep(0.5)

        # Estratégia: swipe contínuo lento (SEM long-press separado).
        # Um único gesto de 3s que o Instagram reconhece como drag.
        self._log("  [pos] arrastando (swipe 3s)...")
        try:
            self.d.swipe(src_x, src_y, dst_x, dst_y, duration=3.0)
            self._log(
                f"  [pos] swipe ok ({src_x},{src_y})->({dst_x},{dst_y})"
            )
        except Exception as exc:
            self._log(f"  [pos] swipe falhou: {exc}")
            # Fallback: shell input swipe
            try:
                self.d.shell(
                    f"input swipe {src_x} {src_y} {dst_x} {dst_y} 3000"
                )
                self._log("  [pos] fallback shell swipe ok.")
            except Exception as exc2:
                self._log(f"  [pos] fallback falhou: {exc2}")

        time.sleep(1.0)

        # Se o swipe abriu o editor de texto, fechar.
        self._dismiss_text_editor()

    def _dismiss_text_editor(self) -> None:
        """Se o editor de texto do story abriu acidentalmente, fecha."""
        try:
            xml = self.d.dump_hierarchy()
            # O editor de texto tem um campo de texto focado.
            if "story_text_editor" in xml or (
                'class="android.widget.EditText"' in xml
                and "Aa" not in xml[:500]
            ):
                self.d.press("back")
                time.sleep(0.5)
                self._log("  [pos] editor de texto fechado.")
        except Exception:
            pass

    def _find_sticker_broad(
        self,
    ) -> tuple[int | None, int | None, str]:
        """Localiza o sticker de link na tela do editor.

        Busca em duas passadas:
        1) Nó com desc/text contendo 'link', 'figurinha', url etc.
        2) Nó desconhecido na região central (não é toolbar) — candidato.

        Returns (x, y, info) ou (None, None, "").
        """
        skip_ids = {
            "asset_button", "add_text_button", "music_button",
            "overflow_button", "cancel_button", "add_caption_textview",
            "story_share_controls_action_bar",
            "quick_capture_root_container",
            "link_sticker_list_cancel_button",
            "link_sticker_list_done_button",
            "link_sticker_list_web_url_edit_text",
            "link_sticker_custom_cta_row",
            "row_search_edit_text", "back_button_ui_refresh_v2",
        }
        skip_classes = {
            "android.widget.EditText", "android.widget.Button",
        }
        try:
            w, h = self.d.window_size()
            xml = self.d.dump_hierarchy()
        except Exception:
            return None, None, ""

        candidates = []
        for m in re.finditer(r"<node\b[^>]*>", xml):
            node = m.group(0)
            desc = re.search(r'content-desc="([^"]*)"', node)
            txt = re.search(r'\btext="([^"]*)"', node)
            rid = re.search(r'resource-id="([^"]*)"', node)
            cls = re.search(r'class="([^"]*)"', node)
            bounds = re.search(
                r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node
            )
            if not bounds:
                continue
            x1, y1, x2, y2 = (int(bounds.group(i)) for i in range(1, 5))
            bw, bh = x2 - x1, y2 - y1
            desc_v = (desc.group(1) if desc else "").lower()
            txt_v = (txt.group(1) if txt else "").lower()
            rid_v = (rid.group(1) if rid else "").split("/")[-1]
            cls_v = cls.group(1) if cls else ""

            if rid_v in skip_ids:
                continue

            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            label = desc_v or txt_v

            # Passada 1: texto/desc remete a link
            link_match = (
                "figurinha de link" in label
                or "link sticker" in label
                or ("link" in label and "figurinha" in label)
                or "meli.la" in label
                or "mercadolivre" in label
                or (txt_v == "link" and bw > 60 and bw < 600)
            )
            if link_match:
                return cx, cy, f"desc/txt match: {label[:40]}"

            # Passada 2: candidato por posição + tamanho
            if cls_v in skip_classes:
                continue
            in_center_h = abs(cx - w // 2) < w * 0.35
            in_center_v = h * 0.30 < cy < h * 0.70
            reasonable_size = 50 < bw < 800 and 30 < bh < 300
            not_toolbar = cy > h * 0.15 and cy < h * 0.85
            is_known = rid_v in (
                "camera_controls", "footer_container",
                "story_share_controls_action_bar",
            )
            if (
                in_center_h
                and in_center_v
                and reasonable_size
                and not_toolbar
                and not is_known
                and not rid_v
            ):
                score = abs(cy - h // 2) + abs(cx - w // 2)
                candidates.append((score, cx, cy, f"{cls_v} {bw}x{bh}"))

        if candidates:
            candidates.sort()
            _, cx, cy, info = candidates[0]
            return cx, cy, f"candidato: {info}"

        return None, None, ""

    def _dump_bounds(self) -> str:
        """Lista nós (desc/id + bounds) pra diagnosticar posições na tela."""
        lines: list[str] = []
        try:
            xml = self.d.dump_hierarchy()
        except Exception as exc:
            return f"(erro dump bounds: {exc})"
        seen = set()
        for m in re.finditer(r"<node\b[^>]*>", xml):
            node = m.group(0)
            desc = re.search(r'content-desc="([^"]*)"', node)
            txt = re.search(r'\btext="([^"]*)"', node)
            rid = re.search(r'resource-id="([^"]*)"', node)
            bounds = re.search(
                r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node
            )
            label = (desc.group(1) if desc else "") or (
                txt.group(1) if txt else "")
            rid_v = (rid.group(1) if rid else "").split("/")[-1]
            if not bounds or (not label and not rid_v):
                continue
            b = bounds.group(0).replace("bounds=", "").strip('"')
            key = f"{rid_v}|{label}|{b}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  • {rid_v or '-'} | {label or '-'} | {b}")
            if len(lines) > 60:
                break
        return "\n".join(lines)

    def _share_story(self) -> bool:
        """Publica o story clicando em 'Seus stories' (barra inferior)."""
        # Botão principal: 'Seus stories' (publica no seu story).
        for q in ["Seus stories", "Seu story", "Your story", "Your stories"]:
            try:
                el = self.d(textContains=q)
                if not el.exists:
                    el = self.d(descriptionContains=q)
                if el.exists:
                    el.click()
                    self._log(f"  [compartilhar] cliquei em '{q}'.")
                    time.sleep(5)
                    # Possível confirmação extra.
                    self._find_and_click(
                        ["compartilhar", "share", "concluído", "ok"],
                        "confirmar", timeout=3, dump_on_fail=False,
                    )
                    time.sleep(3)
                    return True
            except Exception:
                pass

        # Reforço: rótulos genéricos de compartilhar.
        if self._find_and_click(
            _SHARE_LABELS, "compartilhar", timeout=6
        ):
            time.sleep(4)
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
