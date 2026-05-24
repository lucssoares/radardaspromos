"""
Instagram Auto-Follow — Aplicativo Desktop.

Interface gráfica para o bot de automação de follow no Instagram.
Usa CDP para conectar ao Chrome real do usuário.
Inclui dashboard de métricas via Instagram Graph API.
"""

import queue
import threading

import customtkinter as ctk

from bot import InstagramBot, launch_chrome_with_debug
from instagram_api import InstagramAPI

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    """Janela principal do aplicativo."""

    WIDTH = 750
    HEIGHT = 680

    def __init__(self):
        super().__init__()
        self.title("Instagram Auto-Follow Bot")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.resizable(False, False)

        self._bot: InstagramBot | None = None
        self._chrome_process = None
        self._running = False
        self._api: InstagramAPI | None = None

        # Fila de tarefas para a worker thread do Playwright
        self._task_queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self._build_ui()

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
        self.tabview = ctk.CTkTabview(self, width=710, height=560)
        self.tabview.pack(padx=20, pady=(0, 10), fill="both", expand=True)

        self.tab_bot = self.tabview.add("Bot de Follow")
        self.tab_dashboard = self.tabview.add("Dashboard de Métricas")

        self._build_bot_tab()
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

        # ── Botões ───────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.pack(padx=10, pady=(0, 6), fill="x")

        self.login_btn = ctk.CTkButton(
            btn_frame,
            text="1. Abrir Chrome e Logar",
            command=self._on_open_chrome,
            width=200,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.login_btn.pack(side="left", padx=(0, 10))

        self.start_btn = ctk.CTkButton(
            btn_frame,
            text="2. Iniciar Follow",
            command=self._on_start,
            width=180,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#4CAF50",
            hover_color="#388E3C",
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = ctk.CTkButton(
            btn_frame,
            text="Parar",
            command=self._on_stop,
            width=100,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#F44336",
            hover_color="#D32F2F",
        )
        self.stop_btn.pack(side="left")

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

        help_label = ctk.CTkLabel(
            token_frame,
            text="Obtenha em: developers.facebook.com/tools/explorer",
            font=ctk.CTkFont(size=11),
            text_color="#2196F3",
            cursor="hand2",
        )
        help_label.grid(
            row=1, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="w"
        )

        # ── Botões do dashboard ──────────────────────────────────────────
        dash_btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dash_btn_frame.pack(padx=10, pady=(0, 8), fill="x")

        self.connect_api_btn = ctk.CTkButton(
            dash_btn_frame,
            text="Conectar à API",
            command=self._on_connect_api,
            width=160,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2196F3",
            hover_color="#1976D2",
        )
        self.connect_api_btn.pack(side="left", padx=(0, 10))

        self.refresh_btn = ctk.CTkButton(
            dash_btn_frame,
            text="Atualizar Métricas",
            command=self._on_refresh_metrics,
            width=160,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            fg_color="#4CAF50",
            hover_color="#388E3C",
        )
        self.refresh_btn.pack(side="left", padx=(0, 10))

        self.history_btn = ctk.CTkButton(
            dash_btn_frame,
            text="Ver Histórico",
            command=self._on_show_history,
            width=140,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
        )
        self.history_btn.pack(side="left")

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
        self.dash_log = ctk.CTkTextbox(tab, width=660, height=140, state="disabled")
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

    def _safe_progress(self, current: int, total: int) -> None:
        def _update():
            if total > 0:
                self.progress.set(current / total)
            self.progress_label.configure(text=f"Seguidos: {current} / {total}")

        self.after(0, _update)

    # ── Bot callbacks ────────────────────────────────────────────────────

    def _on_open_chrome(self) -> None:
        """Abre o Chrome com debug e conecta via CDP."""
        self.login_btn.configure(state="disabled")
        self._safe_log("Abrindo Chrome do bot (pode manter seu Chrome aberto)...")

        def _task():
            # Abrir Chrome com porta de debug
            self._chrome_process = launch_chrome_with_debug(on_log=self._safe_log)

            # Conectar via CDP (na mesma thread que vai executar as ações)
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
                    self._safe_log("Sessão anterior detectada! Já está logado.")
                else:
                    self._safe_log("Faça login no Instagram e clique 'Iniciar Follow'.")

                self.after(0, lambda: self.start_btn.configure(state="normal"))

            except Exception as exc:
                self._safe_log(f"Erro ao conectar: {exc}")
                self._safe_log(
                    "Tente novamente. Se o erro persistir, feche o Chrome "
                    "manualmente e clique no botão de novo."
                )
                self.after(0, lambda: self.login_btn.configure(state="normal"))

        self._submit_task(_task)

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

    def _on_stop(self) -> None:
        if self._bot:
            self._bot.request_stop()
            self._safe_log("Parando... aguarde a ação atual finalizar.")
        self.stop_btn.configure(state="disabled")

    def _reset_buttons(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.login_btn.configure(state="normal")

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

                metrics = self._api.record_metrics()
                self._display_metrics(metrics)

                self.after(0, lambda: self.refresh_btn.configure(state="normal"))
                self.after(0, lambda: self.history_btn.configure(state="normal"))
                self._safe_dash_log("Métricas carregadas com sucesso!")

            except Exception as exc:
                self._safe_dash_log(f"Erro: {exc}")
                self.after(0, lambda: self.connect_api_btn.configure(state="normal"))

        threading.Thread(target=_connect, daemon=True).start()

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
