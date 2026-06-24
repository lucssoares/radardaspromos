"""Módulo para interagir com a Instagram Graph API (contas profissionais).

Permite obter métricas do perfil como seguidores, seguindo, posts,
e acompanhar a evolução dos seguidores ao longo do tempo.
Inclui gerenciamento de tokens (salvar, trocar por longa duração, renovar).
Suporta publicação de stories via API.
"""

import json
import mimetypes
import os
import time
import uuid
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_VERSION = "v25.0"
BASE_URL_FB = f"https://graph.facebook.com/{API_VERSION}"
BASE_URL_IG = f"https://graph.instagram.com/{API_VERSION}"

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


# ── Upload de imagem para URL pública ────────────────────────────────────

def _multipart_upload(local_path: str, url: str, field: str) -> bytes:
    """Faz um POST multipart/form-data de um arquivo. Retorna o corpo."""
    boundary = "----wb" + uuid.uuid4().hex
    filename = os.path.basename(local_path)
    ctype = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    with open(local_path, "rb") as f:
        file_bytes = f.read()
    body = b""
    body += ("--" + boundary + "\r\n").encode()
    body += (
        'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
        % (field, filename)
    ).encode()
    body += ("Content-Type: %s\r\n\r\n" % ctype).encode()
    body += file_bytes + b"\r\n"
    body += ("--" + boundary + "--\r\n").encode()
    req = Request(
        url,
        data=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urlopen(req, timeout=90) as resp:
        return resp.read()


def upload_image_public(local_path: str) -> str:
    """Sobe uma imagem local para um host público e retorna a URL direta.

    A Graph API do Instagram exige uma URL pública para publicar. Usa o
    tmpfiles.org (com fallback para litterbox/catbox). O link fica
    disponível tempo suficiente para o Instagram baixar a imagem.
    """
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"Imagem não encontrada: {local_path}")

    # 1) tmpfiles.org → retorna JSON com a URL da página; converte p/ /dl/.
    try:
        raw = _multipart_upload(
            local_path, "https://tmpfiles.org/api/v1/upload", "file"
        )
        data = json.loads(raw.decode("utf-8"))
        page_url = data.get("data", {}).get("url", "")
        if page_url:
            return page_url.replace(
                "tmpfiles.org/", "tmpfiles.org/dl/", 1
            )
    except Exception:
        pass

    # 2) Fallback: litterbox (catbox temporário) → retorna a URL direta.
    boundary = "----wb" + uuid.uuid4().hex
    filename = os.path.basename(local_path)
    ctype = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    with open(local_path, "rb") as f:
        file_bytes = f.read()
    body = b""
    for name, value in (("reqtype", "fileupload"), ("time", "1h")):
        body += ("--" + boundary + "\r\n").encode()
        body += (
            'Content-Disposition: form-data; name="%s"\r\n\r\n' % name
        ).encode()
        body += (value + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += (
        'Content-Disposition: form-data; name="fileToUpload"; '
        'filename="%s"\r\n' % filename
    ).encode()
    body += ("Content-Type: %s\r\n\r\n" % ctype).encode()
    body += file_bytes + b"\r\n"
    body += ("--" + boundary + "--\r\n").encode()
    req = Request(
        "https://litterbox.catbox.moe/resources/internals/api.php",
        data=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urlopen(req, timeout=90) as resp:
        link = resp.read().decode("utf-8").strip()
    if link.startswith("http"):
        return link
    raise RuntimeError(f"Falha ao subir imagem para host público: {link}")


class InstagramAPI:
    """Cliente para a Instagram Graph API."""

    def __init__(self, access_token: str, ig_user_id: str = ""):
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self._is_ig_token = access_token.startswith("IGAA")

    @property
    def base_url(self) -> str:
        """Retorna a URL base correta dependendo do tipo de token."""
        if self._is_ig_token:
            return BASE_URL_IG
        return BASE_URL_FB

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

    def _post_request(self, url: str, data: dict) -> dict:
        """Faz uma requisição POST à API."""
        data["access_token"] = self.access_token
        body = urlencode(data).encode("utf-8")
        req = Request(url, data=body, method="POST")
        req.add_header("User-Agent", "InstaFollowBot/1.0")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            resp_body = exc.read().decode()
            raise RuntimeError(
                f"Erro na API ({exc.code}): {resp_body}"
            ) from exc

    # ── Token de longa duração ───────────────────────────────────────────

    def exchange_for_long_lived_token(self, app_id: str, app_secret: str) -> dict:
        """Troca um token de curta duração por um de longa duração (~60 dias).

        Retorna dict com 'access_token', 'token_type' e 'expires_in'.
        Suporta tanto tokens IGAA (Instagram Login) quanto EAA (Facebook Login).
        Tokens IGAA gerados pelo painel do Meta já são de longa duração;
        nesse caso tenta renovar diretamente.
        """
        if self._is_ig_token:
            try:
                params = urlencode({
                    "grant_type": "ig_exchange_token",
                    "client_secret": app_secret,
                    "access_token": self.access_token,
                })
                url = f"https://graph.instagram.com/access_token?{params}"
                req = Request(url)
                req.add_header("User-Agent", "InstaFollowBot/1.0")
                with urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
            except HTTPError:
                return self._mark_as_long_lived()
        else:
            params = urlencode({
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": self.access_token,
            })
            url = (
                f"https://graph.facebook.com/{API_VERSION}"
                f"/oauth/access_token?{params}"
            )
            req = Request(url)
            req.add_header("User-Agent", "InstaFollowBot/1.0")
            try:
                with urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
            except HTTPError as exc:
                body = exc.read().decode()
                raise RuntimeError(
                    f"Erro ao trocar token ({exc.code}): {body}"
                ) from exc

        new_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)

        if new_token:
            self.access_token = new_token

        token_data = {
            "access_token": self.access_token,
            "token_type": data.get("token_type", "bearer"),
            "expires_in": expires_in,
            "ig_user_id": self.ig_user_id,
            "created_at": datetime.now().isoformat(),
            "is_long_lived": True,
        }
        save_token_data(token_data)

        return token_data

    def _mark_as_long_lived(self) -> dict:
        """Marca o token atual como de longa duração e salva."""
        token_data = {
            "access_token": self.access_token,
            "token_type": "bearer",
            "expires_in": 5_184_000,
            "ig_user_id": self.ig_user_id,
            "created_at": datetime.now().isoformat(),
            "is_long_lived": True,
            "already_long_lived": True,
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
        if self._is_ig_token:
            data = self._request(
                f"{self.base_url}/me?fields=user_id,username,name"
            )
            self.ig_user_id = data.get("user_id", data.get("id", ""))
            if self.ig_user_id:
                return self.ig_user_id
            raise RuntimeError(
                "Não foi possível obter o IG User ID com o token IGAA."
            )

        data = self._request(f"{self.base_url}/me?fields=id,name")
        fb_user_id = data.get("id", "")

        pages = self._request(
            f"{self.base_url}/{fb_user_id}/accounts"
            f"?fields=id,name,instagram_business_account"
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
            f"{self.base_url}/{self.ig_user_id}?fields={fields}"
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
            f"{self.base_url}/{self.ig_user_id}?fields={fields}"
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

    # ── Publicação de Stories ─────────────────────────────────────────────

    def publish_story(self, image_url: str) -> dict:
        """Publica um story com imagem via Instagram Graph API.

        A imagem deve estar em uma URL pública acessível.
        Retorna dict com 'id' do media publicado.

        Requer permissão: instagram_business_content_publish
        """
        if not self.ig_user_id:
            self.discover_user_id()

        # Passo 1: Criar container do story
        container_url = f"{self.base_url}/{self.ig_user_id}/media"
        container_data = {
            "image_url": image_url,
            "media_type": "STORIES",
        }

        container_result = self._post_request(container_url, container_data)
        container_id = container_result.get("id")

        if not container_id:
            raise RuntimeError(
                f"Falha ao criar container: {container_result}"
            )

        # Passo 2: Aguardar processamento (polling)
        status_url = (
            f"{self.base_url}/{container_id}"
            f"?fields=status_code"
        )
        for _ in range(30):
            time.sleep(2)
            status = self._request(status_url)
            status_code = status.get("status_code", "")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                raise RuntimeError(
                    f"Erro no processamento do story: {status}"
                )
        else:
            raise RuntimeError("Timeout no processamento do story")

        # Passo 3: Publicar o container
        publish_url = f"{self.base_url}/{self.ig_user_id}/media_publish"
        publish_data = {"creation_id": container_id}

        result = self._post_request(publish_url, publish_data)

        if not result.get("id"):
            raise RuntimeError(f"Falha ao publicar story: {result}")

        return result

    def publish_story_image(self, local_path: str) -> dict:
        """Sobe uma imagem local p/ URL pública e publica como story.

        Útil para publicar uma imagem fixa (ex.: fundo do Radar das
        Promos) em tela cheia, sem depender de um produto.
        """
        public_url = upload_image_public(local_path)
        return self.publish_story(public_url)

    def publish_feed_post(
        self, image_url: str, caption: str = ""
    ) -> dict:
        """Publica um post no feed via Instagram Graph API.

        A imagem deve estar em uma URL pública acessível.
        Retorna dict com 'id' do media publicado.

        Requer permissão: instagram_business_content_publish
        """
        if not self.ig_user_id:
            self.discover_user_id()

        # Passo 1: Criar container do post
        container_url = f"{self.base_url}/{self.ig_user_id}/media"
        container_data = {"image_url": image_url}
        if caption:
            container_data["caption"] = caption

        container_result = self._post_request(container_url, container_data)
        container_id = container_result.get("id")

        if not container_id:
            raise RuntimeError(
                f"Falha ao criar container: {container_result}"
            )

        # Passo 2: Aguardar processamento
        status_url = (
            f"{self.base_url}/{container_id}"
            f"?fields=status_code"
        )
        for _ in range(30):
            time.sleep(2)
            status = self._request(status_url)
            status_code = status.get("status_code", "")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                raise RuntimeError(
                    f"Erro no processamento do post: {status}"
                )
        else:
            raise RuntimeError("Timeout no processamento do post")

        # Passo 3: Publicar
        publish_url = f"{self.base_url}/{self.ig_user_id}/media_publish"
        publish_data = {"creation_id": container_id}

        result = self._post_request(publish_url, publish_data)

        if not result.get("id"):
            raise RuntimeError(f"Falha ao publicar post: {result}")

        return result

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
