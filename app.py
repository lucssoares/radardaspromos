"""
Instagram Auto-Follow — Aplicativo Desktop.

Interface gráfica para o bot de automação de follow no Instagram.
"""

import threading

import customtkinter as ctk

from bot import InstagramBot

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    """Janela principal do aplicativo."""

    WIDTH = 700
    HEIGHT = 620

    def __init__(self):
        super().__init__()
        self.title("Instagram Auto-Follow Bot")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.resizable(False, False)

        self._bot: InstagramBot | None = None
        self._thread: threading.Thread | None = None
        self._running = False

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Título
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
        subtitle.pack(pady=(0, 14))

        # ── Frame de configuração ────────────────────────────────────────
        config_frame = ctk.CTkFrame(self)
        config_frame.pack(padx=20, pady=(0, 10), fill="x")

        # Perfil alvo
        ctk.CTkLabel(
            config_frame,
            text="Perfil alvo (sem @):",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        self.target_entry = ctk.CTkEntry(
            config_frame, placeholder_text="ex: nike", width=300
        )
        self.target_entry.grid(row=0, column=1, padx=12, pady=(12, 4), sticky="w")

        # Máximo de follows
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

        # Delay
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

        # Filtro info
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
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(padx=20, pady=(0, 6), fill="x")

        self.login_btn = ctk.CTkButton(
            btn_frame,
            text="1. Fazer Login no Instagram",
            command=self._on_login,
            width=220,
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
        self.progress = ctk.CTkProgressBar(self, width=660)
        self.progress.pack(padx=20, pady=(4, 2))
        self.progress.set(0)

        self.progress_label = ctk.CTkLabel(
            self, text="Pronto", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self.progress_label.pack(pady=(0, 4))

        # ── Log ──────────────────────────────────────────────────────────
        self.log_box = ctk.CTkTextbox(self, width=660, height=200, state="disabled")
        self.log_box.pack(padx=20, pady=(0, 14))

    # ── Callbacks ────────────────────────────────────────────────────────

    def _append_log(self, msg: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _safe_log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    def _safe_progress(self, current: int, total: int) -> None:
        def _update():
            if total > 0:
                self.progress.set(current / total)
            self.progress_label.configure(text=f"Seguidos: {current} / {total}")

        self.after(0, _update)

    def _on_login(self) -> None:
        self.login_btn.configure(state="disabled")
        self._safe_log("Abrindo navegador para login...")

        def _login_thread():
            from playwright.sync_api import sync_playwright as start_pw

            self._pw = start_pw().start()
            self._bot = InstagramBot(
                max_follows=self._get_max_follows(),
                min_delay=self._get_min_delay(),
                max_delay=self._get_max_delay(),
                on_log=self._safe_log,
                on_progress=self._safe_progress,
            )
            self._bot.start_browser(self._pw)
            self._bot.open_login_page()

            self._safe_log(
                "Navegador aberto! Faça login no Instagram e clique 'Iniciar Follow'."
            )

            # Aguardar login em background
            def _wait():
                if self._bot.wait_for_login(timeout_sec=300):
                    self.after(0, lambda: self.start_btn.configure(state="normal"))
                    self._safe_log("Login confirmado! Clique em 'Iniciar Follow'.")
                else:
                    self._safe_log("Login não detectado. Tente novamente.")
                    self.after(0, lambda: self.login_btn.configure(state="normal"))

            threading.Thread(target=_wait, daemon=True).start()

        threading.Thread(target=_login_thread, daemon=True).start()

    def _on_start(self) -> None:
        target = self.target_entry.get().strip().lstrip("@").strip("/")
        if not target:
            self._append_log("Preencha o perfil alvo!")
            return

        # Extrair username se for URL
        if "instagram.com/" in target:
            parts = target.split("instagram.com/")
            target = parts[-1].strip("/").split("/")[0]

        self._bot.max_follows = self._get_max_follows()
        self._bot.min_delay = self._get_min_delay()
        self._bot.max_delay = self._get_max_delay()
        self._bot._stop_requested = False

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.login_btn.configure(state="disabled")
        self._running = True

        def _run():
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

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def _on_stop(self) -> None:
        if self._bot:
            self._bot.request_stop()
            self._safe_log("Parando... aguarde a ação atual finalizar.")
        self.stop_btn.configure(state="disabled")

    def _reset_buttons(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.login_btn.configure(state="normal")

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
        super().destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
