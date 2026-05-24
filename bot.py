"""Lógica do bot de automação do Instagram via CDP (Chrome DevTools Protocol).

Conecta ao Chrome real do usuário aberto com --remote-debugging-port.
O Instagram não detecta automação porque é literalmente o Chrome normal.
"""

import logging
import os
import platform
import random
import re
import shutil
import subprocess
import time
import urllib.request

from playwright.sync_api import Browser, BrowserContext, Page, Playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

logger = logging.getLogger("instabot")

CDP_PORT = 9222


def _find_chrome() -> str:
    """Encontra o executável do Chrome no sistema."""
    if platform.system() == "Windows":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
    elif platform.system() == "Darwin":
        mac_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.isfile(mac_path):
            return mac_path
    else:
        found = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if found:
            return found

    return "chrome"


# Diretório persistente para dados do bot (home do usuário)
# Usa a pasta do usuário para que os dados persistam mesmo ao rodar como .exe
_APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".instafollow_bot")
os.makedirs(_APP_DATA_DIR, exist_ok=True)

# Perfil separado para o bot (evita conflito com Chrome já aberto)
BOT_PROFILE_DIR = os.path.join(_APP_DATA_DIR, "chrome_bot_profile")


def _wait_for_cdp_port(port: int, timeout: int = 30, on_log=None) -> bool:
    """Espera a porta CDP ficar disponível."""
    log = on_log or (lambda msg: None)
    start = time.time()
    while time.time() - start < timeout:
        try:
            url = f"http://127.0.0.1:{port}/json/version"
            req = urllib.request.urlopen(url, timeout=2)
            req.close()
            log(f"Porta CDP {port} respondendo!")
            return True
        except Exception:
            pass
        time.sleep(1)
    return False


def launch_chrome_for_login(on_log=None) -> subprocess.Popen:
    """Abre o Chrome SEM porta de debug para o usuário fazer login.

    Abre um Chrome completamente normal (sem nenhuma flag de automação)
    usando o perfil dedicado do bot. O Instagram não detecta nada.
    O login fica salvo no perfil para uso futuro.
    """
    log = on_log or (lambda msg: None)
    chrome_path = _find_chrome()
    log(f"Chrome encontrado: {chrome_path}")

    os.makedirs(BOT_PROFILE_DIR, exist_ok=True)
    log(f"Perfil do bot: {BOT_PROFILE_DIR}")

    cmd = [
        chrome_path,
        f"--user-data-dir={BOT_PROFILE_DIR}",
        "--start-maximized",
        "https://www.instagram.com/",
    ]

    log("Abrindo Chrome normal para login (sem automação)...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    log("Chrome aberto! Faça login no Instagram normalmente.")
    log("Depois de logado, FECHE o Chrome e clique 'Conectar e Iniciar'.")
    return process


def _kill_bot_chrome(on_log=None) -> None:
    """Fecha processos Chrome que usam o perfil do bot."""
    log = on_log or (lambda msg: None)
    if platform.system() == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            log("Chrome fechado.")
            time.sleep(2)
        except Exception:
            log("Não foi possível fechar o Chrome automaticamente.")
    else:
        try:
            subprocess.run(
                ["pkill", "-f", BOT_PROFILE_DIR],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            time.sleep(2)
        except Exception:
            pass


def launch_chrome_with_debug(on_log=None) -> subprocess.Popen:
    """Abre o Chrome COM porta de debug para o bot conectar via CDP.

    Deve ser chamado DEPOIS do login. O perfil já contém os cookies
    de login, então o Instagram abre já logado.
    """
    log = on_log or (lambda msg: None)
    chrome_path = _find_chrome()

    os.makedirs(BOT_PROFILE_DIR, exist_ok=True)

    # Fechar Chrome do bot se estiver aberto
    _kill_bot_chrome(on_log=log)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={BOT_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        "https://www.instagram.com/",
    ]

    log("Abrindo Chrome com porta de debug...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    log("Aguardando porta de debug ficar pronta...")
    if _wait_for_cdp_port(CDP_PORT, timeout=30, on_log=log):
        log("Chrome pronto para conexão!")
    else:
        log("AVISO: Porta de debug não respondeu. A conexão pode falhar.")

    return process


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

    # ── Conectar ao Chrome via CDP ───────────────────────────────────────

    def connect_to_chrome(self, pw: Playwright, max_retries: int = 3) -> Page:
        """Conecta ao Chrome já aberto via CDP, com retries."""
        self._playwright = pw

        for attempt in range(1, max_retries + 1):
            try:
                self._log(f"Conectando ao Chrome via CDP (tentativa {attempt})...")
                self._browser = pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{CDP_PORT}"
                )
                self._context = self._browser.contexts[0]

                if self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = self._context.new_page()

                self._log("Conectado ao Chrome com sucesso!")
                return self._page

            except Exception as exc:
                self._log(f"Tentativa {attempt} falhou: {exc}")
                if attempt < max_retries:
                    self._log("Aguardando antes de tentar novamente...")
                    time.sleep(5)
                else:
                    raise

    def close_browser(self) -> None:
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._context = None

    # ── Login ────────────────────────────────────────────────────────────

    def navigate_to_instagram(self) -> None:
        self._log("Navegando para Instagram...")
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
                if "instagram.com" in url and "/accounts/login" not in url:
                    # Checar se há elementos de usuário logado
                    nav = self._page.locator("nav")
                    if nav.count() > 0:
                        self._delay(2, 3)
                        self._dismiss_popups()
                        self._log("Login detectado com sucesso!")
                        return True
            except Exception:
                pass
            time.sleep(2)
        self._log("Tempo limite para login atingido.")
        return False

    def is_already_logged_in(self) -> bool:
        """Verifica se já está logado (sessão salva)."""
        try:
            url = self._page.url
            if "instagram.com" in url and "/accounts/login" not in url:
                nav = self._page.locator("nav")
                if nav.count() > 0:
                    return True
        except Exception:
            pass
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
                        f"[{stats['followed']}/{self.max_follows}] "
                        f"Seguindo @{username}!"
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
