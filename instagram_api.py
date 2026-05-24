"""Módulo para interagir com a Instagram Graph API (contas profissionais).

Permite obter métricas do perfil como seguidores, seguindo, posts,
e acompanhar a evolução dos seguidores ao longo do tempo.
Inclui gerenciamento de tokens (salvar, trocar por longa duração, renovar).
"""

import json
import os
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

# Diretório persistente para dados do bot (home do usuário)
# Usa a pasta do usuário para que os dados persistam mesmo ao rodar como .exe
_APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".instafollow_bot")
os.makedirs(_APP_DATA_DIR, exist_ok=True)

HISTORY_FILE = os.path.join(_APP_DATA_DIR, "metrics_history.json")
TOKEN_FILE = os.path.join(_APP_DATA_DIR, "token_data.json")


# ── Gerenciamento de token ───────────────────────────────────────────────

def save_token_data(data: dict) -> None:
    """Salva dados do token no arquivo local."""
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_token_data() -> dict | None:
    """Carrega dados do token salvo. Retorna None se não existir."""
    if os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def clear_token_data() -> None:
    """Remove o arquivo de token salvo."""
    if os.path.isfile(TOKEN_FILE):
        os.remove(TOKEN_FILE)


class InstagramAPI:
    """Cliente para a Instagram Graph API."""

    def __init__(self, access_token: str, ig_user_id: str = ""):
        self.access_token = access_token
        self.ig_user_id = ig_user_id

    def _request(self, url: str) -> dict:
        """Faz uma requisição GET à API."""
        separator = "&" if "?" in url else "?"
        full_url = f"{url}{separator}access_token={self.access_token}"
        req = Request(full_url)
        req.add_header("User-Agent", "InstaFollowBot/1.0")
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            body = exc.read().decode()
            raise RuntimeError(f"Erro na API ({exc.code}): {body}") from exc

    # ── Token de longa duração ───────────────────────────────────────────

    def exchange_for_long_lived_token(self, app_id: str, app_secret: str) -> dict:
        """Troca um token de curta duração por um de longa duração (~60 dias).

        Retorna dict com 'access_token', 'token_type' e 'expires_in'.
        """
        params = urlencode({
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": self.access_token,
        })
        url = f"https://graph.facebook.com/{API_VERSION}/oauth/access_token?{params}"
        req = Request(url)
        req.add_header("User-Agent", "InstaFollowBot/1.0")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as exc:
            body = exc.read().decode()
            raise RuntimeError(f"Erro ao trocar token ({exc.code}): {body}") from exc

        new_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)

        self.access_token = new_token

        token_data = {
            "access_token": new_token,
            "token_type": data.get("token_type", "bearer"),
            "expires_in": expires_in,
            "ig_user_id": self.ig_user_id,
            "created_at": datetime.now().isoformat(),
            "is_long_lived": True,
        }
        save_token_data(token_data)

        return token_data

    def refresh_long_lived_token(self) -> dict:
        """Renova um token de longa duração (antes de expirar).

        Tokens de longa duração podem ser renovados se ainda não expiraram.
        O novo token dura mais 60 dias.
        """
        params = urlencode({
            "grant_type": "ig_refresh_token",
            "access_token": self.access_token,
        })
        url = f"https://graph.instagram.com/refresh_access_token?{params}"
        req = Request(url)
        req.add_header("User-Agent", "InstaFollowBot/1.0")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as exc:
            body = exc.read().decode()
            raise RuntimeError(f"Erro ao renovar token ({exc.code}): {body}") from exc

        new_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)

        self.access_token = new_token

        token_data = {
            "access_token": new_token,
            "token_type": data.get("token_type", "bearer"),
            "expires_in": expires_in,
            "ig_user_id": self.ig_user_id,
            "created_at": datetime.now().isoformat(),
            "is_long_lived": True,
        }
        save_token_data(token_data)

        return token_data

    def save_current_token(self) -> None:
        """Salva o token atual localmente (sem trocar por longa duração)."""
        token_data = {
            "access_token": self.access_token,
            "ig_user_id": self.ig_user_id,
            "created_at": datetime.now().isoformat(),
            "is_long_lived": False,
        }
        save_token_data(token_data)

    # ── Descobrir IG User ID ─────────────────────────────────────────────

    def discover_user_id(self) -> str:
        """Descobre o IG User ID a partir do access token."""
        data = self._request(f"{BASE_URL}/me?fields=id,name")
        fb_user_id = data.get("id", "")

        pages = self._request(
            f"{BASE_URL}/{fb_user_id}/accounts?fields=id,name,instagram_business_account"
        )

        for page in pages.get("data", []):
            ig_account = page.get("instagram_business_account")
            if ig_account:
                self.ig_user_id = ig_account["id"]
                return self.ig_user_id

        raise RuntimeError(
            "Nenhuma conta profissional do Instagram encontrada. "
            "Verifique se sua conta é Business ou Creator e está "
            "vinculada a uma página do Facebook."
        )

    # ── Métricas do perfil ───────────────────────────────────────────────

    def get_profile_metrics(self) -> dict:
        """Retorna métricas do perfil (seguidores, seguindo, posts, bio)."""
        if not self.ig_user_id:
            self.discover_user_id()

        fields = "username,name,biography,followers_count,follows_count,media_count,profile_picture_url"
        data = self._request(
            f"{BASE_URL}/{self.ig_user_id}?fields={fields}"
        )
        return {
            "username": data.get("username", ""),
            "name": data.get("name", ""),
            "biography": data.get("biography", ""),
            "followers_count": data.get("followers_count", 0),
            "follows_count": data.get("follows_count", 0),
            "media_count": data.get("media_count", 0),
            "profile_picture_url": data.get("profile_picture_url", ""),
            "timestamp": datetime.now().isoformat(),
        }

    # ── Business Discovery (ver perfis de outros) ────────────────────────

    def get_other_profile(self, username: str) -> dict:
        """Retorna dados públicos de outro perfil profissional."""
        if not self.ig_user_id:
            self.discover_user_id()

        fields = (
            f"business_discovery.username({username})"
            "{username,name,biography,followers_count,follows_count,media_count}"
        )
        data = self._request(
            f"{BASE_URL}/{self.ig_user_id}?fields={fields}"
        )
        bd = data.get("business_discovery", {})
        return {
            "username": bd.get("username", username),
            "name": bd.get("name", ""),
            "biography": bd.get("biography", ""),
            "followers_count": bd.get("followers_count", 0),
            "follows_count": bd.get("follows_count", 0),
            "media_count": bd.get("media_count", 0),
        }

    # ── Histórico de métricas ────────────────────────────────────────────

    def _load_history(self) -> list:
        """Carrega histórico de métricas do arquivo JSON."""
        if os.path.isfile(HISTORY_FILE):
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save_history(self, history: list) -> None:
        """Salva histórico no arquivo JSON."""
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def record_metrics(self) -> dict:
        """Busca métricas atuais e salva no histórico. Retorna os dados."""
        metrics = self.get_profile_metrics()
        history = self._load_history()
        history.append(metrics)
        self._save_history(history)
        return metrics

    def get_history(self) -> list:
        """Retorna todo o histórico de métricas salvo."""
        return self._load_history()

    def get_followers_change(self) -> dict:
        """Calcula a variação de seguidores baseado no histórico."""
        history = self._load_history()
        if len(history) < 2:
            return {
                "current": history[-1]["followers_count"] if history else 0,
                "previous": 0,
                "change": 0,
                "records": len(history),
            }

        current = history[-1]
        previous = history[-2]
        change = current["followers_count"] - previous["followers_count"]

        first = history[0]
        total_change = current["followers_count"] - first["followers_count"]

        return {
            "current": current["followers_count"],
            "previous": previous["followers_count"],
            "change": change,
            "total_change": total_change,
            "first_record": first["timestamp"],
            "last_record": current["timestamp"],
            "records": len(history),
        }
