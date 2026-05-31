"""Lógica do bot de automação do Instagram via CDP (Chrome DevTools Protocol).

Conecta ao Chrome real do usuário aberto com --remote-debugging-port.
O Instagram não detecta automação porque é literalmente o Chrome normal.
"""

import json
import logging
import os
import platform
import random
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

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

# Registro de follows com data (para regra de unfollow após 3 dias)
FOLLOW_LOG_FILE = os.path.join(_APP_DATA_DIR, "follow_log.json")

# Lista de seguidores coletada (cache para reutilizar)
FOLLOWERS_LIST_FILE = os.path.join(_APP_DATA_DIR, "followers_list.json")


def load_follow_log() -> dict:
    """Carrega o registro de follows. Retorna {username: iso_date_str}."""
    if os.path.isfile(FOLLOW_LOG_FILE):
        with open(FOLLOW_LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_follow_log(log: dict) -> None:
    """Salva o registro de follows."""
    with open(FOLLOW_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def record_follow(username: str) -> None:
    """Registra que um usuário foi seguido agora."""
    log = load_follow_log()
    log[username] = datetime.now(timezone.utc).isoformat()
    save_follow_log(log)


def remove_from_follow_log(username: str) -> None:
    """Remove um usuário do registro de follows."""
    log = load_follow_log()
    log.pop(username, None)


def load_followers_list() -> list[str]:
    """Carrega a lista de seguidores salva."""
    if os.path.isfile(FOLLOWERS_LIST_FILE):
        with open(FOLLOWERS_LIST_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_followers_list(followers: list[str]) -> None:
    """Salva a lista de seguidores."""
    with open(FOLLOWERS_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(followers, f, ensure_ascii=False, indent=2)


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
        """Converte textos como '1,234', '12.3K', '1.2M', '1.234',
        '12,3 mil', '1,2 mi' em int."""
        text = text.strip()
        # Sufixo "mil" (português para K/thousand)
        match = re.search(r"([\d.,]+)\s*mil\b", text, re.IGNORECASE)
        if match:
            num_str = match.group(1).replace(",", ".")
            return int(float(num_str) * 1000)
        # Sufixo "mi" (português para M/million)
        match = re.search(r"([\d.,]+)\s*mi\b", text, re.IGNORECASE)
        if match:
            num_str = match.group(1).replace(",", ".")
            return int(float(num_str) * 1_000_000)
        # Sufixo K/M (inglês)
        match = re.search(r"([\d.,]+)\s*([KkMm])\b", text)
        if match:
            num_str = match.group(1).replace(",", ".")
            num = float(num_str)
            suffix = match.group(2).upper()
            if suffix == "K":
                num *= 1000
            elif suffix == "M":
                num *= 1_000_000
            return int(num)
        # Sem sufixo: número puro (ex: "1,234" ou "1.234" como separador de milhar)
        match = re.search(r"[\d.,]+", text)
        if match:
            num_str = match.group(0)
            num_str = num_str.replace(",", "").replace(".", "")
            return int(num_str) if num_str else 0
        return 0

    def _extract_profile_counts(self, page: Page) -> dict:
        """Extrai seguidores e seguindo lendo o texto visível da página.

        Abordagem simples: lê o innerText do header/main e usa regex
        para encontrar os padrões "X seguidores" e "X seguindo".
        Não depende da estrutura DOM (que muda frequentemente).
        """
        return page.evaluate("""() => {
            const result = {followers: null, following: null, debug: ''};

            // Pegar o texto visível da área do perfil
            const header = document.querySelector('header');
            const main = document.querySelector('main');
            const headerText = header ? header.innerText : '';
            const mainText = main ? main.innerText : '';
            const fullText = headerText + '\\n' + mainText;
            result.debug = 'header: ' + headerText.substring(0, 200).replace(/\\n/g, ' | ');

            // Regex para capturar "NÚMERO seguidores" e "NÚMERO seguindo"
            // Suporta: 1.234, 1,234, 12K, 12.3K, 12,3 mil, 1.2M, 1,2 mi
            const numPattern = '([\\\\d.,]+\\\\s*(?:mil|mi|[KkMm])?)';

            // Português e Inglês
            const fwersMatch = fullText.match(
                new RegExp(numPattern + '\\\\s*(?:seguidores|followers)', 'i')
            );
            const fwingMatch = fullText.match(
                new RegExp(numPattern + '\\\\s*(?:seguindo|following)', 'i')
            );

            if (fwersMatch) result.followers = fwersMatch[1].trim();
            if (fwingMatch) result.following = fwingMatch[1].trim();

            // Fallback: meta tag og:description
            if (!result.followers || !result.following) {
                const meta = document.querySelector(
                    'meta[property="og:description"], meta[name="description"]'
                );
                if (meta) {
                    const content = meta.getAttribute('content') || '';
                    const mf = content.match(
                        new RegExp(numPattern + '\\\\s*(?:Followers|seguidores)', 'i')
                    );
                    const mg = content.match(
                        new RegExp(numPattern + '\\\\s*(?:Following|seguindo)', 'i')
                    );
                    if (mf && !result.followers) result.followers = mf[1].trim();
                    if (mg && !result.following) result.following = mg[1].trim();
                }
            }

            return result;
        }""")

    def _check_profile_filter(self, username: str) -> bool:
        """Abre o perfil em nova aba e verifica se seguindo > seguidores."""
        page2 = self._context.new_page()
        try:
            page2.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="load",
                timeout=20000,
            )
            # Esperar o SPA renderizar (React precisa de tempo)
            self._delay(3, 5)

            # Tentar extrair dados, com retry se não encontrar de primeira
            counts = None
            for attempt in range(3):
                counts = self._extract_profile_counts(page2)
                if counts.get("followers") and counts.get("following"):
                    break
                # Esperar mais um pouco e tentar de novo
                self._delay(2, 3)

            followers_raw = counts.get("followers") if counts else None
            following_raw = counts.get("following") if counts else None

            if not followers_raw or not following_raw:
                self._log(
                    f"  @{username}: não consegui ler os dados "
                    f"({counts.get('debug', '') if counts else 'sem dados'}). "
                    f"Pulando."
                )
                return False

            followers_count = self._parse_count(str(followers_raw))
            following_count = self._parse_count(str(following_raw))

            passes = following_count > followers_count
            status = "Aprovado" if passes else "Reprovado"
            self._log(
                f"  @{username}: {followers_count} seguidores "
                f"(raw: '{followers_raw}'), "
                f"{following_count} seguindo "
                f"(raw: '{following_raw}') -> {status}"
            )
            return passes
        except (PlaywrightTimeout, Exception) as exc:
            self._log(f"  @{username}: erro ao verificar perfil ({exc}). Pulando.")
            return False
        finally:
            page2.close()

    # ── Scroll na lista de seguidores ────────────────────────────────────

    def _scroll_followers(self) -> None:
        """Rola a lista de seguidores para carregar mais perfis."""
        dialog = self._page.locator("div[role='dialog']")
        if dialog.count() == 0:
            return

        # Tentar encontrar o contêiner rolável dentro do dialog
        scrolled = dialog.first.evaluate("""(dlg) => {
            // Procurar o elemento rolável dentro do dialog
            const candidates = dlg.querySelectorAll('div');
            for (const el of candidates) {
                const style = window.getComputedStyle(el);
                const overflowY = style.overflowY;
                if ((overflowY === 'auto' || overflowY === 'scroll')
                    && el.scrollHeight > el.clientHeight) {
                    const before = el.scrollTop;
                    el.scrollBy(0, 600);
                    return el.scrollTop > before;
                }
            }
            return false;
        }""")

        if not scrolled:
            # Fallback: focar no dialog e usar keyboard
            try:
                dialog.first.click()
                self._page.mouse.wheel(0, 600)
            except Exception:
                pass

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
                if stale_rounds >= 5:
                    self._log("Não há mais perfis novos na lista.")
                    break
                self._log(
                    f"Rolando para carregar mais perfis "
                    f"(tentativa {stale_rounds}/5)..."
                )
                self._scroll_followers()
                self._delay(2, 4)
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
                    record_follow(username)
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

    # ── Unfollow (limpeza de desumildes) ──────────────────────────────────

    def _unfollow_user(self, username: str) -> bool:
        """Deixa de seguir um usuário."""
        page2 = self._context.new_page()
        try:
            page2.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="load",
                timeout=20000,
            )
            self._delay(2, 4)

            # Clicar em "Seguindo" / "Following". O Instagram usa tanto
            # <button> quanto <div role="button"> dependendo do layout.
            following_btn = page2.locator(
                "header button:has-text('Seguindo'), "
                "header button:has-text('Following'), "
                "header div[role='button']:has-text('Seguindo'), "
                "header div[role='button']:has-text('Following'), "
                "header button[aria-label='Seguindo'], "
                "header button[aria-label='Following'], "
                "header svg[aria-label='Seguindo'], "
                "header svg[aria-label='Following']"
            )
            if following_btn.count() == 0:
                # Fallback: qualquer botão do header com esse texto.
                following_btn = page2.locator(
                    "button:has-text('Seguindo'), "
                    "button:has-text('Following'), "
                    "div[role='button']:has-text('Seguindo'), "
                    "div[role='button']:has-text('Following')"
                )
            if following_btn.count() == 0:
                self._log(f"  @{username}: botão 'Seguindo' não encontrado.")
                return False

            following_btn.first.click()

            # Esperar o popup de confirmação aparecer.
            try:
                page2.wait_for_selector("div[role='dialog']", timeout=8000)
            except Exception:
                pass
            self._delay(1, 2)

            # Confirmar unfollow no popup. O botão fica dentro do dialog
            # e pode ser <button> ou <div role="button">.
            unfollow_btn = page2.locator(
                "div[role='dialog'] button:has-text('Deixar de seguir'), "
                "div[role='dialog'] button:has-text('Unfollow'), "
                "div[role='dialog'] [role='button']:has-text('Deixar de seguir'), "
                "div[role='dialog'] [role='button']:has-text('Unfollow')"
            )
            if unfollow_btn.count() == 0:
                # Fallback fora do seletor de dialog.
                unfollow_btn = page2.locator(
                    "button:has-text('Deixar de seguir'), "
                    "button:has-text('Unfollow'), "
                    "[role='button']:has-text('Deixar de seguir'), "
                    "[role='button']:has-text('Unfollow')"
                )

            if unfollow_btn.count() > 0:
                unfollow_btn.first.click()
                remove_from_follow_log(username)
                self._delay(1, 2)
                return True

            # Última tentativa via JavaScript.
            clicked = page2.evaluate("""() => {
                const els = document.querySelectorAll(
                    'div[role="dialog"] button, '
                    + 'div[role="dialog"] [role="button"], '
                    + 'button, [role="button"]'
                );
                for (const el of els) {
                    const t = (el.innerText || '').toLowerCase().trim();
                    if (t === 'deixar de seguir' || t === 'unfollow') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                remove_from_follow_log(username)
                self._delay(1, 2)
                return True

            self._log(f"  @{username}: popup de unfollow não apareceu.")
            return False
        except Exception as exc:
            self._log(f"  Erro ao deixar de seguir @{username}: {exc}")
            return False
        finally:
            page2.close()

    def _collect_following_list(self, my_username: str) -> list[str]:
        """Abre o perfil, clica em 'Seguindo' e coleta todos os usernames."""
        self._log(f"Navegando para @{my_username}...")
        self._page.goto(
            f"https://www.instagram.com/{my_username}/",
            wait_until="load",
            timeout=30000,
        )
        self._delay(2, 3)

        # Clicar no link "seguindo"
        following_link = self._page.locator(
            f'a[href="/{my_username}/following/"]'
        )
        if following_link.count() == 0:
            following_link = self._page.locator(
                "a:has-text('seguindo'), a:has-text('following')"
            )
        if following_link.count() == 0:
            self._log("Não encontrei o link de 'seguindo'.")
            return []

        following_link.first.click()
        self._delay(2, 3)
        self._log("Lista de 'seguindo' aberta! Coletando usernames...")

        # Coletar usernames com scroll
        all_usernames: set[str] = set()
        stale_rounds = 0

        while stale_rounds < 5 and not self._stop_requested:
            before_count = len(all_usernames)
            new = self._get_visible_usernames()
            all_usernames.update(new)

            if len(all_usernames) == before_count:
                stale_rounds += 1
            else:
                stale_rounds = 0
                self._log(f"  {len(all_usernames)} perfis coletados...")

            self._scroll_followers()
            self._delay(1, 2)

        # Remover o próprio username
        all_usernames.discard(my_username)

        self._log(f"Total: {len(all_usernames)} perfis coletados.")

        # Fechar dialog
        self._page.keyboard.press("Escape")
        self._delay(1, 2)

        return list(all_usernames)

    def unfollow_non_followers(
        self, my_username: str, days_threshold: int = 3
    ) -> dict:
        """Deixa de seguir quem não segue de volta após X dias.

        Método confiável: coleta a lista de quem você SEGUE e a lista de
        quem TE SEGUE, e compara os dois conjuntos. Só faz unfollow de
        quem NÃO está na sua lista de seguidores (não te segue de volta).

        A regra dos X dias é aplicada via follow_log.json: na primeira
        execução todos os 'seguindo' são registrados com a data de hoje;
        nas próximas, só são removidos os que já passaram do prazo.
        """
        stats = {"unfollowed": 0, "follows_back": 0, "too_recent": 0,
                 "errors": 0, "checked": 0, "mapped": 0}
        my_user = my_username.lower().strip().lstrip("@").strip("/")
        follow_log = load_follow_log()
        self._stop_requested = False

        self._log(f"Iniciando limpeza (regra: {days_threshold} dias)...")

        # ── 1. Coletar quem você segue ────────────────────────────────
        self._log("Coletando quem você segue...")
        following_list = self._collect_following_list(my_user)
        if not following_list:
            self._log("Não consegui coletar a lista de 'seguindo'.")
            return stats
        following_norm = {u.lower().strip().lstrip("@") for u in following_list}
        following_norm.discard(my_user)

        # ── 2. Primeira execução: mapear datas e sair ─────────────────
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        if len(follow_log) < 5:
            for u in following_norm:
                if u not in follow_log:
                    follow_log[u] = now_str
                    stats["mapped"] += 1
            save_follow_log(follow_log)
            self._log(
                f"Mapeamento concluído! {stats['mapped']} perfis registrados."
            )
            self._log(
                f"Execute novamente após {days_threshold} dias para "
                f"fazer a limpeza."
            )
            self._log("=" * 50)
            return stats

        if self._stop_requested:
            return stats

        # ── 3. Coletar quem te segue (lista confiável) ────────────────
        self._log("Coletando quem te segue (seus seguidores)...")
        followers_list = self._collect_followers(my_user)
        if not followers_list:
            self._log(
                "Não consegui coletar seus seguidores. "
                "Abortando por segurança (para não dar unfollow errado)."
            )
            return stats
        followers_norm = {
            u.lower().strip().lstrip("@") for u in followers_list
        }

        # ── 4. Determinar candidatos ──────────────────────────────────
        # Candidato = você segue + ele NÃO te segue + passou do prazo.
        candidates = []
        for username in following_norm:
            if username in followers_norm:
                stats["follows_back"] += 1
                continue

            date_str = follow_log.get(username)
            if not date_str:
                # Não sabemos quando começamos a seguir: registra agora
                # e trata como recente (não faz unfollow nesta execução).
                follow_log[username] = now_str
                stats["too_recent"] += 1
                continue

            try:
                followed_at = datetime.fromisoformat(date_str)
                days_ago = (now - followed_at).days
            except (ValueError, TypeError):
                days_ago = 999

            if days_ago < days_threshold:
                stats["too_recent"] += 1
                continue

            candidates.append((username, days_ago))

        save_follow_log(follow_log)

        self._log(
            f"  {len(following_norm)} seguindo | "
            f"{len(followers_norm)} seguidores"
        )
        self._log(
            f"  {stats['follows_back']} seguem de volta (mantidos) | "
            f"{stats['too_recent']} recentes (mantidos)"
        )
        self._log(
            f"  {len(candidates)} candidatos para unfollow "
            f"(não te seguem e já passaram de {days_threshold} dias)."
        )

        # ── 5. Executar unfollow ──────────────────────────────────────
        for username, days_ago in candidates:
            if self._stop_requested:
                break

            stats["checked"] += 1
            self.on_progress(stats["unfollowed"], len(candidates))
            self._log(
                f"@{username}: não te segue (seguido há {days_ago} dias). "
                f"Deixando de seguir..."
            )
            if self._unfollow_user(username):
                stats["unfollowed"] += 1
                remove_from_follow_log(username)
                self._log(
                    f"  [{stats['unfollowed']}] Unfollow @{username}!"
                )
            else:
                stats["errors"] += 1

            self._delay()

        self._log("=" * 50)
        self._log("Limpeza finalizada!")
        self._log(f"  Unfollowed: {stats['unfollowed']}")
        self._log(f"  Seguem de volta: {stats['follows_back']}")
        self._log(f"  Muito recentes: {stats['too_recent']}")
        self._log(f"  Erros: {stats['errors']}")
        self._log("=" * 50)
        return stats

    # ── Ocultar stories via tela de configurações ─────────────────────

    def _click_by_text(self, needles: list[str], exact: bool = False) -> str:
        """Clica no primeiro elemento clicável cujo texto bate com um needle.

        Retorna o texto clicado (ou string vazia se nada foi encontrado).
        """
        return self._page.evaluate(
            """(args) => {
                const {needles, exact} = args;
                const sel = 'a,button,[role="button"],[role="link"],'
                    + '[role="menuitem"],span,div';
                const els = document.querySelectorAll(sel);
                let best = null;
                let bestLen = Infinity;
                for (const el of els) {
                    // Ignora elementos invisíveis.
                    if (!el.offsetParent && el.tagName !== 'BODY') continue;
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (!t) continue;
                    const ok = exact
                        ? needles.includes(t)
                        : needles.some(n => t.includes(n));
                    if (ok && t.length < bestLen) {
                        best = el;
                        bestLen = t.length;
                    }
                }
                if (!best) return '';
                const label = (best.innerText || '').trim()
                    .toLowerCase().slice(0, 60);
                // Sobe até um ancestral clicável, se houver.
                let target = best;
                for (let i = 0; i < 4 && target; i++) {
                    const role = target.getAttribute
                        ? target.getAttribute('role') : null;
                    if (target.tagName === 'A' || target.tagName === 'BUTTON'
                        || role === 'button' || role === 'link'
                        || role === 'menuitem' || role === 'tab') {
                        break;
                    }
                    target = target.parentElement;
                }
                (target || best).click();
                return label;
            }""",
            {"needles": needles, "exact": exact},
        )

    def _click_options_menu(self) -> None:
        """Abre o menu 'Opções' (engrenagem/hambúrguer) no perfil/sidebar."""
        clicked = self._page.evaluate(
            """() => {
                // Procura ícones de opções/configurações por aria-label.
                const labels = ['opções', 'opcoes', 'options',
                    'configurações', 'configuracoes', 'settings', 'mais',
                    'more'];
                const svgs = document.querySelectorAll('svg[aria-label]');
                for (const s of svgs) {
                    const l = (s.getAttribute('aria-label') || '')
                        .toLowerCase();
                    if (labels.some(x => l.includes(x))) {
                        let t = s;
                        for (let i = 0; i < 4 && t; i++) {
                            const role = t.getAttribute
                                ? t.getAttribute('role') : null;
                            if (t.tagName === 'BUTTON' || t.tagName === 'A'
                                || role === 'button' || role === 'link') {
                                break;
                            }
                            t = t.parentElement;
                        }
                        (t || s).click();
                        return l;
                    }
                }
                return '';
            }"""
        )
        if clicked:
            self._log(f"  Abrindo menu: '{clicked}'")

    def _open_hide_story_from_list(self, my_user: str = "") -> bool:
        """Navega até a tela 'Ocultar story e transmissão ao vivo de'.

        Segue o caminho do Instagram web:
        Perfil → Opções → Configurações e privacidade →
        Ocultar story e transmissão ao vivo → ...de.
        """
        # 1. Ir para o próprio perfil (ou home se não tiver o @).
        dest = (
            f"https://www.instagram.com/{my_user}/"
            if my_user else "https://www.instagram.com/"
        )
        self._page.goto(dest, wait_until="load", timeout=30000)
        self._delay(3, 5)

        # 2. Abrir o menu: clicar em "Configurações" (ou "Mais"/ícone).
        clicked = self._click_by_text(
            ["configurações", "configuracoes", "mais", "more", "settings"],
            exact=True,
        )
        if not clicked:
            self._click_options_menu()
        else:
            self._log(f"  Abrindo: '{clicked}'")
        self._delay(1.5, 2.5)

        # 3. Dentro do menu, clicar em "Configurações"
        #    (item exato do menu, não o container).
        clicked = self._click_by_text(
            [
                "configurações",
                "configuracoes",
                "configurações e privacidade",
                "configuracoes e privacidade",
                "settings and privacy",
                "settings",
            ],
            exact=True,
        )
        self._log(f"  Menu: '{clicked}'")
        self._delay(2.5, 3.5)

        # 4. "Ocultar story e transmissão ao vivo".
        clicked = self._click_by_text(["ocultar story", "hide story"])
        self._log(f"  Configuração: '{clicked}'")
        self._delay(2, 3)

        # 5. "Ocultar story e transmissão ao vivo de" (abre a lista).
        self._click_by_text(
            [
                "ocultar story e transmissão ao vivo de",
                "ocultar story e transmissao ao vivo de",
                "ocultar story de",
                "ocultar story e live de",
                "hide story and live from",
                "hide story from",
            ]
        )
        self._delay(2.5, 3.5)

        # Verifica se a lista de pessoas apareceu (campo de busca/linhas).
        return self._page.evaluate(
            """() => {
                const txt = (document.body.innerText || '').toLowerCase();
                const hasSearch = !!document.querySelector(
                    "input[placeholder*='Pesquis'], "
                    + "input[placeholder*='Search'], "
                    + "input[aria-label*='Pesquis'], "
                    + "input[aria-label*='Search']"
                );
                return hasSearch
                    || txt.includes('ocultar story e transmiss')
                    || txt.includes('hide story and live');
            }"""
        )

    def _process_hide_story_rows(self, allowed: str) -> dict:
        """Marca/desmarca pessoas na lista de 'Ocultar story de'.

        Seleciona (oculta) todos exceto o perfil permitido. Desmarca o
        perfil permitido se estiver selecionado. Roda em JavaScript sobre
        as linhas atualmente renderizadas.
        """
        return self._page.evaluate(
            """(allowed) => {
                const out = {selected: 0, kept: 0, unselected_allowed: 0,
                             rows: 0, sample: ''};
                // Linhas: elementos que contêm um link de perfil OU um
                // checkbox/circle de seleção.
                const anchors = Array.from(
                    document.querySelectorAll('a[href^="/"][role], a[href^="/"]')
                );
                const seen = new Set();
                for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/^\\/([A-Za-z0-9._]+)\\/?$/);
                    if (!m) continue;
                    const username = m[1].toLowerCase();
                    if (seen.has(username)) continue;
                    seen.add(username);

                    // Sobe até a linha (ancestral com um botão/checkbox).
                    let row = a;
                    for (let i = 0; i < 6 && row; i++) {
                        if (row.querySelector(
                            'button, [role="button"], [role="checkbox"], '
                            + 'svg[aria-label]')) {
                            break;
                        }
                        row = row.parentElement;
                    }
                    if (!row) continue;
                    out.rows++;
                    if (!out.sample) {
                        out.sample = (row.innerText || '').slice(0, 40)
                            + ' :: ' + row.outerHTML.slice(0, 180);
                    }

                    // Detecta estado de seleção pelo aria-label do ícone.
                    const labelled = row.querySelector('[aria-label]');
                    const lbl = labelled
                        ? (labelled.getAttribute('aria-label') || '')
                            .toLowerCase()
                        : '';
                    const isSelected = lbl.includes('selecionad')
                        || lbl.includes('selected')
                        || lbl.includes('marcad')
                        || row.querySelector('[aria-checked="true"]') !== null;

                    const toggle = row.querySelector(
                        'button, [role="button"], [role="checkbox"]'
                    ) || row;

                    if (username === allowed) {
                        if (isSelected) {
                            toggle.click();
                            out.unselected_allowed++;
                        } else {
                            out.kept++;
                        }
                    } else {
                        if (!isSelected) {
                            toggle.click();
                            out.selected++;
                        } else {
                            out.kept++;
                        }
                    }
                }
                return out;
            }""",
            allowed,
        )

    def _scroll_hide_story_list(self) -> None:
        """Rola a lista de pessoas da tela de ocultar story."""
        self._page.evaluate(
            """() => {
                const dlg = document.querySelector("div[role='dialog']")
                    || document.body;
                let sc = null;
                const all = dlg.querySelectorAll('div');
                for (const d of all) {
                    const st = getComputedStyle(d);
                    if ((st.overflowY === 'auto' || st.overflowY === 'scroll')
                        && d.scrollHeight > d.clientHeight + 50) {
                        sc = d; break;
                    }
                }
                if (sc) {
                    sc.scrollTop = sc.scrollHeight;
                } else {
                    window.scrollBy(0, 800);
                }
            }"""
        )

    def hide_story_via_settings(
        self, allowed_username: str, my_username: str = ""
    ) -> dict:
        """Oculta stories via tela de configurações (todos exceto um).

        Usa SOMENTE a tela nativa 'Ocultar story e transmissão ao vivo de'
        (Perfil → Opções → Configurações → Ocultar story → ...de).
        Não visita perfil por perfil.
        """
        stats = {"hidden": 0, "kept": 0, "errors": 0, "rows": 0}
        allowed = allowed_username.lower().strip().lstrip("@").strip("/")
        my_user = my_username.lower().strip().lstrip("@").strip("/")
        self._stop_requested = False
        self._log(
            f"Abrindo configurações de 'Ocultar story' (exceto @{allowed})..."
        )

        try:
            opened = self._open_hide_story_from_list(my_user)
        except Exception as exc:
            self._log(f"Erro ao abrir configurações: {exc}")
            opened = False

        if not opened:
            self._log(
                "Não consegui abrir a tela 'Ocultar story de'. "
                "Verifique se está logado e me envie o que apareceu no log "
                "acima (os textos de 'Menu'/'Configuração') para eu ajustar "
                "a navegação."
            )
            return stats

        self._log("Tela de seleção aberta. Marcando perfis...")
        self._delay(1, 2)

        stale = 0
        total_selected = 0
        first_dump = True
        while stale < 6 and not self._stop_requested:
            res = self._process_hide_story_rows(allowed)
            stats["rows"] = max(stats["rows"], res.get("rows", 0))

            if first_dump and res.get("sample"):
                self._log(f"  [debug linha] {res['sample']}")
                first_dump = False

            newly = res.get("selected", 0)
            total_selected += newly
            if res.get("unselected_allowed"):
                self._log(f"  @{allowed} desmarcado (poderá ver os stories).")

            if newly == 0:
                stale += 1
            else:
                stale = 0
                self._log(f"  {total_selected} perfis marcados para ocultar...")

            self._scroll_hide_story_list()
            self._delay(1.5, 2.5)

        stats["hidden"] = total_selected
        self._log("=" * 50)
        self._log("Configuração concluída!")
        self._log(f"  Marcados para ocultar: {stats['hidden']}")
        self._log(f"  Linhas vistas: {stats['rows']}")
        if stats["hidden"] == 0:
            self._log(
                "Nenhum perfil foi marcado. Pode ser que os seletores "
                "precisem de ajuste — me envie o '[debug linha]' acima."
            )
        self._log("=" * 50)
        return stats

    # ── Ocultar stories de todos exceto um perfil (perfil-a-perfil) ────

    def _hide_story_from_user(self, username: str) -> bool:
        """Visita o perfil e clica em '...' → 'Ocultar seu story'.

        Retorna True se conseguiu ocultar, False caso contrário.
        """
        try:
            self._page.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="load",
                timeout=20000,
            )
            self._delay(3, 5)

            # Clicar no menu "..." (três pontos)
            dots_btn = self._page.locator(
                "svg[aria-label='Opções'], "
                "svg[aria-label='Options'], "
                "div[role='button'] svg circle"
            )

            if dots_btn.count() == 0:
                # Fallback: procurar pelo botão que contém os 3 pontos
                dots_btn = self._page.locator(
                    "button:has(svg circle), "
                    "div[role='button']:has(svg circle)"
                )

            if dots_btn.count() == 0:
                self._log(f"  @{username}: menu '...' não encontrado.")
                return False

            dots_btn.first.click()
            self._delay(1.5, 2.5)

            # Procurar "Ocultar seu story" / "Hide your story"
            hide_option = self._page.locator(
                "button:has-text('Ocultar seu story'), "
                "button:has-text('Ocultar sua história'), "
                "button:has-text('Hide your story')"
            )

            if hide_option.count() == 0:
                # Tentar via JavaScript
                found = self._page.evaluate("""() => {
                    const buttons = document.querySelectorAll(
                        'button, div[role="button"]'
                    );
                    for (const btn of buttons) {
                        const text = (btn.innerText || '').toLowerCase();
                        if (text.includes('ocultar seu story')
                            || text.includes('ocultar sua hist')
                            || text.includes('hide your story')) {
                            btn.click();
                            return 'found';
                        }
                    }
                    // Verificar se já está oculto
                    for (const btn of buttons) {
                        const text = (btn.innerText || '').toLowerCase();
                        if (text.includes('exibir seu story')
                            || text.includes('mostrar seu story')
                            || text.includes('unhide your story')
                            || text.includes('show your story')) {
                            return 'already_hidden';
                        }
                    }
                    return 'not_found';
                }""")

                if found == "already_hidden":
                    self._log(f"  @{username}: já oculto.")
                    # Fechar o menu
                    self._page.keyboard.press("Escape")
                    self._delay(0.5, 1)
                    return True
                if found == "not_found":
                    self._log(
                        f"  @{username}: opção 'Ocultar story' "
                        f"não encontrada no menu."
                    )
                    self._page.keyboard.press("Escape")
                    self._delay(0.5, 1)
                    return False
                # found == 'found' → já clicou
            else:
                hide_option.first.click()

            self._delay(1, 2)
            return True

        except Exception as exc:
            self._log(f"  Erro com @{username}: {exc}")
            return False

    def _unhide_story_from_user(self, username: str) -> bool:
        """Visita o perfil e clica em '...' → 'Exibir seu story'.

        Retorna True se conseguiu desocultar, False caso contrário.
        """
        try:
            self._page.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="load",
                timeout=20000,
            )
            self._delay(3, 5)

            # Clicar no menu "..." (três pontos)
            dots_btn = self._page.locator(
                "svg[aria-label='Opções'], "
                "svg[aria-label='Options'], "
                "div[role='button'] svg circle"
            )

            if dots_btn.count() == 0:
                dots_btn = self._page.locator(
                    "button:has(svg circle), "
                    "div[role='button']:has(svg circle)"
                )

            if dots_btn.count() == 0:
                self._log(f"  @{username}: menu '...' não encontrado.")
                return False

            dots_btn.first.click()
            self._delay(1.5, 2.5)

            # Procurar "Exibir seu story" / "Unhide your story"
            found = self._page.evaluate("""() => {
                const buttons = document.querySelectorAll(
                    'button, div[role="button"]'
                );
                for (const btn of buttons) {
                    const text = (btn.innerText || '').toLowerCase();
                    if (text.includes('exibir seu story')
                        || text.includes('mostrar seu story')
                        || text.includes('unhide your story')
                        || text.includes('show your story')) {
                        btn.click();
                        return 'found';
                    }
                }
                return 'not_found';
            }""")

            if found == "not_found":
                self._log(
                    f"  @{username}: opção de desocultar "
                    f"não encontrada (pode já estar visível)."
                )
                self._page.keyboard.press("Escape")
                self._delay(0.5, 1)
                return False

            self._delay(1, 2)
            return True

        except Exception as exc:
            self._log(f"  Erro com @{username}: {exc}")
            return False

    def _collect_followers(self, my_username: str) -> list[str]:
        """Coleta todos os seguidores e salva em cache."""
        my_user = my_username.lower().strip().lstrip("@")

        self._page.goto(
            f"https://www.instagram.com/{my_user}/",
            wait_until="load",
            timeout=30000,
        )
        self._delay(2, 3)

        followers_link = self._page.locator(
            f'a[href="/{my_user}/followers/"]'
        )
        if followers_link.count() == 0:
            followers_link = self._page.locator(
                "a:has-text('seguidor'), a:has-text('follower')"
            )
        if followers_link.count() == 0:
            self._log("Não encontrei o link de seguidores.")
            return []

        followers_link.first.click()
        self._delay(2, 3)
        self._log("Lista de seguidores aberta! Coletando...")

        all_followers: set[str] = set()
        stale_rounds = 0

        while stale_rounds < 5 and not self._stop_requested:
            before = len(all_followers)
            new = self._get_visible_usernames()
            all_followers.update(new)

            if len(all_followers) == before:
                stale_rounds += 1
            else:
                stale_rounds = 0
                self._log(f"  {len(all_followers)} seguidores coletados...")

            self._scroll_followers()
            self._delay(1, 2)

        all_followers.discard(my_user)
        self._page.keyboard.press("Escape")
        self._delay(1, 2)

        result = list(all_followers)
        save_followers_list(result)
        self._log(
            f"Total: {len(result)} seguidores coletados e salvos em cache."
        )
        return result

    def hide_story_from_all_except(
        self,
        allowed_username: str,
        my_username: str = "",
        force_collect: bool = False,
    ) -> dict:
        """Oculta stories de todos os seguidores exceto o perfil permitido.

        Usa lista de seguidores em cache se disponível. Se não houver,
        coleta do Instagram e salva para próximas execuções.
        """
        stats = {"hidden": 0, "skipped": 0, "already": 0, "errors": 0}
        allowed = allowed_username.lower().strip().lstrip("@")

        self._log(f"Ocultando stories de todos exceto @{allowed}...")
        self._stop_requested = False

        if not my_username:
            self._log("Username não fornecido. Informe seu @.")
            return stats

        my_user = my_username.lower().strip().lstrip("@")

        # Usar cache ou coletar novamente
        cached = load_followers_list()
        if cached and not force_collect:
            all_followers = [
                u for u in cached
                if u.lower() != my_user and u.lower() != allowed
            ]
            self._log(
                f"Usando lista salva ({len(cached)} seguidores no cache)."
            )
        else:
            self._log("Coletando lista de seguidores do Instagram...")
            collected = self._collect_followers(my_user)
            if not collected:
                return stats
            all_followers = [
                u for u in collected
                if u.lower() != my_user and u.lower() != allowed
            ]

        self._log(
            f"{len(all_followers)} seguidores para ocultar "
            f"(mantendo @{allowed})."
        )

        if not all_followers:
            self._log("Nenhum seguidor para ocultar.")
            return stats

        self._log("Visitando cada perfil para ocultar stories...")
        total = len(all_followers)

        for i, username in enumerate(all_followers):
            if self._stop_requested:
                self._log("Parado pelo usuário.")
                break

            self._log(f"[{i + 1}/{total}] Ocultando @{username}...")

            if self._hide_story_from_user(username):
                stats["hidden"] += 1
            else:
                stats["errors"] += 1

            self._delay(2, 4)

        self._log("=" * 50)
        self._log("Ocultação de stories finalizada!")
        self._log(f"  Ocultados: {stats['hidden']}")
        self._log(f"  Já ocultos/pulados: {stats['skipped']}")
        self._log(f"  Erros: {stats['errors']}")
        self._log("=" * 50)
        return stats

    def unhide_story_from_all(
        self, my_username: str = "", force_collect: bool = False,
    ) -> dict:
        """Remove a ocultação visitando cada perfil e clicando em desocultar.

        Usa lista de seguidores em cache se disponível.
        """
        stats = {"unhidden": 0, "skipped": 0, "errors": 0}

        self._log("Removendo ocultação de stories de todos...")
        self._stop_requested = False

        if not my_username:
            self._log("Username não fornecido. Informe seu @.")
            return stats

        my_user = my_username.lower().strip().lstrip("@")

        cached = load_followers_list()
        if cached and not force_collect:
            all_followers = [u for u in cached if u.lower() != my_user]
            self._log(
                f"Usando lista salva ({len(cached)} seguidores no cache)."
            )
        else:
            self._log("Coletando lista de seguidores do Instagram...")
            collected = self._collect_followers(my_user)
            if not collected:
                return stats
            all_followers = [u for u in collected if u.lower() != my_user]

        self._log(f"{len(all_followers)} seguidores para desocultar.")

        total = len(all_followers)
        for i, username in enumerate(all_followers):
            if self._stop_requested:
                self._log("Parado pelo usuário.")
                break

            self._log(f"[{i + 1}/{total}] Desocultando @{username}...")

            if self._unhide_story_from_user(username):
                stats["unhidden"] += 1
            else:
                stats["skipped"] += 1

            self._delay(2, 4)

        self._log("=" * 50)
        self._log("Desocultação finalizada!")
        self._log(f"  Desocultados: {stats['unhidden']}")
        self._log(f"  Já visíveis/pulados: {stats['skipped']}")
        self._log(f"  Erros: {stats['errors']}")
        self._log("=" * 50)
        return stats
