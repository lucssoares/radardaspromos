"""
Online na Promo — Aplicativo Desktop.

Interface gráfica para automação de follow, limpeza de desumildes
e postagem de ofertas no Instagram via LDPlayer.
"""

import os
import queue
import random
import sys
import threading
import time as _time

import customtkinter as ctk

from bot import (
    InstagramBot,
    launch_chrome_for_login,
    launch_chrome_with_debug,
    load_follow_log,
    remove_from_follow_log,
)
from instagram_api import load_token_data, save_token_data
from mercadolivre import (
    extract_product_data,
    format_whatsapp_message,
    generate_affiliate_link,
    scrape_offers,
    scrape_offers_by_keyword,
)
from android_story import AndroidStoryPoster
from story_image import build_story_image

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def resource_path(rel_path: str) -> str:
    """Resolve o caminho de um recurso (funciona no .exe do PyInstaller)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


# Fundo padrão usado nos stories (Online na Promo).
STORY_BG_PATH = resource_path(os.path.join("assets", "story_bg.jpg"))
FONT_REGULAR = resource_path(os.path.join("assets", "DejaVuSans.ttf"))
FONT_BOLD = resource_path(os.path.join("assets", "DejaVuSans-Bold.ttf"))

# Onde a imagem composta do story é salva antes de subir.
_APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".onlinenapromo")
STORY_OUT_PATH = os.path.join(_APP_DATA_DIR, "story_compose.jpg")


def display_link(url: str) -> str:
    """Versão curta/limpa do link para mostrar no story."""
    link = (url or "").strip()
    for prefix in ("https://", "http://"):
        if link.lower().startswith(prefix):
            link = link[len(prefix):]
            break
    return link.rstrip("/")


class App(ctk.CTk):
    """Janela principal do aplicativo."""

    WIDTH = 780
    HEIGHT = 750

    def __init__(self):
        super().__init__()
        self.title("Online na Promo")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        # Janela redimensionável (antes era fixa e cortava em telas menores).
        self.resizable(True, True)
        self.minsize(640, 480)
        # Tenta abrir maximizada (Windows/alguns WMs); ignora se não suportado.
        try:
            self.after(80, lambda: self.state("zoomed"))
        except Exception:
            pass

        self._bot: InstagramBot | None = None
        self._chrome_process = None
        self._running = False
        self._unfollow_stop = False
        self._android: AndroidStoryPoster | None = None
        self._scraper_page = None  # Playwright headless page pra scraping ML
        self._scraper_pw = None
        self._ml_pw = None  # Playwright instance para ML (persistent context)
        self._ml_context = None  # Persistent browser context para ML
        self._ml_page = None  # Página ML (linkbuilder)
        self._ml_logged_in = os.path.exists(self._ml_user_data_path())

        # Fila de tarefas para a worker thread do Playwright
        self._task_queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self._build_ui()

        # Status inicial do ML (persistent context em disco)
        if self._ml_logged_in:
            self._update_status_ml(True, "sessao salva")

        # Conecta Android (emulador) automaticamente ao abrir o app.
        # O bot (Chrome/Playwright) NÃO conecta mais automaticamente —
        # abria e fechava janelas do Chrome, incomodando o usuário.
        # O usuário pode conectar manualmente se precisar (buscar ofertas).
        self.after(800, self._auto_connect_android)

    # ── Worker thread (todas as operações Playwright aqui) ───────────────

    def _worker_loop(self) -> None:
        """Loop que executa tarefas na mesma thread (requerido pelo Playwright)."""
        self._pw = None
        while True:
            task = self._task_queue.get()
            if task is None:
                break
            try:
                task()
            except Exception as exc:
                self._safe_log(f"Erro: {exc}")
            finally:
                self._task_queue.task_done()

    def _submit_task(self, fn) -> None:
        """Envia uma tarefa para a worker thread."""
        self._task_queue.put(fn)

    def _run_on_worker(self, fn):
        """Executa fn() na worker thread (Playwright) e retorna o resultado.

        Necessário quando estamos numa thread de postagem mas precisamos
        chamar o Playwright, que só funciona na thread onde a página foi
        criada (senão dá o erro 'Cannot switch to a different thread').
        """
        if threading.current_thread() is self._worker:
            return fn()
        result: dict = {}
        done = threading.Event()

        def _wrapper():
            try:
                result["value"] = fn()
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc
            finally:
                done.set()

        self._submit_task(_wrapper)
        done.wait()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        title = ctk.CTkLabel(
            self,
            text="Online na Promo",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title.pack(pady=(18, 4))

        subtitle = ctk.CTkLabel(
            self,
            text="Follow, unfollow e ofertas de afiliados via LDPlayer",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        subtitle.pack(pady=(0, 10))

        # ── Abas ──────────────────────────────────────────────────────────
        self.tabview = ctk.CTkTabview(self, width=710, height=580)
        self.tabview.pack(padx=20, pady=(0, 10), fill="both", expand=True)

        self.tab_bot = self.tabview.add("Bot de Follow")
        self.tab_unfollow = self.tabview.add("Limpar Desumildes")
        self.tab_offers = self.tabview.add("Ofertas")

        self._build_bot_tab()
        self._build_unfollow_tab()
        self._build_offers_tab()

    def _build_bot_tab(self) -> None:
        """Constrói a aba do bot de follow."""
        tab = self.tab_bot

        # ── Frame de configuração ────────────────────────────────────────
        config_frame = ctk.CTkFrame(tab)
        config_frame.pack(padx=10, pady=(5, 8), fill="x")

        ctk.CTkLabel(
            config_frame,
            text="Perfil alvo (sem @):",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        self.target_entry = ctk.CTkEntry(
            config_frame, placeholder_text="ex: nike", width=300
        )
        self.target_entry.grid(row=0, column=1, padx=12, pady=(12, 4), sticky="w")

        ctk.CTkLabel(
            config_frame,
            text="Máximo de follows:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=1, column=0, padx=12, pady=4, sticky="w")

        self.max_follows_entry = ctk.CTkEntry(
            config_frame, placeholder_text="20", width=100
        )
        self.max_follows_entry.insert(0, "20")
        self.max_follows_entry.grid(row=1, column=1, padx=12, pady=4, sticky="w")

        ctk.CTkLabel(
            config_frame,
            text="Delay (seg):",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=2, column=0, padx=12, pady=4, sticky="w")

        delay_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        delay_frame.grid(row=2, column=1, padx=12, pady=4, sticky="w")

        self.min_delay_entry = ctk.CTkEntry(delay_frame, placeholder_text="3", width=60)
        self.min_delay_entry.insert(0, "3")
        self.min_delay_entry.pack(side="left")

        ctk.CTkLabel(delay_frame, text=" a ").pack(side="left")

        self.max_delay_entry = ctk.CTkEntry(delay_frame, placeholder_text="8", width=60)
        self.max_delay_entry.insert(0, "8")
        self.max_delay_entry.pack(side="left")

        filter_label = ctk.CTkLabel(
            config_frame,
            text="Filtro: seguindo > seguidores (ativado automaticamente)",
            font=ctk.CTkFont(size=11),
            text_color="#4CAF50",
        )
        filter_label.grid(
            row=3, column=0, columnspan=2, padx=12, pady=(4, 12), sticky="w"
        )

        # ── Botões (linha 1) ──────────────────────────────────────────────
        btn_frame1 = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame1.pack(padx=10, pady=(0, 4), fill="x")

        self.login_btn = ctk.CTkButton(
            btn_frame1,
            text="1. Fazer Login (1a vez)",
            command=self._on_login,
            width=190,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2196F3",
            hover_color="#1976D2",
        )
        self.login_btn.pack(side="left", padx=(0, 8))

        self.connect_btn = ctk.CTkButton(
            btn_frame1,
            text="2. Conectar Bot",
            command=self._on_connect_bot,
            width=160,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#FF9800",
            hover_color="#F57C00",
        )
        self.connect_btn.pack(side="left", padx=(0, 8))

        self.start_btn = ctk.CTkButton(
            btn_frame1,
            text="3. Iniciar Follow",
            command=self._on_start,
            width=150,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#4CAF50",
            hover_color="#388E3C",
        )
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_frame1,
            text="Parar",
            command=self._on_stop,
            width=80,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#F44336",
            hover_color="#D32F2F",
        )
        self.stop_btn.pack(side="left")

        # ── Instrução ────────────────────────────────────────────────────
        hint_label = ctk.CTkLabel(
            tab,
            text="Passo 1 só na 1a vez. Depois, use direto o passo 2 (login já salvo).",
            font=ctk.CTkFont(size=11),
            text_color="#FF9800",
        )
        hint_label.pack(pady=(0, 2))

        # ── Barra de progresso ───────────────────────────────────────────
        self.progress = ctk.CTkProgressBar(tab, width=660)
        self.progress.pack(padx=10, pady=(4, 2))
        self.progress.set(0)

        self.progress_label = ctk.CTkLabel(
            tab, text="Pronto", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self.progress_label.pack(pady=(0, 4))

        # ── Log ──────────────────────────────────────────────────────────
        self.log_box = ctk.CTkTextbox(tab, width=660, height=170, state="disabled")
        self.log_box.pack(padx=10, pady=(0, 10))

    def _build_unfollow_tab(self) -> None:
        """Constrói a aba de limpeza de desumildes (unfollow)."""
        tab = self.tab_unfollow

        # ── Título ────────────────────────────────────────────────────────
        ctk.CTkLabel(
            tab,
            text="Limpeza de Desumildes",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(pady=(10, 2))

        ctk.CTkLabel(
            tab,
            text="Deixa de seguir quem não te segue de volta",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(pady=(0, 8))

        # ── Configuração ──────────────────────────────────────────────────
        config_frame = ctk.CTkFrame(tab)
        config_frame.pack(padx=10, pady=(0, 8), fill="x")

        ctk.CTkLabel(
            config_frame,
            text="Seu @ (sem @):",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        self.my_username_entry = ctk.CTkEntry(
            config_frame, placeholder_text="ex: seu_usuario", width=250
        )
        self.my_username_entry.grid(
            row=0, column=1, padx=12, pady=(12, 4), sticky="w"
        )

        # Tentar preencher @ automaticamente do token salvo
        self._try_autofill_username()

        # ── Info dos follows registrados ──────────────────────────────────
        self.follow_count_label = ctk.CTkLabel(
            tab,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#4CAF50",
        )
        self.follow_count_label.pack(pady=(0, 4))
        self._update_follow_count()

        # ── Botões ────────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.pack(padx=10, pady=(0, 4), fill="x")

        self.unfollow_btn = ctk.CTkButton(
            btn_frame,
            text="Limpar Desumildes",
            command=self._on_unfollow,
            width=200,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#E91E63",
            hover_color="#C2185B",
        )
        self.unfollow_btn.pack(side="left", padx=(0, 8))

        self.unfollow_stop_btn = ctk.CTkButton(
            btn_frame,
            text="Parar",
            command=self._on_stop,
            width=80,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#F44336",
            hover_color="#D32F2F",
        )
        self.unfollow_stop_btn.pack(side="left")

        # ── Log ──────────────────────────────────────────────────────────
        self.unfollow_log_box = ctk.CTkTextbox(
            tab, width=660, height=200, state="disabled"
        )
        self.unfollow_log_box.pack(padx=10, pady=(8, 10))

    def _try_autofill_username(self) -> None:
        """Tenta preencher o campo de username automaticamente."""
        saved = load_token_data()
        if saved and saved.get("username"):
            self.my_username_entry.insert(0, saved["username"])

    def _set_username_entry(self, username: str) -> None:
        """Preenche o campo de username e salva no token."""
        if not self.my_username_entry.get().strip():
            self.my_username_entry.insert(0, username)
        saved = load_token_data()
        if saved:
            saved["username"] = username
            save_token_data(saved)

    def _update_follow_count(self) -> None:
        """Atualiza o contador de follows registrados."""
        log = load_follow_log()
        count = len(log)
        if count == 0:
            text = "Nenhum follow registrado ainda. Use o Bot de Follow primeiro."
        else:
            text = f"{count} follows registrados no histórico."
        self.follow_count_label.configure(text=text)

    # ── Aba Ofertas ──────────────────────────────────────────────────────

    def _build_offers_tab(self) -> None:
        """Constrói a aba de Ofertas (ML → Instagram)."""
        tab = self.tab_offers

        ctk.CTkLabel(
            tab,
            text="Ofertas",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(pady=(10, 2))

        ctk.CTkLabel(
            tab,
            text="Busque promoções do ML e poste automaticamente no Instagram",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(pady=(0, 6))

        # ── Barra de status (conexões) ─────────────────────────────────────
        status_frame = ctk.CTkFrame(tab, corner_radius=8)
        status_frame.pack(padx=10, pady=(0, 6), fill="x")

        # Instagram / LDPlayer
        self._status_ig_dot = ctk.CTkLabel(
            status_frame, text="\u2B24", font=ctk.CTkFont(size=12),
            text_color="#F44336", width=16,
        )
        self._status_ig_dot.grid(row=0, column=0, padx=(12, 4), pady=8)
        self._status_ig_label = ctk.CTkLabel(
            status_frame, text="Instagram (LDPlayer): Desconectado",
            font=ctk.CTkFont(size=12),
        )
        self._status_ig_label.grid(row=0, column=1, padx=(0, 24), pady=8, sticky="w")

        # Mercado Livre
        self._status_ml_dot = ctk.CTkLabel(
            status_frame, text="\u2B24", font=ctk.CTkFont(size=12),
            text_color="#F44336", width=16,
        )
        self._status_ml_dot.grid(row=0, column=2, padx=(0, 4), pady=8)
        self._status_ml_label = ctk.CTkLabel(
            status_frame, text="Mercado Livre: Desconectado",
            font=ctk.CTkFont(size=12),
        )
        self._status_ml_label.grid(row=0, column=3, padx=(0, 12), pady=8, sticky="w")

        # ── Tag de afiliado ────────────────────────────────────────────────
        tag_frame = ctk.CTkFrame(tab)
        tag_frame.pack(padx=10, pady=(0, 4), fill="x")

        ctk.CTkLabel(
            tag_frame,
            text="Tag de afiliado:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=8, sticky="w")

        self.affiliate_tag_entry = ctk.CTkEntry(
            tag_frame,
            placeholder_text="matt_tool ID",
            width=200,
        )
        self.affiliate_tag_entry.insert(0, "38524122")
        self.affiliate_tag_entry.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        # ── Busca de ofertas ───────────────────────────────────────────────
        search_frame = ctk.CTkFrame(tab)
        search_frame.pack(padx=10, pady=(0, 4), fill="x")

        ctk.CTkLabel(
            search_frame,
            text="Buscar ofertas:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        self.ml_keyword_entry = ctk.CTkEntry(
            search_frame,
            placeholder_text="Palavra-chave (ex: celular, notebook) ou vazio = ofertas do dia",
            width=420,
        )
        self.ml_keyword_entry.grid(
            row=0, column=1, padx=8, pady=(8, 4), sticky="w"
        )

        ctk.CTkLabel(
            search_frame,
            text="Qtd máxima:",
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")

        self.ml_max_items_entry = ctk.CTkEntry(
            search_frame, width=60,
        )
        self.ml_max_items_entry.insert(0, "10")
        self.ml_max_items_entry.grid(
            row=1, column=1, padx=8, pady=(0, 8), sticky="w"
        )

        # ── URL manual (mantém compatibilidade) ───────────────────────────
        manual_frame = ctk.CTkFrame(tab)
        manual_frame.pack(padx=10, pady=(0, 4), fill="x")

        ctk.CTkLabel(
            manual_frame,
            text="Ou URL manual:",
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, padx=12, pady=6, sticky="w")

        self.ml_url_entry = ctk.CTkEntry(
            manual_frame,
            placeholder_text="Cole a URL de um produto específico do ML",
            width=440,
        )
        self.ml_url_entry.grid(row=0, column=1, padx=8, pady=6, sticky="w")

        # ── Botões ────────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.pack(padx=10, pady=(2, 2), fill="x")

        self.ml_login_btn = ctk.CTkButton(
            btn_frame,
            text="Login ML",
            command=self._on_login_mercadolivre,
            width=100,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#FFD600",
            hover_color="#FFC107",
            text_color="#000000",
        )
        self.ml_login_btn.pack(side="left", padx=(0, 6))

        self.search_offers_btn = ctk.CTkButton(
            btn_frame,
            text="Buscar Ofertas",
            command=self._on_search_offers,
            width=140,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2196F3",
            hover_color="#1976D2",
        )
        self.search_offers_btn.pack(side="left", padx=(0, 6))

        self.extract_btn = ctk.CTkButton(
            btn_frame,
            text="Extrair URL",
            command=self._on_extract_product,
            width=110,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#FF9800",
            hover_color="#F57C00",
        )
        self.extract_btn.pack(side="left", padx=(0, 6))

        self.post_selected_btn = ctk.CTkButton(
            btn_frame,
            text="Postar Selecionado",
            command=self._on_post_selected,
            width=150,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            state="disabled",
            fg_color="#E91E63",
            hover_color="#C2185B",
        )
        self.post_selected_btn.pack(side="left", padx=(0, 6))

        self.auto_post_btn = ctk.CTkButton(
            btn_frame,
            text="Auto-Postar Tudo",
            command=self._on_auto_post_all,
            width=140,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            state="disabled",
            fg_color="#4CAF50",
            hover_color="#388E3C",
        )
        self.auto_post_btn.pack(side="left", padx=(0, 6))

        self.copy_whatsapp_btn = ctk.CTkButton(
            btn_frame,
            text="WhatsApp",
            command=self._on_copy_whatsapp,
            width=90,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            state="disabled",
            fg_color="#25D366",
            hover_color="#128C7E",
        )
        self.copy_whatsapp_btn.pack(side="left")

        # ── Android (emulador / celular) para story com sticker de link ──
        android_frame = ctk.CTkFrame(tab)
        android_frame.pack(padx=10, pady=(2, 4), fill="x")

        ctk.CTkLabel(
            android_frame,
            text="Android (sticker):",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, padx=8, pady=6, sticky="w")

        self.android_serial_entry = ctk.CTkEntry(
            android_frame,
            placeholder_text="auto (Bluestacks/emulador)",
            width=200,
        )
        self.android_serial_entry.grid(row=0, column=1, padx=4, pady=6)
        saved_ip = self._load_android_ip()
        if saved_ip:
            self.android_serial_entry.insert(0, saved_ip)

        self.android_connect_btn = ctk.CTkButton(
            android_frame,
            text="Conectar Android",
            command=self._on_connect_android,
            width=140,
            height=32,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#4CAF50",
            hover_color="#388E3C",
        )
        self.android_connect_btn.grid(row=0, column=2, padx=4, pady=6)

        self.android_diag_btn = ctk.CTkButton(
            android_frame,
            text="Diagnóstico",
            command=self._on_android_diagnostic,
            width=100,
            height=32,
            font=ctk.CTkFont(size=11),
            fg_color="#FF9800",
            hover_color="#F57C00",
        )
        self.android_diag_btn.grid(row=0, column=3, padx=4, pady=6)

        # ── Ofertas (esquerda) + Log (direita), lado a lado ───────────────
        panes = ctk.CTkFrame(tab, fg_color="transparent")
        panes.pack(padx=10, pady=(4, 8), fill="both", expand=True)

        # Coluna esquerda: ofertas
        left_col = ctk.CTkFrame(panes, fg_color="transparent")
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 5))
        ctk.CTkLabel(
            left_col, text="Ofertas encontradas:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        self.offers_listbox = ctk.CTkTextbox(left_col, state="disabled")
        self.offers_listbox.pack(fill="both", expand=True, pady=(2, 0))

        # Coluna direita: log
        right_col = ctk.CTkFrame(panes, fg_color="transparent")
        right_col.pack(side="left", fill="both", expand=True, padx=(5, 0))
        log_header = ctk.CTkFrame(right_col, fg_color="transparent")
        log_header.pack(fill="x")
        ctk.CTkLabel(
            log_header, text="Log:", font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            log_header, text="Copiar Log", command=self._copy_offers_log,
            width=90, height=26, font=ctk.CTkFont(size=11),
            fg_color="#607D8B", hover_color="#455A64",
        ).pack(side="right")
        ctk.CTkButton(
            log_header, text="Limpar", command=self._clear_offers_log,
            width=70, height=26, font=ctk.CTkFont(size=11),
            fg_color="#9E9E9E", hover_color="#757575",
        ).pack(side="right", padx=(0, 6))
        self.offers_log_box = ctk.CTkTextbox(right_col, state="disabled")
        self.offers_log_box.pack(fill="both", expand=True, pady=(2, 0))

        # Dados internos
        self._current_product: dict | None = None
        self._current_affiliate_link: str = ""
        self._offers_list: list[dict] = []
        self._selected_offer_idx: int = -1

    # ── Callbacks ────────────────────────────────────────────────────────

    def _append_log(self, msg: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _safe_log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    # ── Status visual de conexões ──────────────────────────────────────

    def _update_status_ig(self, connected: bool, detail: str = "") -> None:
        """Atualiza o indicador visual do Instagram/LDPlayer."""
        def _do():
            if connected:
                self._status_ig_dot.configure(text_color="#4CAF50")
                text = f"Instagram (LDPlayer): Conectado"
                if detail:
                    text += f" — {detail}"
            else:
                self._status_ig_dot.configure(text_color="#F44336")
                text = "Instagram (LDPlayer): Desconectado"
                if detail:
                    text += f" — {detail}"
            self._status_ig_label.configure(text=text)
        self.after(0, _do)

    def _update_status_ml(self, connected: bool, detail: str = "") -> None:
        """Atualiza o indicador visual do Mercado Livre."""
        def _do():
            if connected:
                self._status_ml_dot.configure(text_color="#4CAF50")
                text = "Mercado Livre: Conectado"
                if detail:
                    text += f" — {detail}"
            else:
                self._status_ml_dot.configure(text_color="#F44336")
                text = "Mercado Livre: Desconectado"
                if detail:
                    text += f" — {detail}"
            self._status_ml_label.configure(text=text)
        self.after(0, _do)

    def _append_unfollow_log(self, msg: str) -> None:
        self.unfollow_log_box.configure(state="normal")
        self.unfollow_log_box.insert("end", msg + "\n")
        self.unfollow_log_box.see("end")
        self.unfollow_log_box.configure(state="disabled")

    def _safe_unfollow_log(self, msg: str) -> None:
        self.after(0, self._append_unfollow_log, msg)

    def _safe_progress(self, current: int, total: int) -> None:
        def _update():
            if total > 0:
                self.progress.set(current / total)
            self.progress_label.configure(text=f"Seguidos: {current} / {total}")

        self.after(0, _update)

    # ── Bot callbacks ────────────────────────────────────────────────────

    def _on_login(self) -> None:
        """Abre Chrome normal (sem debug) para o usuário fazer login."""
        self.login_btn.configure(state="disabled")
        self._safe_log("Abrindo Chrome normal para login...")
        self._safe_log("(Sem automação — Instagram não detecta nada)")

        def _task():
            self._chrome_process = launch_chrome_for_login(
                on_log=self._safe_log,
            )
            self._safe_log("")
            self._safe_log("=" * 50)
            self._safe_log("INSTRUÇÕES:")
            self._safe_log("1. Faça login no Instagram na janela que abriu")
            self._safe_log("2. FECHE a janela do Chrome do bot")
            self._safe_log("3. Clique em 'Conectar Bot' aqui no app")
            self._safe_log("=" * 50)
            self.after(0, lambda: self.login_btn.configure(state="normal"))

        self._submit_task(_task)

    def _auto_connect_bot(self) -> None:
        """Conecta o bot automaticamente no início, se ainda não conectado."""
        if self._bot is not None:
            return
        self._safe_log("Conectando o bot automaticamente...")
        self._on_connect_bot()

    def _on_connect_bot(self) -> None:
        """Fecha Chrome do login, reabre com debug e conecta via CDP."""
        self.connect_btn.configure(state="disabled")
        self.login_btn.configure(state="disabled")
        self._safe_log("Conectando bot ao Instagram...")

        def _task():
            self._chrome_process = launch_chrome_with_debug(
                on_log=self._safe_log,
            )

            try:
                from playwright.sync_api import sync_playwright as start_pw

                self._pw = start_pw().start()
                self._bot = InstagramBot(
                    max_follows=self._get_max_follows(),
                    min_delay=self._get_min_delay(),
                    max_delay=self._get_max_delay(),
                    on_log=self._safe_log,
                    on_progress=self._safe_progress,
                )
                self._bot.connect_to_chrome(self._pw)

                if self._bot.is_already_logged_in():
                    self._safe_log("Login detectado! Pronto para iniciar.")
                else:
                    self._safe_log(
                        "Login não detectado. Use o botão 1 para logar primeiro."
                    )

                self.after(0, lambda: self.start_btn.configure(state="normal"))
                self.after(0, lambda: self.connect_btn.configure(state="normal"))
                self.after(0, lambda: self.unfollow_btn.configure(state="normal"))

            except Exception as exc:
                self._safe_log(f"Erro ao conectar: {exc}")
                self._safe_log(
                    "Feche o Chrome do bot e tente novamente."
                )
                self.after(0, lambda: self.connect_btn.configure(state="normal"))
                self.after(0, lambda: self.login_btn.configure(state="normal"))

        self._submit_task(_task)

    # ── Android (Wi-Fi / USB) ─────────────────────────────────────────────

    _ANDROID_IP_FILE = os.path.join(_APP_DATA_DIR, "android_ip.txt")

    def _save_android_ip(self, ip: str) -> None:
        os.makedirs(_APP_DATA_DIR, exist_ok=True)
        with open(self._ANDROID_IP_FILE, "w") as f:
            f.write(ip.strip())

    def _load_android_ip(self) -> str | None:
        try:
            with open(self._ANDROID_IP_FILE) as f:
                ip = f.read().strip()
                return ip if ip else None
        except FileNotFoundError:
            return None

    # Portas comuns dos emuladores Android
    _EMULATOR_PORTS = [
        ("127.0.0.1:5555", "LDPlayer/Bluestacks"),
        ("127.0.0.1:5556", "LDPlayer/Bluestacks"),
        ("127.0.0.1:5565", "Bluestacks"),
        ("127.0.0.1:21503", "LDPlayer"),
        ("127.0.0.1:62001", "Nox"),
        ("emulator-5554", "AVD"),
    ]

    def _on_connect_android(self, override_ip: str | None = None) -> None:
        """Conecta ao emulador Android (Bluestacks etc.) ou celular."""
        if override_ip is not None:
            ip_or_serial = override_ip
        else:
            ip_or_serial = self.android_serial_entry.get().strip()
        self.android_connect_btn.configure(state="disabled")

        def _do():
            try:
                serial = None

                if ip_or_serial:
                    # Usuário digitou algo específico
                    parts = ip_or_serial.replace(":", ".")
                    looks_like_ip = all(
                        c.isdigit() or c == "." for c in parts
                    )
                    if looks_like_ip and "." in ip_or_serial:
                        ip = ip_or_serial.split(":")[0]
                        port = 5555
                        if ":" in ip_or_serial:
                            try:
                                port = int(ip_or_serial.split(":")[1])
                            except ValueError:
                                port = 5555
                        self._safe_offers_log(
                            f"Conectando a {ip}:{port}..."
                        )
                        ok, msg = AndroidStoryPoster.adb_connect_wifi(
                            ip, port
                        )
                        self._safe_offers_log(f"  {msg}")
                        if not ok:
                            self._safe_offers_log(
                                "Falha na conexão. Verifique se o "
                                "emulador/celular está ligado."
                            )
                            return
                        serial = f"{ip}:{port}"
                        self._save_android_ip(ip_or_serial)
                    else:
                        serial = ip_or_serial
                else:
                    # Auto-detect: tenta emuladores conhecidos
                    self._safe_offers_log(
                        "Buscando emulador Android (Bluestacks etc.)..."
                    )
                    for addr, name in self._EMULATOR_PORTS:
                        self._safe_offers_log(
                            f"  Tentando {name} ({addr})..."
                        )
                        if ":" in addr and not addr.startswith("emulator"):
                            ip, port_s = addr.rsplit(":", 1)
                            ok, msg = AndroidStoryPoster.adb_connect_wifi(
                                ip, int(port_s)
                            )
                            if ok:
                                serial = addr
                                self._safe_offers_log(
                                    f"  {name} encontrado! ({addr})"
                                )
                                self._save_android_ip(addr)
                                self.after(
                                    0,
                                    lambda a=addr: (
                                        self.android_serial_entry.delete(
                                            0, "end"
                                        ),
                                        self.android_serial_entry.insert(
                                            0, a
                                        ),
                                    ),
                                )
                                break
                        else:
                            serial = addr
                            break

                    if serial is None:
                        self._safe_offers_log(
                            "Nenhum emulador encontrado. "
                            "Abra o LDPlayer/Bluestacks e tente de novo."
                        )
                        return

                poster = AndroidStoryPoster(
                    serial=serial, log=self._safe_offers_log
                )
                if poster.connect():
                    self._android = poster
                    version = poster.android_version or "?"
                    self._safe_offers_log(
                        "Android conectado! Stories com sticker de link "
                        "usarão o app do Instagram."
                    )
                    self._update_status_ig(True, f"v{version}")
                else:
                    self._safe_offers_log(
                        "Falha ao conectar ao Android. "
                        "Verifique se o LDPlayer/Bluestacks está aberto e "
                        "o ADB está ativado nas configurações dele."
                    )
                    self._update_status_ig(False)
            except Exception as exc:
                self._safe_offers_log(f"Erro ao conectar: {exc}")
                self._update_status_ig(False)
            finally:
                self.after(
                    0,
                    lambda: self.android_connect_btn.configure(
                        state="normal"
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    def _set_android_ip_entry(self, ip: str) -> None:
        self.android_serial_entry.delete(0, "end")
        self.android_serial_entry.insert(0, ip)

    def _auto_connect_android(self) -> None:
        """Tenta conectar ao Android (emulador) automaticamente."""
        if self._android is not None:
            return
        saved = self._load_android_ip()
        # Se tem um IP salvo que é localhost/127.0.0.1, usa ele.
        # Se é um IP remoto antigo (ex: 192.168.x.x), ignora e tenta
        # emuladores (o usuário migrou pra emulador).
        is_local = saved and (
            saved.startswith("127.0.0.1")
            or saved.startswith("localhost")
            or saved.startswith("emulator")
        )
        if is_local:
            self.after(0, lambda: (
                self.android_serial_entry.delete(0, "end"),
                self.android_serial_entry.insert(0, saved),
            ))
        else:
            # Limpa IP antigo de celular, tenta auto-detect
            if saved:
                self._save_android_ip("")
            self.after(0, lambda: (
                self.android_serial_entry.delete(0, "end"),
            ))
        self._safe_offers_log(
            "Conectando ao Android automaticamente..."
        )
        self._on_connect_android(override_ip="" if not is_local else None)

    def _on_android_diagnostic(self) -> None:
        """Roda o diagnóstico do Instagram no Android (captura hierarquia)."""
        if self._android is None:
            self._append_offers_log(
                "Conecte ao Android primeiro (botão 'Conectar Android')."
            )
            return
        self.android_diag_btn.configure(state="disabled")
        self._safe_offers_log("Rodando diagnóstico do Instagram no Android...")

        def _do():
            try:
                report = self._android.run_diagnostic()
                self._safe_offers_log(report)
            except Exception as exc:
                self._safe_offers_log(f"Erro no diagnóstico: {exc}")
            finally:
                self.after(
                    0,
                    lambda: self.android_diag_btn.configure(state="normal"),
                )

        threading.Thread(target=_do, daemon=True).start()

    def _on_start(self) -> None:
        target = self.target_entry.get().strip().lstrip("@").strip("/")
        if not target:
            self._append_log("Preencha o perfil alvo!")
            return

        if "instagram.com/" in target:
            parts = target.split("instagram.com/")
            target = parts[-1].strip("/").split("/")[0]

        if self._bot:
            self._bot.max_follows = self._get_max_follows()
            self._bot.min_delay = self._get_min_delay()
            self._bot.max_delay = self._get_max_delay()
            self._bot._stop_requested = False

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.login_btn.configure(state="disabled")
        self.connect_btn.configure(state="disabled")
        self._running = True

        def _task():
            try:
                if not self._bot.open_followers(target):
                    self._safe_log("Falha ao abrir seguidores. Verifique o perfil.")
                    self.after(0, self._reset_buttons)
                    return

                stats = self._bot.follow_users(target)

                self._safe_log("=" * 50)
                self._safe_log("Processo finalizado!")
                self._safe_log(f"  Seguidos: {stats['followed']}")
                self._safe_log(f"  Filtrados: {stats['filtered_out']}")
                self._safe_log(f"  Verificados: {stats['checked']}")
                self._safe_log(f"  Erros: {stats['errors']}")
                self._safe_log("=" * 50)
            except Exception as exc:
                self._safe_log(f"Erro: {exc}")
            finally:
                self._running = False
                self.after(0, self._reset_buttons)

        self._submit_task(_task)

    def _on_unfollow(self) -> None:
        """Inicia o processo de limpeza de desumildes via LDPlayer."""
        my_user = self.my_username_entry.get().strip().lstrip("@").strip("/")
        if not my_user:
            self._append_unfollow_log("Preencha seu @ (nome de usuário)!")
            return

        if not self._android:
            self._append_unfollow_log(
                "LDPlayer não conectado! Conecte na aba Ofertas."
            )
            return

        self._unfollow_stop = False
        self.unfollow_btn.configure(state="disabled")
        self.unfollow_stop_btn.configure(state="normal")
        self._running = True

        def _task():
            try:
                self._unfollow_via_android(my_user)
            except Exception as exc:
                self._safe_unfollow_log(f"Erro: {exc}")
            finally:
                self._running = False
                self._unfollow_stop = False
                self.after(0, self._reset_buttons)
                self.after(0, self._update_follow_count)

        threading.Thread(target=_task, daemon=True).start()

    def _unfollow_via_android(self, my_username: str) -> None:
        """Coleta seguidores/seguindo e faz unfollow dos desumildes via Android."""
        log = self._safe_unfollow_log
        old_log = self._android._log_fn
        self._android._log_fn = log

        try:
            stop_fn = lambda: self._unfollow_stop

            log("Iniciando limpeza de desumildes via LDPlayer...")

            # 1. Coletar seguindo
            log("Coletando quem você segue...")
            following = self._android.collect_following(my_username, stop_fn)
            if not following:
                log("Não consegui coletar a lista de 'seguindo'.")
                return
            following_set = {u.lower() for u in following}
            following_set.discard(my_username.lower())

            if self._unfollow_stop:
                log("Parado pelo usuário.")
                return

            # 2. Coletar seguidores
            log("Coletando quem te segue (seus seguidores)...")
            followers = self._android.collect_followers(my_username, stop_fn)
            if not followers:
                log(
                    "Não consegui coletar seus seguidores. "
                    "Abortando por segurança."
                )
                return
            followers_set = {u.lower() for u in followers}

            # 3. Determinar desumildes
            follows_back = 0
            candidates = []
            for u in following_set:
                if u in followers_set:
                    follows_back += 1
                else:
                    candidates.append(u)

            log(f"  {len(following_set)} seguindo | {len(followers_set)} seguidores")
            log(f"  {follows_back} seguem de volta (mantidos)")
            log(f"  {len(candidates)} desumildes (não te seguem de volta).")

            # 4. Unfollow
            unfollowed = 0
            errors = 0
            for username in candidates:
                if self._unfollow_stop:
                    log("Parado pelo usuário.")
                    break

                log(f"@{username}: não te segue de volta. Deixando de seguir...")
                if self._android.unfollow_user(username):
                    unfollowed += 1
                    remove_from_follow_log(username)
                    log(f"  [{unfollowed}] Unfollow @{username}!")
                else:
                    errors += 1

                _time.sleep(random.uniform(2, 4))

            log("=" * 50)
            log("Limpeza finalizada!")
            log(f"  Unfollowed: {unfollowed}")
            log(f"  Seguem de volta: {follows_back}")
            log(f"  Erros: {errors}")
            log("=" * 50)
        finally:
            self._android._log_fn = old_log

    def _on_stop(self) -> None:
        if self._bot:
            self._bot.request_stop()
            self._safe_log("Parando... aguarde a ação atual finalizar.")
        self._unfollow_stop = True
        self._safe_unfollow_log("Parando... aguarde a ação atual finalizar.")
        self.stop_btn.configure(state="disabled")
        self.unfollow_stop_btn.configure(state="disabled")

    def _reset_buttons(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.login_btn.configure(state="normal")
        self.connect_btn.configure(state="normal")
        self.unfollow_btn.configure(state="normal")
        self.unfollow_stop_btn.configure(state="disabled")

    # ── Ofertas callbacks ─────────────────────────────────────────────────

    def _append_offers_log(self, msg: str) -> None:
        self.offers_log_box.configure(state="normal")
        self.offers_log_box.insert("end", msg + "\n")
        self.offers_log_box.see("end")
        self.offers_log_box.configure(state="disabled")

    def _safe_offers_log(self, msg: str) -> None:
        self.after(0, self._append_offers_log, msg)

    def _copy_offers_log(self) -> None:
        """Copia todo o log de Ofertas pra área de transferência."""
        try:
            content = self.offers_log_box.get("1.0", "end").strip()
            self.clipboard_clear()
            self.clipboard_append(content)
            self._append_offers_log("[Log copiado para a área de transferência]")
        except Exception as exc:
            self._append_offers_log(f"[Erro ao copiar log: {exc}]")

    def _clear_offers_log(self) -> None:
        """Limpa o log de Ofertas."""
        self.offers_log_box.configure(state="normal")
        self.offers_log_box.delete("1.0", "end")
        self.offers_log_box.configure(state="disabled")

    def _display_offers_list(self) -> None:
        """Exibe a lista de ofertas encontradas na listbox."""
        self.offers_listbox.configure(state="normal")
        self.offers_listbox.delete("1.0", "end")
        for i, item in enumerate(self._offers_list):
            title = item.get("title", "")[:60]
            price = item.get("price", "N/A")
            discount = item.get("discount", "")
            disc_text = f" ({discount})" if discount else ""
            line = f"[{i + 1}] {title} — R$ {price}{disc_text}\n"
            self.offers_listbox.insert("end", line)
        self.offers_listbox.configure(state="disabled")

    def _get_scraper_page(self):
        """Retorna uma página Playwright headless pra scraping (sem abrir Chrome visível)."""
        if self._scraper_page is not None:
            try:
                self._scraper_page.title()
                return self._scraper_page
            except Exception:
                self._scraper_page = None

        from playwright.sync_api import sync_playwright
        self._scraper_pw = sync_playwright().start()
        browser = self._scraper_pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._scraper_page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
        )
        # Remover flag de automação pra evitar bloqueio anti-bot
        self._scraper_page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
        """)
        return self._scraper_page

    def _ml_user_data_path(self) -> str:
        """Retorna o diretório de dados persistentes do ML."""
        return os.path.join(os.path.dirname(__file__), "ml_browser_data")

    def _close_ml_context(self) -> None:
        """Fecha o contexto headless do ML (libera user_data_dir)."""
        if self._ml_page:
            try:
                self._ml_page.close()
            except Exception:
                pass
            self._ml_page = None
        if self._ml_context:
            try:
                self._ml_context.close()
            except Exception:
                pass
            self._ml_context = None
        if self._ml_pw:
            try:
                self._ml_pw.stop()
            except Exception:
                pass
            self._ml_pw = None

    def _get_ml_page(self):
        """Retorna uma página do contexto persistente ML (headless)."""
        if self._ml_page is not None:
            try:
                self._ml_page.title()
                return self._ml_page
            except Exception:
                self._ml_page = None
                self._ml_context = None
                self._ml_pw = None

        from playwright.sync_api import sync_playwright
        self._ml_pw = sync_playwright().start()
        self._ml_context = self._ml_pw.chromium.launch_persistent_context(
            user_data_dir=self._ml_user_data_path(),
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        self._ml_page = self._ml_context.new_page()
        self._ml_page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
        """)
        return self._ml_page

    def _on_login_mercadolivre(self) -> None:
        """Abre Chromium visível com persistent context pra login no ML.

        Usa o mesmo user_data_dir do headless — sessão salva em disco.
        Roda em thread própria (não bloqueia a worker thread).
        """
        self.ml_login_btn.configure(state="disabled")
        self._safe_offers_log("Abrindo janela pra login no Mercado Livre...")
        self._safe_offers_log(
            "Faça login e FECHE a janela (ou aguarde fechar sozinha)."
        )

        def _login_thread():
            pw = None
            context = None
            try:
                # Fechar contexto headless (liberar user_data_dir)
                self._close_ml_context()

                from playwright.sync_api import sync_playwright
                pw = sync_playwright().start()
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=self._ml_user_data_path(),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = context.new_page()
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver',
                        {get: () => false});
                """)
                page.goto(
                    "https://www.mercadolivre.com.br/afiliados/linkbuilder",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                def _any_page_logged_in():
                    """Verifica se alguma página no contexto indica login OK."""
                    try:
                        for p in context.pages:
                            try:
                                u = (p.url or "").lower()
                            except Exception:
                                continue
                            # Ignorar about:blank
                            if u.startswith("about:"):
                                continue
                            # Se tem login/signin no path = ainda logando
                            has_login = (
                                "/login" in u or "/signin" in u
                                or "lgz/login" in u
                            )
                            if has_login:
                                continue
                            # Página do ML sem login = logou
                            if "mercadolivre" in u or "mercadolibre" in u:
                                return True
                            if "afiliados" in u:
                                return True
                            if "meli" in u:
                                return True
                    except Exception:
                        pass
                    return False

                logged_in = False
                check_count = 0
                for _ in range(150):
                    _time.sleep(2)
                    try:
                        # Verificar se janela foi fechada manualmente
                        try:
                            _ = context.pages
                        except Exception:
                            # Contexto fechado = usuário fechou a janela
                            logged_in = True
                            break

                        if _any_page_logged_in():
                            logged_in = True
                            break

                        # Log a cada 10 checks pra diagnóstico
                        check_count += 1
                        if check_count % 10 == 0:
                            urls = []
                            for p in context.pages:
                                try:
                                    urls.append(p.url[:60])
                                except Exception:
                                    pass
                            self._safe_offers_log(
                                f"  [login] aguardando... "
                                f"paginas: {urls}"
                            )
                    except Exception:
                        logged_in = True
                        break

                if logged_in:
                    _time.sleep(1)
                    self._ml_logged_in = True
                    self._safe_offers_log(
                        "Login ML salvo! Links meli.la serao "
                        "gerados automaticamente."
                    )
                    self._update_status_ml(True)
                else:
                    # Se o usuário fechou a janela, assumir login OK
                    # (a sessão é salva em disco de qualquer forma)
                    self._ml_logged_in = True
                    self._safe_offers_log(
                        "Janela fechada. Sessao ML salva em disco."
                    )
                    self._update_status_ml(True)

                try:
                    context.close()
                except Exception:
                    pass
                try:
                    pw.stop()
                except Exception:
                    pass
            except Exception as exc:
                self._safe_offers_log(f"Erro no login ML: {exc}")
                self._update_status_ml(False, "erro")
                if context:
                    try:
                        context.close()
                    except Exception:
                        pass
                if pw:
                    try:
                        pw.stop()
                    except Exception:
                        pass
            finally:
                self.after(
                    0, lambda: self.ml_login_btn.configure(state="normal")
                )

        # Thread própria: não bloqueia worker (ofertas podem rodar em paralelo)
        t = threading.Thread(target=_login_thread, daemon=True)
        t.start()

    def _generate_meli_la_link(self, product_url: str, tag: str) -> str | None:
        """Gera link meli.la via API interna do ML (persistent context).

        Usa a API /affiliate-program/api/v2/affiliates/createLink
        chamada via fetch() dentro do contexto autenticado do Playwright.
        """
        import re as _re
        import json as _json

        try:
            page = self._get_ml_page()
            cur = (page.url or "").lower()

            # Navegar pro linkbuilder pra garantir cookies e CSRF
            if "mercadolivre" not in cur:
                self._safe_offers_log("  [ml] navegando pro ML...")
                page.goto(
                    "https://www.mercadolivre.com.br/afiliados/linkbuilder",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                _time.sleep(2)

            # Verificar se está logado
            cur_url = page.url or ""
            self._safe_offers_log(f"  [ml] URL: {cur_url[:80]}")
            if "login" in cur_url.lower() or "signin" in cur_url.lower():
                self._safe_offers_log(
                    "  [ml] sessão expirada (redirecionou pro login)"
                )
                self._ml_logged_in = False
                self._update_status_ml(False, "sessao expirada")
                return None

            # Chamar API createLink via fetch() no contexto da página
            self._safe_offers_log("  [ml] chamando API createLink...")
            js_code = """
            async (args) => {
                const [productUrl, affiliateTag] = args;
                try {
                    // Pegar CSRF token do cookie ou meta
                    let csrf = '';
                    const csrfCookie = document.cookie
                        .split(';')
                        .map(c => c.trim())
                        .find(c => c.startsWith('_csrf='));
                    if (csrfCookie) {
                        csrf = csrfCookie.split('=')[1];
                    }
                    // Tentar meta tag
                    if (!csrf) {
                        const meta = document.querySelector(
                            'meta[name="csrf-token"]'
                        );
                        if (meta) csrf = meta.getAttribute('content') || '';
                    }

                    const resp = await fetch(
                        '/affiliate-program/api/v2/affiliates/createLink',
                        {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'Accept': 'application/json',
                                'x-csrf-token': csrf,
                            },
                            credentials: 'include',
                            body: JSON.stringify({
                                urls: [productUrl],
                                tag: affiliateTag,
                            }),
                        }
                    );
                    const text = await resp.text();
                    return {
                        status: resp.status,
                        body: text,
                    };
                } catch (err) {
                    return {status: 0, body: err.message};
                }
            }
            """
            result = page.evaluate(js_code, [product_url, tag])
            status = result.get("status", 0)
            body = result.get("body", "")
            self._safe_offers_log(f"  [ml] API status: {status}")

            if status == 200:
                try:
                    data = _json.loads(body)
                    # Resposta pode ser: {"links": [{"url": "https://meli.la/..."}]}
                    # ou {"url": "https://meli.la/..."} ou lista direta
                    if isinstance(data, dict):
                        # Tentar extrair link de várias estruturas
                        links = data.get("links", [])
                        if links and isinstance(links, list):
                            link = links[0]
                            if isinstance(link, dict):
                                url = link.get("url") or link.get(
                                    "shortUrl"
                                ) or link.get("short_url", "")
                            else:
                                url = str(link)
                            if url:
                                self._safe_offers_log(
                                    f"  [ml] link: {url[:80]}"
                                )
                                return url
                        # Tentar campo direto
                        url = data.get("url") or data.get(
                            "shortUrl"
                        ) or data.get("short_url", "")
                        if url:
                            self._safe_offers_log(
                                f"  [ml] link: {url[:80]}"
                            )
                            return url
                    # Procurar meli.la no body inteiro
                    meli = _re.search(
                        r"https?://meli\.la/[^\s\"'<>]+", body
                    )
                    if meli:
                        self._safe_offers_log(
                            f"  [ml] link (regex): {meli.group(0)}"
                        )
                        return meli.group(0)
                    self._safe_offers_log(
                        f"  [ml] resposta 200 sem link: {body[:120]}"
                    )
                except _json.JSONDecodeError:
                    # Não é JSON, procurar link no texto
                    meli = _re.search(
                        r"https?://meli\.la/[^\s\"'<>]+", body
                    )
                    if meli:
                        return meli.group(0)
                    self._safe_offers_log(
                        f"  [ml] resposta não-JSON: {body[:120]}"
                    )
            else:
                self._safe_offers_log(f"  [ml] erro API: {body[:120]}")
                if status in (401, 403):
                    self._ml_logged_in = False
                    self._update_status_ml(False, "sessao expirada")

            return None
        except Exception as exc:
            self._safe_offers_log(f"  [ml] exceção: {exc}")
            return None

    def _on_search_offers(self) -> None:
        """Busca ofertas do ML via Playwright (headless, não abre Chrome)."""
        keyword = self.ml_keyword_entry.get().strip()
        try:
            max_items = int(self.ml_max_items_entry.get().strip() or "10")
        except ValueError:
            max_items = 10

        self.search_offers_btn.configure(state="disabled")

        def _task():
            try:
                page = self._get_scraper_page()
                if keyword:
                    items = scrape_offers_by_keyword(
                        page, keyword,
                        max_items=max_items,
                        on_log=self._safe_offers_log,
                    )
                else:
                    items = scrape_offers(
                        page,
                        max_items=max_items,
                        on_log=self._safe_offers_log,
                    )

                self._offers_list = items
                self._selected_offer_idx = 0 if items else -1

                if items:
                    self._current_product = items[0]
                    tag = self.affiliate_tag_entry.get().strip()
                    if tag:
                        self._current_affiliate_link = generate_affiliate_link(
                            items[0]["url"], tag
                        )
                    self.after(0, self._display_offers_list)
                    self.after(
                        0,
                        lambda: self.post_selected_btn.configure(
                            state="normal"
                        ),
                    )
                    self.after(
                        0,
                        lambda: self.auto_post_btn.configure(state="normal"),
                    )
                    self.after(
                        0,
                        lambda: self.copy_whatsapp_btn.configure(
                            state="normal"
                        ),
                    )
                    self._safe_offers_log(
                        f"Selecione um produto (1-{len(items)}) ou "
                        f"use 'Auto-Postar Tudo'."
                    )
                else:
                    self._safe_offers_log(
                        "Nenhuma oferta encontrada. "
                        "Verifique sua conexão com a internet."
                    )
                    # Log adicional pra debug
                    try:
                        title = page.title()
                        self._safe_offers_log(f"  (página: {title})")
                    except Exception:
                        pass
            except Exception as exc:
                self._safe_offers_log(f"Erro ao buscar ofertas: {exc}")
            finally:
                self.after(
                    0,
                    lambda: self.search_offers_btn.configure(state="normal"),
                )

        self._submit_task(_task)

    def _on_extract_product(self) -> None:
        """Extrai dados de um produto específico via URL manual."""
        url = self.ml_url_entry.get().strip()
        if not url:
            self._append_offers_log("Cole a URL do produto do Mercado Livre!")
            return

        if "mercadolivre.com.br" not in url and "mercadolibre.com" not in url:
            self._append_offers_log("URL inválida. Use uma URL do ML.")
            return

        self.extract_btn.configure(state="disabled")
        self._safe_offers_log("Extraindo dados do produto...")

        def _task():
            try:
                page = self._get_scraper_page()
                data = extract_product_data(
                    page, url, on_log=self._safe_offers_log
                )
                if not data:
                    self._safe_offers_log("Falha ao extrair dados.")
                    return

                self._current_product = data
                self._offers_list = [data]
                self._selected_offer_idx = 0

                tag = self.affiliate_tag_entry.get().strip()
                if tag:
                    self._current_affiliate_link = generate_affiliate_link(
                        url, tag
                    )
                    self._safe_offers_log(
                        f"Link: {self._current_affiliate_link}"
                    )

                self.after(0, self._display_offers_list)
                self.after(
                    0,
                    lambda: self.post_selected_btn.configure(state="normal"),
                )
                self.after(
                    0,
                    lambda: self.copy_whatsapp_btn.configure(state="normal"),
                )
                self._safe_offers_log("Dados extraídos!")
            except Exception as exc:
                self._safe_offers_log(f"Erro: {exc}")
            finally:
                self.after(
                    0, lambda: self.extract_btn.configure(state="normal")
                )

        self._submit_task(_task)

    def _resolve_affiliate_link(self, product: dict) -> str:
        """Resolve o link de afiliado do produto.

        Tenta gerar link meli.la (requer login ML). Se falhar, usa tag.
        """
        product_url = product.get("url", "")
        tag = self.affiliate_tag_entry.get().strip()

        # Tentar meli.la se tiver login ML salvo
        if self._ml_logged_in and tag:
            self._safe_offers_log("Gerando link meli.la...")
            meli_link = self._generate_meli_la_link(product_url, tag)
            if meli_link:
                self._safe_offers_log(f"Link meli.la: {meli_link}")
                return meli_link
            self._safe_offers_log(
                "meli.la falhou. Usando link com tag direto."
            )

        if tag:
            link = generate_affiliate_link(product_url, tag)
            self._safe_offers_log(f"Link de afiliado: {link}")
            return link
        return product_url

    def _post_product_to_instagram(self, product: dict) -> bool:
        """Posta um produto no Instagram via Android (LDPlayer). Retorna True se OK."""
        if not self._android:
            self._safe_offers_log(
                "Conecte o Android (LDPlayer) primeiro!"
            )
            return False

        image_url = product.get("image_url", "")
        if not image_url:
            self._safe_offers_log(
                f"Sem imagem: {product.get('title', '')[:40]}"
            )
            return False

        affiliate_link = self._resolve_affiliate_link(product)
        title = product.get("title", "")[:50]

        try:
            self._safe_offers_log(f"Montando story: {title}...")
            build_story_image(
                STORY_BG_PATH,
                STORY_OUT_PATH,
                product_image_url=image_url,
                link="",
                font_regular=FONT_REGULAR,
                font_bold=FONT_BOLD,
            )

            self._safe_offers_log(
                "Postando story via app Android (sticker de link)..."
            )
            res = self._android.post_story(
                STORY_OUT_PATH, link=affiliate_link
            )
            if res.get("ok"):
                self._safe_offers_log(
                    "Story com sticker de link publicado (Android)!"
                )
                return True

            self._safe_offers_log(
                "Falha no story via Android "
                f"(passo: {res.get('step')}, "
                f"detalhe: {res.get('detail')})."
            )
            return False
        except Exception as exc:
            self._safe_offers_log(f"Erro ao postar: {exc}")
            return False

    def _on_post_selected(self) -> None:
        """Posta o produto selecionado no Instagram."""
        if not self._offers_list:
            self._append_offers_log("Busque ofertas primeiro!")
            return

        idx = self._selected_offer_idx
        if idx < 0 or idx >= len(self._offers_list):
            idx = 0

        product = self._offers_list[idx]
        self.post_selected_btn.configure(state="disabled")

        def _post():
            try:
                self._post_product_to_instagram(product)
            finally:
                self.after(
                    0,
                    lambda: self.post_selected_btn.configure(state="normal"),
                )

        threading.Thread(target=_post, daemon=True).start()

    def _on_auto_post_all(self) -> None:
        """Posta todas as ofertas encontradas no Instagram automaticamente."""
        if not self._offers_list:
            self._append_offers_log("Busque ofertas primeiro!")
            return

        if not self._android:
            self._append_offers_log(
                "Conecte o Android (LDPlayer) primeiro!"
            )
            return

        self.auto_post_btn.configure(state="disabled")
        self.search_offers_btn.configure(state="disabled")

        def _post_all():
            total = len(self._offers_list)
            ok = 0
            errs = 0
            try:
                for i, product in enumerate(self._offers_list):
                    self._safe_offers_log(
                        f"--- Postando {i + 1}/{total} ---"
                    )
                    success = self._post_product_to_instagram(product)
                    if success:
                        ok += 1
                    else:
                        errs += 1
                    # Intervalo entre postagens (evitar rate limit)
                    if i < total - 1:
                        self._safe_offers_log("Aguardando 30s...")
                        _time.sleep(30)

                self._safe_offers_log(
                    f"Concluído! {ok} postados, {errs} erros."
                )
            except Exception as exc:
                self._safe_offers_log(f"Erro geral: {exc}")
            finally:
                self.after(
                    0,
                    lambda: self.auto_post_btn.configure(state="normal"),
                )
                self.after(
                    0,
                    lambda: self.search_offers_btn.configure(state="normal"),
                )

        threading.Thread(target=_post_all, daemon=True).start()

    def _on_copy_whatsapp(self) -> None:
        """Copia mensagem formatada para WhatsApp."""
        if not self._current_product:
            self._append_offers_log("Busque ou extraia um produto primeiro!")
            return

        tag = self.affiliate_tag_entry.get().strip()
        link = (
            generate_affiliate_link(self._current_product["url"], tag)
            if tag
            else self._current_product.get("url", "")
        )
        msg = format_whatsapp_message(self._current_product, link)

        self.clipboard_clear()
        self.clipboard_append(msg)
        self._append_offers_log("Mensagem copiada para a área de transferência!")

    # ── Helpers de config ────────────────────────────────────────────────

    def _get_max_follows(self) -> int:
        try:
            return int(self.max_follows_entry.get())
        except ValueError:
            return 20

    def _get_min_delay(self) -> float:
        try:
            return float(self.min_delay_entry.get())
        except ValueError:
            return 3.0

    def _get_max_delay(self) -> float:
        try:
            return float(self.max_delay_entry.get())
        except ValueError:
            return 8.0

    def destroy(self):
        if self._bot:
            self._bot.request_stop()
            self._bot.close_browser()
        if hasattr(self, "_pw") and self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._task_queue.put(None)
        super().destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
