"""Lógica do bot de automação do Instagram usando Playwright com Chrome real."""

import logging
import os
import random
import re
import time

from playwright.sync_api import Browser, BrowserContext, Page, Playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

logger = logging.getLogger("instabot")

# Pasta para salvar dados do navegador (cookies, sessão, etc.)
USER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_data")


class InstagramBot:
    """Bot para seguir seguidores de um perfil no Instagram."""

    def __init__(
        self,
        *,
        max_follows: int = 20,
        min_delay: float = 3,
        max_delay: float = 8,
        on_log: callable = None,
        on_progress: callable = None,
    ):
        self.max_follows = max_follows
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.on_log = on_log or (lambda msg: None)
        self.on_progress = on_progress or (lambda current, total: None)
        self._stop_requested = False
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def request_stop(self) -> None:
        self._stop_requested = True

    def _log(self, msg: str) -> None:
        logger.info(msg)
        self.on_log(msg)

    def _delay(
        self, min_sec: float | None = None, max_sec: float | None = None
    ) -> None:
        lo = min_sec if min_sec is not None else self.min_delay
        hi = max_sec if max_sec is not None else self.max_delay
        delay = random.uniform(lo, hi)
        time.sleep(delay)

    # ── Browser lifecycle ────────────────────────────────────────────────

    def start_browser(self, pw: Playwright) -> Page:
        """Abre o Chrome real instalado no computador (channel='chrome')."""
        self._playwright = pw

        os.makedirs(USER_DATA_DIR, exist_ok=True)

        self._context = pw.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--window-size=1280,800",
            ],
            ignore_default_args=["--enable-automation"],
        )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()

        # Esconder flag webdriver
        self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        return self._page

    def close_browser(self) -> None:
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        self._browser = None

    # ── Login manual ─────────────────────────────────────────────────────

    def open_login_page(self) -> None:
        self._log("Abrindo Instagram no Chrome...")
        self._page.goto(
            "https://www.instagram.com/",
            wait_until="domcontentloaded",
            timeout=30000,
        )

    def wait_for_login(self, timeout_sec: int = 300) -> bool:
        """Espera o usuário fazer login manualmente. Retorna True se logado."""
        self._log("Faça login no Instagram (você tem 5 minutos)...")
        start = time.time()
        while time.time() - start < timeout_sec:
            if self._stop_requested:
                return False
            try:
                url = self._page.url
                # Detectar que saiu da tela de login
                if "instagram.com" in url and "/accounts/login" not in url:
                    # Verificar se realmente está logado checando elementos da página
                    self._delay(3, 5)
                    self._dismiss_popups()
                    self._log("Login detectado com sucesso!")
                    return True
            except Exception:
                pass
            time.sleep(2)
        self._log("Tempo limite para login atingido.")
        return False

    def _dismiss_popups(self) -> None:
        for _ in range(3):
            try:
                btn = self._page.locator(
                    "button:has-text('Agora não'), "
                    "button:has-text('Not Now'), "
                    "button:has-text('Not now'), "
                    "button:has-text('Ahora no')"
                )
                if btn.count() > 0:
                    btn.first.click()
                    self._delay(1, 2)
            except Exception:
                pass

    # ── Navegar até seguidores ───────────────────────────────────────────

    def open_followers(self, target_profile: str) -> bool:
        self._log(f"Navegando para @{target_profile}...")
        self._page.goto(
            f"https://www.instagram.com/{target_profile}/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        self._delay(2, 3)

        not_found = self._page.locator(
            "h2:has-text('Esta página não está disponível'), "
            "h2:has-text('Sorry, this page')"
        )
        if not_found.count() > 0:
            self._log(f"Perfil @{target_profile} não encontrado.")
            return False

        followers_link = self._page.locator(f'a[href="/{target_profile}/followers/"]')
        if followers_link.count() == 0:
            followers_link = self._page.locator(
                "a:has-text('seguidores'), a:has-text('followers')"
            )
        if followers_link.count() == 0:
            self._log("Não encontrei o link de seguidores. Perfil pode ser privado.")
            return False

        followers_link.first.click()
        self._delay(2, 3)
        self._log("Lista de seguidores aberta!")
        return True

    # ── Checar perfil (filtro seguindo > seguidores) ─────────────────────

    def _parse_count(self, text: str) -> int:
        """Converte textos como '1,234' ou '12.3K' ou '1.2M' em int."""
        text = text.strip().replace(",", "").replace(".", "")
        match = re.search(r"([\d.]+)\s*([KkMm]?)", text)
        if not match:
            return 0
        num = float(match.group(1))
        suffix = match.group(2).upper()
        if suffix == "K":
            num *= 1000
        elif suffix == "M":
            num *= 1_000_000
        return int(num)

    def _check_profile_filter(self, username: str) -> bool:
        """Abre o perfil em nova aba e verifica se seguindo > seguidores."""
        page2 = self._context.new_page()
        try:
            page2.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            self._delay(1, 2)

            header = page2.locator("header section")
            stats_items = header.locator("li")
            if stats_items.count() < 3:
                self._log(f"  @{username}: não consegui ler os dados. Pulando.")
                return False

            followers_text = stats_items.nth(1).text_content()
            following_text = stats_items.nth(2).text_content()

            followers_count = self._parse_count(followers_text)
            following_count = self._parse_count(following_text)

            passes = following_count > followers_count
            status = "Aprovado" if passes else "Reprovado"
            self._log(
                f"  @{username}: {followers_count} seguidores, "
                f"{following_count} seguindo -> {status}"
            )
            return passes
        except (PlaywrightTimeout, Exception) as exc:
            self._log(f"  @{username}: erro ao verificar perfil ({exc}). Pulando.")
            return False
        finally:
            page2.close()

    # ── Scroll na lista de seguidores ────────────────────────────────────

    def _scroll_followers(self) -> None:
        dialog = self._page.locator("div[role='dialog']")
        if dialog.count() > 0:
            scrollable = dialog.locator("div[style*='overflow']")
            if scrollable.count() > 0:
                scrollable.first.evaluate("el => el.scrollTop = el.scrollHeight")

    # ── Extrair usernames da lista ───────────────────────────────────────

    def _get_visible_usernames(self) -> list[str]:
        """Retorna os usernames visíveis na lista de seguidores."""
        dialog = self._page.locator("div[role='dialog']")
        links = dialog.locator("a[href^='/']")
        usernames = set()
        for i in range(links.count()):
            href = links.nth(i).get_attribute("href")
            if href and href.count("/") == 2:
                name = href.strip("/")
                if name and name not in ("explore", "accounts", "direct"):
                    usernames.add(name)
        return list(usernames)

    # ── Follow principal ─────────────────────────────────────────────────

    def follow_users(self, target_profile: str) -> dict:
        """Segue usuários da lista de seguidores com filtro."""
        stats = {"followed": 0, "filtered_out": 0, "errors": 0, "checked": 0}
        already_processed: set[str] = set()

        self._log(f"Iniciando processo (máximo: {self.max_follows} follows)...")
        self._delay(1, 2)

        stale_rounds = 0

        while stats["followed"] < self.max_follows and not self._stop_requested:
            usernames = self._get_visible_usernames()
            new_usernames = [u for u in usernames if u not in already_processed]

            if not new_usernames:
                stale_rounds += 1
                if stale_rounds >= 3:
                    self._log("Não há mais perfis novos na lista.")
                    break
                self._log("Rolando para carregar mais perfis...")
                self._scroll_followers()
                self._delay(2, 3)
                continue

            stale_rounds = 0

            for username in new_usernames:
                if self._stop_requested or stats["followed"] >= self.max_follows:
                    break

                already_processed.add(username)
                if username == target_profile:
                    continue

                stats["checked"] += 1
                self.on_progress(stats["followed"], self.max_follows)

                self._log(f"Verificando @{username}...")
                if not self._check_profile_filter(username):
                    stats["filtered_out"] += 1
                    continue

                if self._follow_single_user(username, stats):
                    self.on_progress(stats["followed"], self.max_follows)

            self._scroll_followers()
            self._delay(2, 3)

        return stats

    def _follow_single_user(self, username: str, stats: dict) -> bool:
        """Segue um único usuário pelo perfil dele."""
        page2 = self._context.new_page()
        try:
            page2.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            self._delay(1, 2)

            follow_btn = page2.locator(
                "header button:has-text('Seguir'):not(:has-text('Seguindo')), "
                "header button:has-text('Follow'):not(:has-text('Following'))"
            )
            if follow_btn.count() > 0:
                btn_text = follow_btn.first.text_content().strip().lower()
                if btn_text in ("seguir", "follow"):
                    follow_btn.first.click()
                    stats["followed"] += 1
                    self._log(
                        f"[{stats['followed']}/{self.max_follows}] Seguindo @{username}!"
                    )
                    self._delay()
                    return True
                else:
                    self._log(f"  @{username}: já está seguindo ou botão indisponível.")
            else:
                self._log(f"  @{username}: botão 'Seguir' não encontrado.")
            return False
        except Exception as exc:
            self._log(f"  Erro ao seguir @{username}: {exc}")
            stats["errors"] += 1
            return False
        finally:
            page2.close()
