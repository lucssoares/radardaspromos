"""
Instagram Auto-Follow — Aplicativo Desktop.

Interface gráfica para o bot de automação de follow no Instagram.
Usa CDP para conectar ao Chrome real do usuário.
Inclui dashboard de métricas via Instagram Graph API.
"""

import os
import queue
import sys
import threading
import time as _time

import customtkinter as ctk

from bot import (
    InstagramBot,
    launch_chrome_for_login,
    launch_chrome_with_debug,
    load_follow_log,
    load_followers_list,
)
from instagram_api import InstagramAPI, load_token_data, save_token_data
from mercadolivre import (
    extract_product_data,
    format_instagram_caption,
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


# Fundo padrão usado nos stories (Radar das Promos).
STORY_BG_PATH = resource_path(os.path.join("assets", "story_bg.jpg"))
FONT_REGULAR = resource_path(os.path.join("assets", "DejaVuSans.ttf"))
FONT_BOLD = resource_path(os.path.join("assets", "DejaVuSans-Bold.ttf"))

# Onde a imagem composta do story é salva antes de subir.
_APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".instafollow_bot")
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
        self.title("Instagram Auto-Follow Bot")
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
        self._api: InstagramAPI | None = None
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
        self._try_load_saved_token()

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
            text="Instagram Auto-Follow Bot",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title.pack(pady=(18, 4))

        subtitle = ctk.CTkLabel(
            self,
            text="Siga automaticamente seguidores de um perfil",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        subtitle.pack(pady=(0, 10))

        # ── Abas ──────────────────────────────────────────────────────────
        self.tabview = ctk.CTkTabview(self, width=710, height=580)
        self.tabview.pack(padx=20, pady=(0, 10), fill="both", expand=True)

        self.tab_bot = self.tabview.add("Bot de Follow")
        self.tab_unfollow = self.tabview.add("Limpar Desumildes")
        self.tab_hide_story = self.tabview.add("Ocultar Stories")
        self.tab_offers = self.tabview.add("Radar de Ofertas")
        self.tab_dashboard = self.tabview.add("Dashboard de Métricas")

        self._build_bot_tab()
        self._build_unfollow_tab()
        self._build_hide_story_tab()
        self._build_offers_tab()
        self._build_dashboard_tab()

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
            text="Deixa de seguir quem não te segue de volta após X dias",
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

        ctk.CTkLabel(
            config_frame,
            text="Dias mínimos:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=1, column=0, padx=12, pady=4, sticky="w")

        self.days_entry = ctk.CTkEntry(
            config_frame, placeholder_text="3", width=80
        )
        self.days_entry.insert(0, "3")
        self.days_entry.grid(row=1, column=1, padx=12, pady=4, sticky="w")

        ctk.CTkLabel(
            config_frame,
            text="(só faz unfollow se seguiu há mais de X dias)",
            font=ctk.CTkFont(size=11),
            text_color="#FF9800",
        ).grid(row=2, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="w")

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

        self.unfollow_connect_btn = ctk.CTkButton(
            btn_frame,
            text="1. Conectar Bot",
            command=self._on_connect_bot,
            width=160,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#FF9800",
            hover_color="#F57C00",
        )
        self.unfollow_connect_btn.pack(side="left", padx=(0, 8))

        self.unfollow_btn = ctk.CTkButton(
            btn_frame,
            text="2. Limpar Desumildes",
            command=self._on_unfollow,
            width=200,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
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

    # ── Aba Ocultar Stories ──────────────────────────────────────────────

    def _build_hide_story_tab(self) -> None:
        """Constrói a aba de ocultar stories de seguidores."""
        tab = self.tab_hide_story

        # ── Título ────────────────────────────────────────────────────────
        ctk.CTkLabel(
            tab,
            text="Ocultar Stories",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(pady=(10, 2))

        ctk.CTkLabel(
            tab,
            text="Oculta seus stories de todos, exceto o perfil escolhido",
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

        self.hide_story_my_user_entry = ctk.CTkEntry(
            config_frame, placeholder_text="ex: seu_usuario", width=250
        )
        self.hide_story_my_user_entry.grid(
            row=0, column=1, padx=12, pady=(12, 4), sticky="w"
        )

        # Auto-preencher username
        saved = load_token_data()
        if saved and saved.get("username"):
            self.hide_story_my_user_entry.insert(0, saved["username"])

        ctk.CTkLabel(
            config_frame,
            text="@ permitido (quem PODE ver):",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=1, column=0, padx=12, pady=4, sticky="w")

        self.allowed_user_entry = ctk.CTkEntry(
            config_frame, placeholder_text="ex: perfil_teste", width=250
        )
        self.allowed_user_entry.grid(
            row=1, column=1, padx=12, pady=4, sticky="w"
        )
        # Pré-preenchido com o perfil de teste (pode alterar livremente).
        self.allowed_user_entry.insert(0, "leferreira_99")

        ctk.CTkLabel(
            config_frame,
            text="Apenas este perfil verá seus stories. Todos os outros serão ocultados.",
            font=ctk.CTkFont(size=11),
            text_color="#FF9800",
        ).grid(row=2, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="w")

        # ── Info do cache de seguidores ──────────────────────────────────
        self.followers_cache_label = ctk.CTkLabel(
            tab,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#4CAF50",
        )
        self.followers_cache_label.pack(pady=(0, 4))
        self._update_followers_cache_label()

        # ── Botões ────────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.pack(padx=10, pady=(0, 4), fill="x")

        self.hide_story_connect_btn = ctk.CTkButton(
            btn_frame,
            text="1. Conectar Bot",
            command=self._on_connect_bot,
            width=150,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#FF9800",
            hover_color="#F57C00",
        )
        self.hide_story_connect_btn.pack(side="left", padx=(0, 6))

        self.hide_story_btn = ctk.CTkButton(
            btn_frame,
            text="2. Ocultar Stories",
            command=self._on_hide_story,
            width=170,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#9C27B0",
            hover_color="#7B1FA2",
        )
        self.hide_story_btn.pack(side="left", padx=(0, 6))

        self.unhide_story_btn = ctk.CTkButton(
            btn_frame,
            text="Desocultar",
            command=self._on_unhide_story,
            width=120,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#4CAF50",
            hover_color="#388E3C",
        )
        self.unhide_story_btn.pack(side="left", padx=(0, 6))

        self.recollect_btn = ctk.CTkButton(
            btn_frame,
            text="Recolectar",
            command=self._on_recollect_followers,
            width=120,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#2196F3",
            hover_color="#1976D2",
        )
        self.recollect_btn.pack(side="left", padx=(0, 6))

        self.hide_story_stop_btn = ctk.CTkButton(
            btn_frame,
            text="Parar",
            command=self._on_stop,
            width=70,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#F44336",
            hover_color="#D32F2F",
        )
        self.hide_story_stop_btn.pack(side="left")

        # ── Log ──────────────────────────────────────────────────────────
        self.hide_story_log_box = ctk.CTkTextbox(
            tab, width=660, height=200, state="disabled"
        )
        self.hide_story_log_box.pack(padx=10, pady=(8, 10))

    def _build_offers_tab(self) -> None:
        """Constrói a aba do Radar de Ofertas (ML → Instagram)."""
        tab = self.tab_offers

        ctk.CTkLabel(
            tab,
            text="Radar de Ofertas",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(pady=(10, 2))

        ctk.CTkLabel(
            tab,
            text="Busque promoções do ML e poste automaticamente no Instagram",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(pady=(0, 6))

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

        self.post_story_bg_btn = ctk.CTkButton(
            btn_frame,
            text="Postar Story (Fundo)",
            command=self._on_post_story_bg,
            width=160,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#9C27B0",
            hover_color="#7B1FA2",
        )
        self.post_story_bg_btn.pack(side="left", padx=(0, 6))

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

        # ── Tipo de postagem ─────────────────────────────────────────────
        post_type_frame = ctk.CTkFrame(tab, fg_color="transparent")
        post_type_frame.pack(padx=10, pady=(0, 2), fill="x")

        ctk.CTkLabel(
            post_type_frame, text="Postar como:",
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 8))

        self.post_type_var = ctk.StringVar(value="story")
        ctk.CTkRadioButton(
            post_type_frame, text="Feed",
            variable=self.post_type_var, value="feed",
        ).pack(side="left", padx=(0, 12))
        ctk.CTkRadioButton(
            post_type_frame, text="Story",
            variable=self.post_type_var, value="story",
        ).pack(side="left")

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

    def _build_dashboard_tab(self) -> None:
        """Constrói a aba do dashboard de métricas."""
        tab = self.tab_dashboard

        # ── Token de acesso ──────────────────────────────────────────────
        token_frame = ctk.CTkFrame(tab)
        token_frame.pack(padx=10, pady=(5, 8), fill="x")

        ctk.CTkLabel(
            token_frame,
            text="Access Token do Meta:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        self.token_entry = ctk.CTkEntry(
            token_frame, placeholder_text="Cole seu token aqui", width=400, show="*"
        )
        self.token_entry.grid(row=0, column=1, padx=12, pady=(12, 4), sticky="w")

        # Status do token
        self.token_status_label = ctk.CTkLabel(
            token_frame,
            text="Nenhum token salvo",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.token_status_label.grid(
            row=1, column=0, columnspan=2, padx=12, pady=(0, 4), sticky="w"
        )

        # Token longa duração
        long_token_frame = ctk.CTkFrame(token_frame, fg_color="transparent")
        long_token_frame.grid(
            row=2, column=0, columnspan=2, padx=12, pady=(0, 4), sticky="w"
        )

        ctk.CTkLabel(
            long_token_frame,
            text="App ID:",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(0, 4))

        self.app_id_entry = ctk.CTkEntry(
            long_token_frame, placeholder_text="ID do App", width=140
        )
        self.app_id_entry.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            long_token_frame,
            text="App Secret:",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(0, 4))

        self.app_secret_entry = ctk.CTkEntry(
            long_token_frame, placeholder_text="Secret do App", width=140, show="*"
        )
        self.app_secret_entry.pack(side="left", padx=(0, 8))

        self.long_token_btn = ctk.CTkButton(
            long_token_frame,
            text="Gerar Token 60 dias",
            command=self._on_exchange_token,
            width=140,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color="#FF9800",
            hover_color="#F57C00",
            state="disabled",
        )
        self.long_token_btn.pack(side="left")

        help_label = ctk.CTkLabel(
            token_frame,
            text="Token salvo localmente. App ID/Secret em: Configurações > Básico no painel do Meta",
            font=ctk.CTkFont(size=10),
            text_color="#2196F3",
        )
        help_label.grid(
            row=3, column=0, columnspan=2, padx=12, pady=(0, 10), sticky="w"
        )

        # ── Botões do dashboard ──────────────────────────────────────────
        dash_btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dash_btn_frame.pack(padx=10, pady=(0, 8), fill="x")

        self.connect_api_btn = ctk.CTkButton(
            dash_btn_frame,
            text="Conectar à API",
            command=self._on_connect_api,
            width=140,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2196F3",
            hover_color="#1976D2",
        )
        self.connect_api_btn.pack(side="left", padx=(0, 8))

        self.refresh_btn = ctk.CTkButton(
            dash_btn_frame,
            text="Atualizar Métricas",
            command=self._on_refresh_metrics,
            width=140,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#4CAF50",
            hover_color="#388E3C",
        )
        self.refresh_btn.pack(side="left", padx=(0, 8))

        self.history_btn = ctk.CTkButton(
            dash_btn_frame,
            text="Ver Histórico",
            command=self._on_show_history,
            width=120,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
        )
        self.history_btn.pack(side="left", padx=(0, 8))

        self.clear_token_btn = ctk.CTkButton(
            dash_btn_frame,
            text="Limpar Token",
            command=self._on_clear_token,
            width=110,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#757575",
            hover_color="#616161",
        )
        self.clear_token_btn.pack(side="left")

        # ── Cards de métricas ────────────────────────────────────────────
        cards_frame = ctk.CTkFrame(tab, fg_color="transparent")
        cards_frame.pack(padx=10, pady=(0, 8), fill="x")

        self.card_followers = self._create_metric_card(
            cards_frame, "Seguidores", "---", "#4CAF50"
        )
        self.card_followers.pack(side="left", padx=(0, 8), expand=True, fill="x")

        self.card_following = self._create_metric_card(
            cards_frame, "Seguindo", "---", "#2196F3"
        )
        self.card_following.pack(side="left", padx=(0, 8), expand=True, fill="x")

        self.card_posts = self._create_metric_card(
            cards_frame, "Posts", "---", "#FF9800"
        )
        self.card_posts.pack(side="left", padx=(0, 8), expand=True, fill="x")

        self.card_change = self._create_metric_card(
            cards_frame, "Variação", "---", "#9C27B0"
        )
        self.card_change.pack(side="left", expand=True, fill="x")

        # ── Info do perfil ───────────────────────────────────────────────
        self.profile_info_label = ctk.CTkLabel(
            tab,
            text="Conecte à API para ver métricas do seu perfil",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        self.profile_info_label.pack(pady=(4, 4))

        # ── Log do dashboard ────────────────────────────────────────────
        self.dash_log = ctk.CTkTextbox(tab, width=660, height=110, state="disabled")
        self.dash_log.pack(padx=10, pady=(0, 10))

    def _create_metric_card(
        self, parent, title: str, value: str, color: str
    ) -> ctk.CTkFrame:
        """Cria um card de métrica para o dashboard."""
        card = ctk.CTkFrame(parent, corner_radius=10)

        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(pady=(10, 0))

        label = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=color,
        )
        label.pack(pady=(0, 10))

        card._value_label = label
        return card

    def _update_card(self, card: ctk.CTkFrame, value: str) -> None:
        """Atualiza o valor exibido em um card de métrica."""
        card._value_label.configure(text=value)

    # ── Token persistence ────────────────────────────────────────────────

    def _try_load_saved_token(self) -> None:
        """Tenta carregar token salvo anteriormente."""
        token_data = load_token_data()
        if not token_data:
            return

        token = token_data.get("access_token", "")
        if not token:
            return

        self.token_entry.insert(0, token)

        is_long = token_data.get("is_long_lived", False)
        created = token_data.get("created_at", "")[:19].replace("T", " ")
        ig_user_id = token_data.get("ig_user_id", "")

        if is_long:
            status = f"Token de longa duração salvo ({created})"
            color = "#4CAF50"
        else:
            status = f"Token salvo ({created}) — gere um de longa duração!"
            color = "#FF9800"

        self.token_status_label.configure(text=status, text_color=color)

        # Auto-conectar se tiver IG User ID salvo
        if ig_user_id:
            self._api = InstagramAPI(access_token=token, ig_user_id=ig_user_id)
            self._safe_dash_log("Token salvo encontrado! Clique 'Conectar à API' para carregar métricas.")

    # ── Callbacks ────────────────────────────────────────────────────────

    def _append_log(self, msg: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _safe_log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    def _append_dash_log(self, msg: str) -> None:
        self.dash_log.configure(state="normal")
        self.dash_log.insert("end", msg + "\n")
        self.dash_log.see("end")
        self.dash_log.configure(state="disabled")

    def _safe_dash_log(self, msg: str) -> None:
        self.after(0, self._append_dash_log, msg)

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
                self.after(0, lambda: self.hide_story_btn.configure(state="normal"))
                self.after(0, lambda: self.unhide_story_btn.configure(state="normal"))
                self.after(0, lambda: self.recollect_btn.configure(state="normal"))

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
                    self._safe_offers_log(
                        "Android conectado! Stories com sticker de link "
                        "usarão o app do Instagram."
                    )
                else:
                    self._safe_offers_log(
                        "Falha ao conectar ao Android. "
                        "Verifique se o LDPlayer/Bluestacks está aberto e "
                        "o ADB está ativado nas configurações dele."
                    )
            except Exception as exc:
                self._safe_offers_log(f"Erro ao conectar: {exc}")
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
        """Inicia o processo de limpeza de desumildes."""
        my_user = self.my_username_entry.get().strip().lstrip("@").strip("/")
        if not my_user:
            self._append_unfollow_log("Preencha seu @ (nome de usuário)!")
            return

        if not self._bot:
            self._append_unfollow_log(
                "Bot não conectado! Clique em 'Conectar Bot' primeiro."
            )
            return

        try:
            days = int(self.days_entry.get().strip())
        except (ValueError, AttributeError):
            days = 3

        self._bot._stop_requested = False
        self._bot.on_log = self._safe_unfollow_log
        self.unfollow_btn.configure(state="disabled")
        self.unfollow_stop_btn.configure(state="normal")
        self.unfollow_connect_btn.configure(state="disabled")
        self._running = True

        def _task():
            try:
                self._bot.unfollow_non_followers(my_user, days)
            except Exception as exc:
                self._safe_unfollow_log(f"Erro: {exc}")
            finally:
                self._running = False
                self._bot.on_log = self._safe_log
                self.after(0, self._reset_buttons)
                self.after(0, self._update_follow_count)

        self._submit_task(_task)

    def _on_stop(self) -> None:
        if self._bot:
            self._bot.request_stop()
            self._safe_log("Parando... aguarde a ação atual finalizar.")
            self._safe_unfollow_log("Parando... aguarde a ação atual finalizar.")
            self._safe_hide_story_log("Parando... aguarde a ação atual finalizar.")
        self.stop_btn.configure(state="disabled")
        self.unfollow_stop_btn.configure(state="disabled")
        self.hide_story_stop_btn.configure(state="disabled")

    def _reset_buttons(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.login_btn.configure(state="normal")
        self.connect_btn.configure(state="normal")
        self.unfollow_btn.configure(state="normal")
        self.unfollow_stop_btn.configure(state="disabled")
        self.unfollow_connect_btn.configure(state="normal")
        self.hide_story_btn.configure(state="normal")
        self.unhide_story_btn.configure(state="normal")
        self.hide_story_stop_btn.configure(state="disabled")
        self.hide_story_connect_btn.configure(state="normal")
        self.recollect_btn.configure(state="normal")

    def _update_followers_cache_label(self) -> None:
        """Atualiza o label mostrando quantos seguidores estão no cache."""
        cached = load_followers_list()
        if cached:
            text = f"{len(cached)} seguidores no cache. Pronto para ocultar!"
        else:
            text = (
                "Nenhum seguidor no cache. "
                "Clique em 'Ocultar Stories' para coletar."
            )
        self.followers_cache_label.configure(text=text)

    # ── Ocultar Stories callbacks ───────────────────────────────────────

    def _append_hide_story_log(self, msg: str) -> None:
        self.hide_story_log_box.configure(state="normal")
        self.hide_story_log_box.insert("end", msg + "\n")
        self.hide_story_log_box.see("end")
        self.hide_story_log_box.configure(state="disabled")

    def _safe_hide_story_log(self, msg: str) -> None:
        self.after(0, self._append_hide_story_log, msg)

    def _on_hide_story(self) -> None:
        """Oculta stories de todos exceto o perfil permitido."""
        my_user = (
            self.hide_story_my_user_entry.get().strip().lstrip("@").strip("/")
        )
        allowed = (
            self.allowed_user_entry.get().strip().lstrip("@").strip("/")
        )

        if not my_user:
            self._append_hide_story_log("Preencha seu @ (nome de usuário)!")
            return

        if not allowed:
            self._append_hide_story_log(
                "Preencha o @ do perfil que PODE ver seus stories!"
            )
            return

        if not self._bot:
            self._append_hide_story_log(
                "Bot não conectado! Clique em 'Conectar Bot' primeiro."
            )
            return

        self._bot._stop_requested = False
        self._bot.on_log = self._safe_hide_story_log
        self.hide_story_btn.configure(state="disabled")
        self.unhide_story_btn.configure(state="disabled")
        self.hide_story_stop_btn.configure(state="normal")
        self.hide_story_connect_btn.configure(state="disabled")
        self._running = True

        def _task():
            try:
                self._bot.hide_story_via_settings(
                    allowed_username=allowed,
                    my_username=my_user,
                )
            except Exception as exc:
                self._safe_hide_story_log(f"Erro: {exc}")
            finally:
                self._running = False
                self._bot.on_log = self._safe_log
                self.after(0, self._reset_buttons)
                self.after(0, self._update_followers_cache_label)

        self._submit_task(_task)

    def _on_unhide_story(self) -> None:
        """Remove ocultação de stories de todos os seguidores."""
        if not self._bot:
            self._append_hide_story_log(
                "Bot não conectado! Clique em 'Conectar Bot' primeiro."
            )
            return

        self._bot._stop_requested = False
        self._bot.on_log = self._safe_hide_story_log
        self.hide_story_btn.configure(state="disabled")
        self.unhide_story_btn.configure(state="disabled")
        self.hide_story_stop_btn.configure(state="normal")
        self.hide_story_connect_btn.configure(state="disabled")
        self._running = True

        my_user = (
            self.hide_story_my_user_entry.get().strip().lstrip("@").strip("/")
        )

        def _task():
            try:
                self._bot.unhide_story_from_all(my_username=my_user)
            except Exception as exc:
                self._safe_hide_story_log(f"Erro: {exc}")
            finally:
                self._running = False
                self._bot.on_log = self._safe_log
                self.after(0, self._reset_buttons)

        self._submit_task(_task)

    def _on_recollect_followers(self) -> None:
        """Força a recoleta dos seguidores do Instagram."""
        if not self._bot:
            self._append_hide_story_log(
                "Bot não conectado! Clique em 'Conectar Bot' primeiro."
            )
            return

        my_user = (
            self.hide_story_my_user_entry.get().strip().lstrip("@").strip("/")
        )
        if not my_user:
            self._append_hide_story_log("Preencha seu @ (nome de usuário)!")
            return

        self._bot._stop_requested = False
        self._bot.on_log = self._safe_hide_story_log
        self.recollect_btn.configure(state="disabled")
        self.hide_story_stop_btn.configure(state="normal")
        self._running = True

        def _task():
            try:
                self._bot._collect_followers(my_user)
                self._safe_hide_story_log("Lista de seguidores atualizada!")
            except Exception as exc:
                self._safe_hide_story_log(f"Erro: {exc}")
            finally:
                self._running = False
                self._bot.on_log = self._safe_log
                self.after(0, self._reset_buttons)
                self.after(0, self._update_followers_cache_label)

        self._submit_task(_task)

    # ── Ofertas callbacks ─────────────────────────────────────────────────

    def _append_offers_log(self, msg: str) -> None:
        self.offers_log_box.configure(state="normal")
        self.offers_log_box.insert("end", msg + "\n")
        self.offers_log_box.see("end")
        self.offers_log_box.configure(state="disabled")

    def _safe_offers_log(self, msg: str) -> None:
        self.after(0, self._append_offers_log, msg)

    def _copy_offers_log(self) -> None:
        """Copia todo o log do Radar pra área de transferência."""
        try:
            content = self.offers_log_box.get("1.0", "end").strip()
            self.clipboard_clear()
            self.clipboard_append(content)
            self._append_offers_log("[Log copiado para a área de transferência]")
        except Exception as exc:
            self._append_offers_log(f"[Erro ao copiar log: {exc}]")

    def _clear_offers_log(self) -> None:
        """Limpa o log do Radar."""
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
            "Faça login. A janela fecha sozinha quando detectar o login."
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
                            has_login = "login" in u or "signin" in u
                            if has_login:
                                continue
                            # Página do ML sem login = logou
                            if "mercadolivre" in u or "mercadolibre" in u:
                                return True
                            if "afiliados" in u:
                                return True
                    except Exception:
                        pass
                    return False

                logged_in = False
                for _ in range(150):
                    _time.sleep(2)
                    try:
                        if _any_page_logged_in():
                            logged_in = True
                            break
                    except Exception:
                        break

                if logged_in:
                    _time.sleep(2)
                    self._ml_logged_in = True
                    self._safe_offers_log(
                        "Login ML salvo! Links meli.la serao "
                        "gerados automaticamente."
                    )
                else:
                    self._safe_offers_log(
                        "Login ML nao detectado (janela fechada ou timeout)."
                    )

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
        """Gera link meli.la via UI do linkbuilder (persistent context).

        Navega pro linkbuilder, preenche a URL, clica 'Gerar', lê o resultado.
        Retorna o link meli.la ou None se falhar.
        """
        import re as _re

        try:
            page = self._get_ml_page()
            cur = (page.url or "").lower()
            if "afiliados/linkbuilder" not in cur:
                self._safe_offers_log("  [ml] navegando pro linkbuilder...")
                page.goto(
                    "https://www.mercadolivre.com.br/afiliados/linkbuilder",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                _time.sleep(2)

            # Verificar se está logado
            cur_url = page.url or ""
            self._safe_offers_log(f"  [ml] URL atual: {cur_url[:80]}")
            if "login" in cur_url.lower() or "signin" in cur_url.lower():
                self._safe_offers_log(
                    "  [ml] FALHA: redirecionou pro login (sessão expirada)"
                )
                self._ml_logged_in = False
                return None

            # Preencher campo de URL
            try:
                url_input = page.get_by_role(
                    "textbox",
                    name=_re.compile(r"url|link|insira", _re.IGNORECASE),
                )
                url_input.wait_for(state="visible", timeout=10000)
                url_input.fill("")
                _time.sleep(0.3)
                url_input.fill(product_url)
                self._safe_offers_log("  [ml] URL preenchida no campo.")
            except Exception as exc:
                self._safe_offers_log(f"  [ml] campo de URL não encontrado: {exc}")
                return None

            # Clicar em 'Gerar'
            try:
                gen_btn = page.get_by_role(
                    "button",
                    name=_re.compile(r"gerar|criar|generate", _re.IGNORECASE),
                )
                gen_btn.wait_for(state="visible", timeout=5000)
                gen_btn.click()
                self._safe_offers_log("  [ml] cliquei em 'Gerar'.")
            except Exception as exc:
                self._safe_offers_log(f"  [ml] botão 'Gerar' não encontrado: {exc}")
                return None

            # Aguardar link aparecer (meli.la ou https://)
            _time.sleep(4)
            try:
                link_el = page.get_by_text(_re.compile(r"^https://"))
                link_el.first.wait_for(state="visible", timeout=15000)
                link_text = link_el.first.text_content() or ""
                self._safe_offers_log(f"  [ml] link gerado: {link_text[:80]}")
                if "meli.la" in link_text or "mercadolivre" in link_text:
                    return link_text.strip()
            except Exception:
                pass

            # Fallback: procurar qualquer meli.la no HTML da página
            html = page.content()
            meli = _re.search(r"https?://meli\.la/[^\s\"'<>]+", html)
            if meli:
                self._safe_offers_log(f"  [ml] link (html): {meli.group(0)}")
                return meli.group(0)

            self._safe_offers_log("  [ml] nenhum link gerado.")
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
        """Posta um produto no Instagram. Retorna True se OK."""
        post_type = self.post_type_var.get()
        # Story com sticker vai pelo Android (emulador LDPlayer).
        has_story_engine = self._android or self._bot
        if not self._api and not (post_type == "story" and has_story_engine):
            self._safe_offers_log(
                "Conecte o Android (LDPlayer) ou configure o token "
                "da API na aba Dashboard."
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
            if post_type == "story":
                self._safe_offers_log(f"Montando story: {title}...")
                # Compõe imagem SEM link em texto (o link vai no sticker).
                build_story_image(
                    STORY_BG_PATH,
                    STORY_OUT_PATH,
                    product_image_url=image_url,
                    link="",
                    font_regular=FONT_REGULAR,
                    font_bold=FONT_BOLD,
                )

                # ── Prioridade 1: Android (app real, sticker clicável) ──
                if self._android is not None:
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
                        f"detalhe: {res.get('detail')}). "
                        "Tentando reserva..."
                    )

                # ── Prioridade 2: Navegador web (bot/CDP emulando mobile)
                elif self._bot is not None:
                    res = self._bot.post_story_web(
                        STORY_OUT_PATH, link=affiliate_link
                    )
                    if res.get("ok"):
                        self._safe_offers_log(
                            "Story com sticker de link publicado (web)!"
                        )
                        return True
                    self._safe_offers_log(
                        "Falha no story via navegador "
                        f"(passo: {res.get('step')}). Tentando reserva..."
                    )

                else:
                    self._safe_offers_log(
                        "Sem Android nem Bot conectados — sem sticker. "
                        "Conecte o Android ou Bot. "
                        "Publicando via API (link em texto) por enquanto."
                    )

                # ── Reserva: API (sem sticker, link em texto na imagem) ──
                if not self._api:
                    self._safe_offers_log(
                        "Sem API para a reserva. Configure o token na aba "
                        "Dashboard ou me envie o log acima p/ ajustar."
                    )
                    return False
                build_story_image(
                    STORY_BG_PATH,
                    STORY_OUT_PATH,
                    product_image_url=image_url,
                    link=display_link(affiliate_link),
                    font_regular=FONT_REGULAR,
                    font_bold=FONT_BOLD,
                )
                self._safe_offers_log(f"Postando story (reserva): {title}...")
                result = self._api.publish_story_image(STORY_OUT_PATH)
            else:
                caption = format_instagram_caption(product, affiliate_link)
                self._safe_offers_log(f"Postando no feed: {title}...")
                result = self._api.publish_feed_post(image_url, caption)

            media_id = result.get("id", "")
            self._safe_offers_log(
                f"Publicado! ID: {media_id}"
            )
            return True
        except Exception as exc:
            self._safe_offers_log(f"Erro ao postar: {exc}")
            return False

    def _on_post_story_bg(self) -> None:
        """Publica apenas o fundo do Radar das Promos como story (tela cheia)."""
        if not self._api:
            self._append_offers_log(
                "API não conectada! Configure o token na aba Dashboard."
            )
            return

        if not os.path.isfile(STORY_BG_PATH):
            self._append_offers_log(
                f"Imagem de fundo não encontrada: {STORY_BG_PATH}"
            )
            return

        self.post_story_bg_btn.configure(state="disabled")

        def _post():
            try:
                self._safe_offers_log("Publicando story (fundo)...")
                result = self._api.publish_story_image(STORY_BG_PATH)
                self._safe_offers_log(
                    f"Story publicado! ID: {result.get('id', '')}"
                )
            except Exception as exc:
                self._safe_offers_log(f"Erro ao publicar story: {exc}")
            finally:
                self.after(
                    0,
                    lambda: self.post_story_bg_btn.configure(state="normal"),
                )

        threading.Thread(target=_post, daemon=True).start()

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

        if not self._api:
            self._append_offers_log(
                "API não conectada! Configure o token na aba Dashboard."
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

    # ── Dashboard callbacks ──────────────────────────────────────────────

    def _on_connect_api(self) -> None:
        """Conecta à Instagram Graph API com o token fornecido."""
        token = self.token_entry.get().strip()
        if not token:
            self._safe_dash_log("Cole o Access Token do Meta para conectar.")
            return

        self.connect_api_btn.configure(state="disabled")
        self._safe_dash_log("Conectando à API do Instagram...")

        def _connect():
            try:
                self._api = InstagramAPI(access_token=token)
                ig_user_id = self._api.discover_user_id()
                self._safe_dash_log(f"Conectado! IG User ID: {ig_user_id}")

                # Salvar token localmente
                self._api.save_current_token()
                self._safe_dash_log("Token salvo localmente!")

                self.after(0, lambda: self.token_status_label.configure(
                    text="Token salvo com sucesso!",
                    text_color="#4CAF50",
                ))

                metrics = self._api.record_metrics()
                self._display_metrics(metrics)

                # Preencher @ automaticamente na aba Limpar Desumildes
                username = metrics.get("username", "")
                if username:
                    self.after(0, self._set_username_entry, username)

                self.after(0, lambda: self.refresh_btn.configure(state="normal"))
                self.after(0, lambda: self.history_btn.configure(state="normal"))
                self.after(0, lambda: self.long_token_btn.configure(state="normal"))
                self._safe_dash_log("Métricas carregadas com sucesso!")

            except Exception as exc:
                self._safe_dash_log(f"Erro: {exc}")
                self.after(0, lambda: self.connect_api_btn.configure(state="normal"))

        threading.Thread(target=_connect, daemon=True).start()

    def _on_exchange_token(self) -> None:
        """Troca o token por um de longa duração (60 dias)."""
        if not self._api:
            self._safe_dash_log("Conecte à API primeiro.")
            return

        app_id = self.app_id_entry.get().strip()
        app_secret = self.app_secret_entry.get().strip()

        if not app_id or not app_secret:
            self._safe_dash_log(
                "Preencha o App ID e App Secret. "
                "Encontre em: Painel do Meta > Configurações > Básico"
            )
            return

        self.long_token_btn.configure(state="disabled")
        self._safe_dash_log("Trocando por token de longa duração...")

        def _exchange():
            try:
                result = self._api.exchange_for_long_lived_token(app_id, app_secret)
                already = result.get("already_long_lived", False)
                expires_days = result.get("expires_in", 0) // 86400

                if already:
                    msg = (
                        "Seu token já é de longa duração (60 dias)! "
                        "Salvo localmente. Use 'Renovar' quando "
                        "estiver perto de expirar."
                    )
                else:
                    msg = (
                        f"Token de longa duração gerado! "
                        f"Válido por {expires_days} dias."
                    )
                    self.after(
                        0, self._update_token_display, result["access_token"]
                    )

                self._safe_dash_log(msg)
                self.after(0, lambda: self.token_status_label.configure(
                    text=f"Token de longa duração ({expires_days} dias)",
                    text_color="#4CAF50",
                ))

            except Exception as exc:
                self._safe_dash_log(f"Erro ao trocar token: {exc}")
            finally:
                self.after(0, lambda: self.long_token_btn.configure(state="normal"))

        threading.Thread(target=_exchange, daemon=True).start()

    def _update_token_display(self, new_token: str) -> None:
        """Atualiza o campo de token na UI."""
        self.token_entry.delete(0, "end")
        self.token_entry.insert(0, new_token)

    def _on_clear_token(self) -> None:
        """Limpa o token salvo."""
        from instagram_api import clear_token_data

        clear_token_data()
        self.token_entry.delete(0, "end")
        self._api = None
        self.token_status_label.configure(
            text="Token removido", text_color="gray"
        )
        self.refresh_btn.configure(state="disabled")
        self.history_btn.configure(state="disabled")
        self.long_token_btn.configure(state="disabled")
        self.connect_api_btn.configure(state="normal")
        self._safe_dash_log("Token limpo. Cole um novo token para conectar.")

    def _on_refresh_metrics(self) -> None:
        """Atualiza as métricas do perfil."""
        if not self._api:
            self._safe_dash_log("Conecte à API primeiro.")
            return

        self.refresh_btn.configure(state="disabled")
        self._safe_dash_log("Atualizando métricas...")

        def _refresh():
            try:
                metrics = self._api.record_metrics()
                self._display_metrics(metrics)
                self._safe_dash_log("Métricas atualizadas!")
            except Exception as exc:
                self._safe_dash_log(f"Erro ao atualizar: {exc}")
            finally:
                self.after(0, lambda: self.refresh_btn.configure(state="normal"))

        threading.Thread(target=_refresh, daemon=True).start()

    def _on_show_history(self) -> None:
        """Mostra o histórico de métricas."""
        if not self._api:
            return

        change_data = self._api.get_followers_change()
        history = self._api.get_history()

        self._safe_dash_log("=" * 50)
        self._safe_dash_log(f"Histórico de métricas ({change_data['records']} registros)")
        self._safe_dash_log("-" * 50)

        if change_data["records"] >= 2:
            sign = "+" if change_data["change"] >= 0 else ""
            self._safe_dash_log(
                f"Última variação: {sign}{change_data['change']} seguidores"
            )
            total_sign = "+" if change_data.get("total_change", 0) >= 0 else ""
            self._safe_dash_log(
                f"Variação total: {total_sign}{change_data.get('total_change', 0)} seguidores"
            )
            self._safe_dash_log(
                f"Desde: {change_data.get('first_record', 'N/A')}"
            )

        self._safe_dash_log("-" * 50)

        for entry in history[-10:]:
            ts = entry.get("timestamp", "")[:19].replace("T", " ")
            followers = entry.get("followers_count", 0)
            following = entry.get("follows_count", 0)
            self._safe_dash_log(f"  {ts} | Seg: {followers} | Sdo: {following}")

        if len(history) > 10:
            self._safe_dash_log(f"  ... e mais {len(history) - 10} registros anteriores")

        self._safe_dash_log("=" * 50)

    def _display_metrics(self, metrics: dict) -> None:
        """Atualiza os cards e labels com as métricas."""
        def _update():
            self._update_card(
                self.card_followers,
                f"{metrics.get('followers_count', 0):,}".replace(",", "."),
            )
            self._update_card(
                self.card_following,
                f"{metrics.get('follows_count', 0):,}".replace(",", "."),
            )
            self._update_card(
                self.card_posts,
                f"{metrics.get('media_count', 0):,}".replace(",", "."),
            )

            change_data = self._api.get_followers_change()
            if change_data["records"] >= 2:
                change = change_data["change"]
                sign = "+" if change >= 0 else ""
                self._update_card(self.card_change, f"{sign}{change}")
            else:
                self._update_card(self.card_change, "---")

            username = metrics.get("username", "")
            name = metrics.get("name", "")
            if username:
                self.profile_info_label.configure(
                    text=f"@{username} — {name}",
                    text_color="white",
                )

        self.after(0, _update)

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
