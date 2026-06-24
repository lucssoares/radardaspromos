"""
Instagram Auto-Follow Bot usando Playwright.

Automatiza o processo de seguir os seguidores de um perfil específico.
"""

import logging
import os
import random
import sys
import time

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

load_dotenv()

# ── Configuração de logging ──────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("instagram_follow.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── Configurações ────────────────────────────────────────────────────────────

USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
TARGET_PROFILE = os.getenv("TARGET_PROFILE", "")
MAX_FOLLOWS = int(os.getenv("MAX_FOLLOWS_PER_SESSION", "20"))
MIN_DELAY = int(os.getenv("MIN_DELAY_SECONDS", "3"))
MAX_DELAY = int(os.getenv("MAX_DELAY_SECONDS", "8"))
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"


def random_delay(min_sec: int = MIN_DELAY, max_sec: int = MAX_DELAY) -> None:
    """Espera um tempo aleatório entre ações para simular comportamento humano."""
    delay = random.uniform(min_sec, max_sec)
    logger.info("Aguardando %.1f segundos...", delay)
    time.sleep(delay)


def login(page) -> bool:
    """Realiza login no Instagram."""
    logger.info("Navegando para a página de login...")
    page.goto("https://www.instagram.com/accounts/login/", wait_until="networkidle")
    random_delay(2, 4)

    # Aceitar cookies se o banner aparecer
    try:
        cookie_btn = page.locator(
            "button:has-text('Permitir'), button:has-text('Allow'), button:has-text('Accept')"
        )
        if cookie_btn.count() > 0:
            cookie_btn.first.click()
            random_delay(1, 2)
    except Exception:
        pass

    logger.info("Preenchendo credenciais para @%s...", USERNAME)
    username_input = page.locator('input[name="username"]')
    username_input.fill(USERNAME)
    random_delay(0.5, 1.5)

    password_input = page.locator('input[name="password"]')
    password_input.fill(PASSWORD)
    random_delay(0.5, 1.5)

    # Clicar no botão de login
    login_button = page.locator('button[type="submit"]')
    login_button.click()

    logger.info("Aguardando login...")
    try:
        page.wait_for_url("**/instagram.com/**", timeout=30000)
        random_delay(3, 5)
    except PlaywrightTimeout:
        logger.error("Timeout ao aguardar login. Verifique suas credenciais.")
        return False

    # Verificar se houve erro de login
    error_message = page.locator("#slfErrorAlert, [data-testid='login-error-message']")
    if error_message.count() > 0:
        logger.error("Erro de login: %s", error_message.text_content())
        return False

    # Fechar popup "Salvar informações de login" se aparecer
    try:
        not_now_btn = page.locator(
            "button:has-text('Agora não'), button:has-text('Not Now'), button:has-text('Not now')"
        )
        if not_now_btn.count() > 0:
            not_now_btn.first.click()
            random_delay(1, 2)
    except Exception:
        pass

    # Fechar popup de notificações se aparecer
    try:
        not_now_btn = page.locator(
            "button:has-text('Agora não'), button:has-text('Not Now'), button:has-text('Not now')"
        )
        if not_now_btn.count() > 0:
            not_now_btn.first.click()
            random_delay(1, 2)
    except Exception:
        pass

    logger.info("Login realizado com sucesso!")
    return True


def open_followers_dialog(page, target_profile: str) -> bool:
    """Navega até o perfil alvo e abre a lista de seguidores."""
    profile_url = f"https://www.instagram.com/{target_profile}/"
    logger.info("Navegando para o perfil @%s...", target_profile)
    page.goto(profile_url, wait_until="networkidle")
    random_delay(2, 4)

    # Verificar se o perfil existe
    if (
        page.locator(
            "h2:has-text('Esta página não está disponível'), h2:has-text('Sorry, this page')"
        ).count()
        > 0
    ):
        logger.error("Perfil @%s não encontrado.", target_profile)
        return False

    # Clicar no link de seguidores
    logger.info("Abrindo lista de seguidores...")
    followers_link = page.locator(f'a[href="/{target_profile}/followers/"]')
    if followers_link.count() == 0:
        # Tentar seletor alternativo
        followers_link = page.locator(
            "a:has-text('seguidores'), a:has-text('followers')"
        )

    if followers_link.count() == 0:
        logger.error(
            "Não foi possível encontrar o link de seguidores. O perfil pode ser privado."
        )
        return False

    followers_link.first.click()
    random_delay(2, 3)

    logger.info("Lista de seguidores aberta com sucesso!")
    return True


def scroll_followers_list(page) -> None:
    """Rola a lista de seguidores para carregar mais perfis."""
    dialog = page.locator("div[role='dialog']")
    if dialog.count() > 0:
        scrollable = dialog.locator("div[style*='overflow']").first
        scrollable.evaluate("el => el.scrollTop = el.scrollHeight")


def follow_users(page, max_follows: int) -> dict:
    """Segue usuários da lista de seguidores aberta."""
    stats = {"followed": 0, "skipped": 0, "errors": 0}

    logger.info("Iniciando processo de follow (máximo: %d)...", max_follows)

    # Aguardar a lista de seguidores carregar
    random_delay(2, 3)

    while stats["followed"] < max_follows:
        # Buscar botões "Seguir" / "Follow" dentro do diálogo
        dialog = page.locator("div[role='dialog']")
        follow_buttons = dialog.locator(
            "button:has-text('Seguir'):not(:has-text('Seguindo')), "
            "button:has-text('Follow'):not(:has-text('Following'))"
        )

        available = follow_buttons.count()
        if available == 0:
            logger.info("Nenhum botão 'Seguir' encontrado. Rolando a lista...")
            scroll_followers_list(page)
            random_delay(2, 3)

            # Verificar novamente após rolar
            follow_buttons = dialog.locator(
                "button:has-text('Seguir'):not(:has-text('Seguindo')), "
                "button:has-text('Follow'):not(:has-text('Following'))"
            )
            if follow_buttons.count() == 0:
                logger.info("Não há mais usuários para seguir nesta lista.")
                break

        # Iterar pelos botões disponíveis
        for i in range(min(follow_buttons.count(), max_follows - stats["followed"])):
            try:
                button = follow_buttons.nth(i)
                button_text = button.text_content().strip()

                # Garantir que é um botão de "Seguir" e não "Seguindo"
                if button_text.lower() in ("seguir", "follow"):
                    # Tentar pegar o username do usuário
                    parent_row = button.locator(
                        "xpath=ancestor::li | xpath=ancestor::div[contains(@class, 'user')]"
                    )
                    username_el = parent_row.locator("a span, a").first
                    username = (
                        username_el.text_content().strip()
                        if username_el.count() > 0
                        else "desconhecido"
                    )

                    logger.info(
                        "[%d/%d] Seguindo @%s...",
                        stats["followed"] + 1,
                        max_follows,
                        username,
                    )
                    button.click()
                    stats["followed"] += 1
                    random_delay()
                else:
                    stats["skipped"] += 1

            except PlaywrightTimeout:
                logger.warning("Timeout ao tentar seguir usuário. Pulando...")
                stats["errors"] += 1
                random_delay(1, 2)
            except Exception as e:
                logger.warning("Erro ao seguir usuário: %s", e)
                stats["errors"] += 1
                random_delay(1, 2)

        # Rolar para carregar mais
        if stats["followed"] < max_follows:
            scroll_followers_list(page)
            random_delay(2, 3)

    return stats


def run() -> None:
    """Função principal que executa o bot."""
    # Validar configurações
    if not USERNAME or not PASSWORD:
        logger.error(
            "Configure INSTAGRAM_USERNAME e INSTAGRAM_PASSWORD no arquivo .env"
        )
        sys.exit(1)

    if not TARGET_PROFILE:
        logger.error("Configure TARGET_PROFILE no arquivo .env")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Instagram Auto-Follow Bot")
    logger.info("Perfil alvo: @%s", TARGET_PROFILE)
    logger.info("Máximo de follows: %d", MAX_FOLLOWS)
    logger.info("Delay entre ações: %d-%d segundos", MIN_DELAY, MAX_DELAY)
    logger.info("Modo headless: %s", HEADLESS)
    logger.info("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        page = context.new_page()

        try:
            if not login(page):
                logger.error("Falha no login. Encerrando.")
                return

            if not open_followers_dialog(page, TARGET_PROFILE):
                logger.error("Falha ao abrir lista de seguidores. Encerrando.")
                return

            stats = follow_users(page, MAX_FOLLOWS)

            logger.info("=" * 60)
            logger.info("Processo finalizado!")
            logger.info("Seguidos: %d", stats["followed"])
            logger.info("Pulados: %d", stats["skipped"])
            logger.info("Erros: %d", stats["errors"])
            logger.info("=" * 60)

        except KeyboardInterrupt:
            logger.info("Processo interrompido pelo usuário.")
        except Exception as e:
            logger.error("Erro inesperado: %s", e, exc_info=True)
        finally:
            random_delay(1, 2)
            browser.close()
            logger.info("Navegador fechado. Até a próxima!")


if __name__ == "__main__":
    run()
