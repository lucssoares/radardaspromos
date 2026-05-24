"""Módulo para interagir com a Instagram Graph API (contas profissionais).

Permite obter métricas do perfil como seguidores, seguindo, posts,
e acompanhar a evolução dos seguidores ao longo do tempo.
"""

import json
import os
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

# Arquivo local para salvar histórico de métricas
HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "metrics_history.json"
)


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

        # Variação desde o primeiro registro
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
