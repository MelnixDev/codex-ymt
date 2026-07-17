#!/usr/bin/env python3
"""Dependency-free MCP server for private YouTube metadata localization."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


SERVER_NAME = "codex-ymt"
SERVER_VERSION = "0.1.0"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
PENDING_TTL_SECONDS = 15 * 60
DISCONNECT_TTL_SECONDS = 5 * 60


class ToolFailure(RuntimeError):
    """An expected error that should be shown to the user."""


class GoogleApiFailure(ToolFailure):
    """A structured Google API error that callers can handle safely."""

    def __init__(self, status: int, message: str, reason: str | None = None) -> None:
        super().__init__(f"Google API error ({status}): {message}")
        self.status = status
        self.reason = reason


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise ToolFailure("Could not read local JSON data because it is invalid.") from exc
    except OSError as exc:
        raise ToolFailure(
            f"Could not read local JSON data ({exc.__class__.__name__})."
        ) from exc


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def data_root() -> Path:
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data).expanduser()
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config).expanduser() if xdg_config else Path.home() / ".config"
    return base / "codex-ymt"


def result_payload(payload: Any) -> dict[str, Any]:
    structured = payload if isinstance(payload, dict) else {"result": payload}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "structuredContent": structured,
    }


def tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


class YouTubeLocalizer:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_root()
        self.credentials_path = self.root / "oauth-client.json"
        self.token_path = self.root / "oauth-token.json"
        self.settings_path = self.root / "channel-settings.json"
        self.drafts_dir = self.root / "drafts"
        self._auth: dict[str, Any] | None = None
        self._auth_server: ThreadingHTTPServer | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._disconnect_pending: dict[str, dict[str, Any]] = {}

    # OAuth

    def _credentials(self) -> dict[str, str]:
        stored = read_json(self.credentials_path, {})
        client_id = os.environ.get("YOUTUBE_CLIENT_ID") or stored.get("client_id")
        client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET") or stored.get(
            "client_secret"
        )
        if not client_id or not client_secret:
            raise ToolFailure(
                "Google OAuth is not configured. Create a Desktop app OAuth client, "
                "then call youtube_configure_oauth with the local path to its JSON file, "
                "or configure YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET."
            )
        return {"client_id": client_id, "client_secret": client_secret}

    def configure_oauth(self, args: dict[str, Any]) -> dict[str, Any]:
        client_json_path = str(args.get("client_json_path", "")).strip()
        if not client_json_path:
            raise ToolFailure(
                "client_json_path is required. Provide the local path to a Google Desktop OAuth JSON file."
            )
        source_path = Path(client_json_path).expanduser()
        source = read_json(source_path, None)
        if not isinstance(source, dict):
            raise ToolFailure("The OAuth client JSON file is missing or invalid.")
        client = source.get("installed")
        if not isinstance(client, dict):
            if isinstance(source.get("web"), dict):
                raise ToolFailure(
                    "Web OAuth clients are not supported. Create a Desktop app OAuth client."
                )
            raise ToolFailure(
                "The OAuth JSON has no 'installed' client. Create a Desktop app OAuth client."
            )
        client_id = str(client.get("client_id", "")).strip()
        client_secret = str(client.get("client_secret", "")).strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise ToolFailure("client_id must be a Google OAuth client ID.")
        if len(client_secret) < 8:
            raise ToolFailure("client_secret is missing or invalid.")
        existing = read_json(self.credentials_path, {})
        existing_client_id = existing.get("client_id") if isinstance(existing, dict) else None
        token_cleared = self.token_path.exists() and existing_client_id != client_id
        atomic_write_json(
            self.credentials_path,
            {"client_id": client_id, "client_secret": client_secret},
        )
        if token_cleared:
            self._delete_local_token()
        return {
            "configured": True,
            "source": "local_json_file",
            "stored_locally": True,
            "token_cleared": token_cleared,
            "reconnect_required": token_cleared or not self.token_path.exists(),
        }

    def auth_start(self, _args: dict[str, Any]) -> dict[str, Any]:
        credentials = self._credentials()
        if self._auth_server:
            try:
                self._auth_server.shutdown()
                self._auth_server.server_close()
            except OSError:
                pass

        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        auth_state: dict[str, Any] = {
            "state": state,
            "verifier": verifier,
            "started_at": time.time(),
            "code": None,
            "error": None,
        }

        outer = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_values: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                parsed = urlparse(self.path)
                if parsed.path != "/oauth2/callback":
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query)
                if query.get("state", [None])[0] != auth_state["state"]:
                    auth_state["error"] = "OAuth state mismatch. Start authorization again."
                    status = 400
                elif query.get("error"):
                    auth_state["error"] = str(query["error"][0])
                    status = 400
                elif query.get("code"):
                    auth_state["code"] = str(query["code"][0])
                    status = 200
                else:
                    auth_state["error"] = "Google returned no authorization code."
                    status = 400
                body = (
                    "Codex YMT is connected. Return to Codex."
                    if status == 200
                    else f"Authorization failed: {auth_state['error']}"
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                threading.Thread(target=outer._stop_auth_server, daemon=True).start()

        server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
        redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2/callback"
        auth_state["redirect_uri"] = redirect_uri
        self._auth = auth_state
        self._auth_server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()

        query = urlencode(
            {
                "client_id": credentials["client_id"],
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": YOUTUBE_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return {
            "status": "awaiting_user",
            "authorization_url": f"{GOOGLE_AUTH_URL}?{query}",
            "expires_in_seconds": 300,
            "next": "Open the URL, approve access, then call youtube_auth_status.",
        }

    def _stop_auth_server(self) -> None:
        server = self._auth_server
        if not server:
            return
        try:
            server.shutdown()
            server.server_close()
        finally:
            self._auth_server = None

    def auth_status(self, _args: dict[str, Any]) -> dict[str, Any]:
        if self._auth:
            if self._auth.get("error"):
                error = self._auth["error"]
                self._auth = None
                raise ToolFailure(f"Google authorization failed: {error}")
            if self._auth.get("code"):
                self._exchange_code()
                self._auth = None
            elif time.time() - self._auth["started_at"] < 300:
                return {"configured": True, "connected": False, "status": "awaiting_user"}
            else:
                self._stop_auth_server()
                self._auth = None
                return {"configured": True, "connected": False, "status": "expired"}

        try:
            self._credentials()
            configured = True
        except ToolFailure:
            configured = False
        token = read_json(self.token_path, {})
        return {
            "configured": configured,
            "connected": bool(token.get("refresh_token") or token.get("access_token")),
            "status": "connected" if token else "not_connected",
            "scope": token.get("scope", YOUTUBE_SCOPE) if token else YOUTUBE_SCOPE,
        }

    def _exchange_code(self) -> None:
        if not self._auth:
            raise ToolFailure("No OAuth flow is pending.")
        credentials = self._credentials()
        payload = self._form_request(
            GOOGLE_TOKEN_URL,
            {
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "code": self._auth["code"],
                "code_verifier": self._auth["verifier"],
                "redirect_uri": self._auth["redirect_uri"],
                "grant_type": "authorization_code",
            },
        )
        payload["expires_at"] = time.time() + int(payload.get("expires_in", 3600))
        atomic_write_json(self.token_path, payload)

    def _form_request(self, url: str, values: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url,
            data=urlencode(values).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        return self._open_json(request)

    def _access_token(self) -> str:
        token = read_json(self.token_path, {})
        if not token:
            raise ToolFailure("YouTube is not connected. Call youtube_auth_start first.")
        if token.get("access_token") and float(token.get("expires_at", 0)) > time.time() + 60:
            return str(token["access_token"])
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise ToolFailure("The Google access token expired and no refresh token is stored. Reconnect.")
        credentials = self._credentials()
        refreshed = self._form_request(
            GOOGLE_TOKEN_URL,
            {
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        refreshed["refresh_token"] = refresh_token
        refreshed["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600))
        atomic_write_json(self.token_path, refreshed)
        return str(refreshed["access_token"])

    # YouTube API

    def _open_json(self, request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            reason = None
            try:
                payload = json.loads(raw)
                detail = payload.get("error", {})
                if isinstance(detail, dict):
                    message = detail.get("message") or raw
                    errors = detail.get("errors", [])
                    if errors and isinstance(errors[0], dict):
                        reason = errors[0].get("reason")
                else:
                    reason = str(detail) if detail else None
                    message = payload.get("error_description") or reason or raw
            except json.JSONDecodeError:
                message = raw or str(exc)
            raise GoogleApiFailure(exc.code, str(message), reason) from exc
        except URLError as exc:
            raise ToolFailure(f"Could not reach Google API: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolFailure("Google returned an invalid JSON response.") from exc
        if not isinstance(parsed, dict):
            raise ToolFailure("Google returned an unexpected response.")
        return parsed

    def prepare_disconnect(self, _args: dict[str, Any]) -> dict[str, Any]:
        token_data = read_json(self.token_path, {})
        revoke_token = token_data.get("refresh_token") or token_data.get("access_token")
        if not revoke_token:
            return {
                "ready": False,
                "connected": False,
                "message": "No local Google token is stored. Nothing was changed.",
            }
        confirmation_token = secrets.token_urlsafe(24)
        self._disconnect_pending[confirmation_token] = {
            "expires_at": time.time() + DISCONNECT_TTL_SECONDS,
            "token_fingerprint": canonical_hash(str(revoke_token)),
        }
        return {
            "ready": True,
            "connected": True,
            "effects": [
                "Revoke the stored Google token.",
                "Delete the local oauth-token.json file.",
            ],
            "preserved": [
                "OAuth client configuration",
                "channel settings",
                "translation drafts",
            ],
            "confirmation_token": confirmation_token,
            "confirmation_expires_in_seconds": DISCONNECT_TTL_SECONDS,
            "warning": "Commit only after the user explicitly approves this disconnect preview.",
        }

    def commit_disconnect(self, args: dict[str, Any]) -> dict[str, Any]:
        confirmation_token = str(args.get("confirmation_token", "")).strip()
        pending = self._disconnect_pending.get(confirmation_token)
        if pending is None:
            raise ToolFailure(
                "Disconnect confirmation token is missing or unknown. Prepare a new disconnect preview."
            )
        if pending["expires_at"] < time.time():
            self._disconnect_pending.pop(confirmation_token, None)
            raise ToolFailure(
                "Disconnect confirmation token expired. Prepare and approve a new preview."
            )

        token_data = read_json(self.token_path, {})
        revoke_token = token_data.get("refresh_token") or token_data.get("access_token")
        if not revoke_token:
            self._disconnect_pending.pop(confirmation_token, None)
            return {
                "disconnected": True,
                "remote_revoked": False,
                "local_token_deleted": False,
                "already_disconnected": True,
            }
        if canonical_hash(str(revoke_token)) != pending["token_fingerprint"]:
            self._disconnect_pending.pop(confirmation_token, None)
            raise ToolFailure(
                "The Google connection changed after the disconnect preview. "
                "Nothing was revoked. Prepare and approve a new preview."
            )

        already_revoked = False
        try:
            self._form_request(GOOGLE_REVOKE_URL, {"token": revoke_token})
        except GoogleApiFailure as exc:
            if exc.reason == "invalid_token":
                already_revoked = True
            else:
                raise

        self._delete_local_token()
        self._disconnect_pending.pop(confirmation_token, None)
        return {
            "disconnected": True,
            "remote_revoked": not already_revoked,
            "local_token_deleted": True,
            "already_revoked": already_revoked,
            "preserved": ["oauth-client.json", "channel settings", "translation drafts"],
        }

    def _delete_local_token(self) -> None:
        try:
            self.token_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ToolFailure(
                f"Could not delete the local OAuth token ({exc.__class__.__name__})."
            ) from exc

    def _api(
        self,
        resource: str,
        params: dict[str, Any],
        method: str = "GET",
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{YOUTUBE_API}/{resource}?{query}"
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if extra_headers:
            headers.update(extra_headers)
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return self._open_json(Request(url, data=data, headers=headers, method=method))

    def _channel(self) -> dict[str, Any]:
        response = self._api(
            "channels",
            {"part": "id,snippet,contentDetails", "mine": "true", "maxResults": 1},
        )
        items = response.get("items", [])
        if not items:
            raise ToolFailure("The connected Google account has no accessible YouTube channel.")
        return items[0]

    def list_videos(self, args: dict[str, Any]) -> dict[str, Any]:
        max_results = int(args.get("max_results", 10))
        if not 1 <= max_results <= 50:
            raise ToolFailure("max_results must be between 1 and 50.")
        channel = self._channel()
        uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        page = self._api(
            "playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": uploads,
                "maxResults": max_results,
                "pageToken": args.get("page_token"),
            },
        )
        ids = [item["contentDetails"]["videoId"] for item in page.get("items", [])]
        videos: list[dict[str, Any]] = []
        if ids:
            response = self._api(
                "videos",
                {"part": "snippet,localizations,status", "id": ",".join(ids)},
            )
            by_id = {item["id"]: item for item in response.get("items", [])}
            videos = [self._video_summary(by_id[video_id]) for video_id in ids if video_id in by_id]
        return {
            "channel": {"id": channel["id"], "title": channel["snippet"]["title"]},
            "videos": videos,
            "next_page_token": page.get("nextPageToken"),
        }

    def get_video(self, args: dict[str, Any]) -> dict[str, Any]:
        video_id = self._video_id(args)
        return self._get_video(video_id)

    def _get_video(self, video_id: str) -> dict[str, Any]:
        response = self._api(
            "videos",
            {"part": "snippet,localizations,status", "id": video_id},
        )
        items = response.get("items", [])
        if not items:
            raise ToolFailure(f"Video {video_id} was not found or is not editable by this account.")
        item = items[0]
        summary = self._video_summary(item)
        summary["etag"] = item.get("etag")
        summary["localizations"] = item.get("localizations", {})
        summary["tags"] = item.get("snippet", {}).get("tags", [])
        summary["categoryId"] = item.get("snippet", {}).get("categoryId")
        summary["defaultAudioLanguage"] = item.get("snippet", {}).get("defaultAudioLanguage")
        return summary

    @staticmethod
    def _video_summary(item: dict[str, Any]) -> dict[str, Any]:
        snippet = item.get("snippet", {})
        return {
            "id": item.get("id"),
            "channelId": snippet.get("channelId"),
            "channelTitle": snippet.get("channelTitle"),
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "publishedAt": snippet.get("publishedAt"),
            "defaultLanguage": snippet.get("defaultLanguage"),
            "privacyStatus": item.get("status", {}).get("privacyStatus"),
            "localization_languages": sorted(item.get("localizations", {}).keys()),
            "url": f"https://www.youtube.com/watch?v={item.get('id')}",
        }

    @staticmethod
    def _video_id(args: dict[str, Any]) -> str:
        raw = str(args.get("video_id", "")).strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", raw):
            return raw
        raise ToolFailure("video_id is missing or invalid.")

    # Settings and drafts

    def get_channel_settings(self, args: dict[str, Any]) -> dict[str, Any]:
        channel_id = self._channel_id(args)
        all_settings = read_json(self.settings_path, {})
        return {
            "channel_id": channel_id,
            "settings": all_settings.get(
                channel_id,
                {
                    "source_language": "auto",
                    "target_languages": [],
                    "instructions": "",
                    "interface_language": "uk",
                },
            ),
        }

    def save_channel_settings(self, args: dict[str, Any]) -> dict[str, Any]:
        channel_id = self._channel_id(args)
        source = str(args.get("source_language", "auto")).strip() or "auto"
        targets = self._languages(args.get("target_languages", []), allow_empty=True)
        instructions = str(args.get("instructions", "")).strip()
        interface_language = str(args.get("interface_language", "uk")).strip().lower()
        if interface_language not in {"en", "uk"}:
            raise ToolFailure("interface_language must be 'en' or 'uk'.")
        settings = {
            "source_language": source,
            "target_languages": targets,
            "instructions": instructions,
            "interface_language": interface_language,
            "updated_at": utc_timestamp(),
        }
        all_settings = read_json(self.settings_path, {})
        all_settings[channel_id] = settings
        atomic_write_json(self.settings_path, all_settings)
        return {"channel_id": channel_id, "settings": settings, "stored_locally": True}

    def _channel_id(self, args: dict[str, Any]) -> str:
        channel_id = str(args.get("channel_id", "")).strip()
        if channel_id:
            return channel_id
        return str(self._channel()["id"])

    @staticmethod
    def _languages(value: Any, allow_empty: bool = False) -> list[str]:
        if not isinstance(value, list):
            raise ToolFailure("target_languages must be an array of language codes.")
        languages: list[str] = []
        for raw in value:
            language = str(raw).strip()
            if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
                raise ToolFailure(f"Invalid language code: {language!r}.")
            if language not in languages:
                languages.append(language)
        if not languages and not allow_empty:
            raise ToolFailure("At least one target language is required.")
        return languages

    def save_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        video_id = self._video_id(args)
        translations = self._translations(args.get("translations"))
        selected = self._languages(
            args.get("selected_languages", list(translations)), allow_empty=True
        )
        unknown = sorted(set(selected) - set(translations))
        if unknown:
            raise ToolFailure(f"Selected languages are missing translations: {', '.join(unknown)}")
        video = self._get_video(video_id)
        draft = {
            "video_id": video_id,
            "channel_id": video.get("channelId"),
            "source_fingerprint": self._source_fingerprint(video),
            "source": {
                "title": video["title"],
                "description": video["description"],
                "default_language": video.get("defaultLanguage"),
            },
            "translations": translations,
            "selected_languages": selected,
            "updated_at": utc_timestamp(),
        }
        atomic_write_json(self._draft_path(video_id), draft)
        return {"saved": True, "draft": draft, "stored_locally": True}

    def get_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        video_id = self._video_id(args)
        draft = read_json(self._draft_path(video_id), None)
        if draft is None:
            return {"found": False, "video_id": video_id}
        video = self._get_video(video_id)
        return {
            "found": True,
            "stale": draft.get("source_fingerprint") != self._source_fingerprint(video),
            "draft": draft,
        }

    def _draft_path(self, video_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", video_id)
        return self.drafts_dir / f"{safe_id}.json"

    @staticmethod
    def _source_fingerprint(video: dict[str, Any]) -> str:
        return canonical_hash(
            {
                "title": video.get("title"),
                "description": video.get("description"),
                "defaultLanguage": video.get("defaultLanguage"),
            }
        )

    @staticmethod
    def _state_fingerprint(video: dict[str, Any]) -> str:
        return canonical_hash(
            {
                "title": video.get("title"),
                "description": video.get("description"),
                "defaultLanguage": video.get("defaultLanguage"),
                "localizations": video.get("localizations", {}),
            }
        )

    @staticmethod
    def _translations(value: Any) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict) or not value:
            raise ToolFailure("translations must be a non-empty object keyed by language code.")
        normalized: dict[str, dict[str, str]] = {}
        for language, localization in value.items():
            code = str(language).strip()
            if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", code):
                raise ToolFailure(f"Invalid language code: {code!r}.")
            if not isinstance(localization, dict):
                raise ToolFailure(f"Translation for {code} must be an object.")
            title = str(localization.get("title", "")).strip()
            description = str(localization.get("description", ""))
            if not title:
                raise ToolFailure(f"Translation title for {code} is empty.")
            if len(title) > TITLE_LIMIT:
                raise ToolFailure(
                    f"Translation title for {code} is {len(title)} characters; limit is {TITLE_LIMIT}."
                )
            if "<" in title or ">" in title:
                raise ToolFailure(f"Translation title for {code} cannot contain '<' or '>'.")
            description_bytes = len(description.encode("utf-8"))
            if description_bytes > DESCRIPTION_LIMIT:
                raise ToolFailure(
                    f"Translation description for {code} is {description_bytes} UTF-8 bytes; "
                    f"limit is {DESCRIPTION_LIMIT} bytes."
                )
            if "<" in description or ">" in description:
                raise ToolFailure(f"Translation description for {code} cannot contain '<' or '>'.")
            normalized[code] = {"title": title, "description": description}
        return normalized

    # Two-phase update

    def prepare_update(self, args: dict[str, Any]) -> dict[str, Any]:
        video_id = self._video_id(args)
        translations = self._translations(args.get("translations"))
        selected = self._languages(
            args.get("selected_languages", list(translations)), allow_empty=False
        )
        unknown = sorted(set(selected) - set(translations))
        if unknown:
            raise ToolFailure(f"Selected languages are missing translations: {', '.join(unknown)}")
        video = self._get_video(video_id)
        etag = str(video.get("etag", "")).strip()
        if not etag:
            raise ToolFailure(
                "YouTube returned no ETag for this video. No update preview was created."
            )
        source_language = str(args.get("source_language", "")).strip()
        default_language_change = None
        if not video.get("defaultLanguage"):
            if not source_language or source_language == "auto":
                raise ToolFailure(
                    "This video has no default language. Supply the confirmed source_language "
                    "so the preview can include that required YouTube change."
                )
            if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", source_language):
                raise ToolFailure("source_language is not a valid language code.")
            default_language_change = {"from": None, "to": source_language}

        existing = video.get("localizations", {})
        merged = dict(existing)
        changes: list[dict[str, Any]] = []
        for language in selected:
            proposed = translations[language]
            previous = existing.get(language)
            if previous == proposed:
                action = "unchanged"
            elif previous:
                action = "overwrite"
            else:
                action = "add"
            changes.append(
                {
                    "language": language,
                    "action": action,
                    "before": previous,
                    "after": proposed,
                    "title_characters": len(proposed["title"]),
                    "description_characters": len(proposed["description"]),
                    "description_bytes": len(proposed["description"].encode("utf-8")),
                }
            )
            merged[language] = proposed

        token = secrets.token_urlsafe(24)
        pending = {
            "video_id": video_id,
            "selected_languages": selected,
            "merged_localizations": merged,
            "changes": changes,
            "source_language": source_language or video.get("defaultLanguage"),
            "set_default_language": bool(default_language_change),
            "state_fingerprint": self._state_fingerprint(video),
            "etag": etag,
            "expires_at": time.time() + PENDING_TTL_SECONDS,
        }
        self._pending[token] = pending
        return {
            "ready": True,
            "video": {"id": video_id, "title": video["title"], "url": video["url"]},
            "changes": changes,
            "default_language_change": default_language_change,
            "preserved_existing_localizations": len(set(existing) - set(selected)),
            "confirmation_token": token,
            "confirmation_expires_in_seconds": PENDING_TTL_SECONDS,
            "warning": "Commit only after the user explicitly approves this exact diff.",
        }

    def commit_update(self, args: dict[str, Any]) -> dict[str, Any]:
        token = str(args.get("confirmation_token", "")).strip()
        pending = self._pending.get(token)
        if not pending:
            raise ToolFailure("Confirmation token is missing or unknown. Prepare a new preview.")
        if pending["expires_at"] < time.time():
            self._pending.pop(token, None)
            raise ToolFailure("Confirmation token expired. Prepare and review a new preview.")
        video = self._get_video(pending["video_id"])
        if (
            self._state_fingerprint(video) != pending["state_fingerprint"]
            or video.get("etag") != pending["etag"]
        ):
            self._pending.pop(token, None)
            raise ToolFailure(
                "The video changed after preview. No update was sent. Fetch it and prepare a new diff."
            )

        body: dict[str, Any] = {
            "id": pending["video_id"],
            "localizations": pending["merged_localizations"],
        }
        part = "localizations"
        if pending["set_default_language"]:
            snippet: dict[str, Any] = {
                "title": video["title"],
                "description": video["description"],
                "categoryId": video.get("categoryId"),
                "defaultLanguage": pending["source_language"],
            }
            if video.get("tags"):
                snippet["tags"] = video["tags"]
            if video.get("defaultAudioLanguage"):
                snippet["defaultAudioLanguage"] = video["defaultAudioLanguage"]
            body["snippet"] = snippet
            part = "snippet,localizations"

        try:
            response = self._api(
                "videos",
                {"part": part},
                method="PUT",
                body=body,
                extra_headers={"If-Match": pending["etag"]},
            )
        except GoogleApiFailure as exc:
            if exc.status == 412:
                self._pending.pop(token, None)
                raise ToolFailure(
                    "The video changed during commit. YouTube rejected the update. "
                    "Fetch it and prepare a new diff."
                ) from exc
            raise
        self._pending.pop(token, None)
        return {
            "saved": True,
            "video_id": pending["video_id"],
            "saved_languages": pending["selected_languages"],
            "saved_at": utc_timestamp(),
            "youtube_response_id": response.get("id"),
        }


def tool_definitions() -> list[dict[str, Any]]:
    object_schema = {"type": "object", "additionalProperties": False}
    return [
        {
            "name": "youtube_configure_oauth",
            "description": "Read and store a Google Desktop OAuth client from a local JSON file.",
            "inputSchema": {
                **object_schema,
                "properties": {
                    "client_json_path": {
                        "type": "string",
                        "description": "Local path to a downloaded Google Desktop OAuth client JSON file.",
                    },
                },
                "required": ["client_json_path"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "youtube_auth_start",
            "description": "Start Google OAuth and return a browser URL for the user to approve YouTube access.",
            "inputSchema": {**object_schema, "properties": {}},
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "openWorldHint": True,
            },
        },
        {
            "name": "youtube_auth_status",
            "description": "Check local YouTube connection status and finish a pending OAuth callback.",
            "inputSchema": {**object_schema, "properties": {}},
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "openWorldHint": True,
            },
        },
        {
            "name": "youtube_prepare_disconnect",
            "description": "Preview Google token revocation and local token deletion without changing anything.",
            "inputSchema": {**object_schema, "properties": {}},
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "youtube_commit_disconnect",
            "description": "Revoke Google access and delete the local token after explicit approval of a disconnect preview.",
            "inputSchema": {
                **object_schema,
                "properties": {"confirmation_token": {"type": "string"}},
                "required": ["confirmation_token"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "openWorldHint": True,
            },
        },
        {
            "name": "youtube_list_videos",
            "description": "List recent videos from the connected creator's uploads playlist.",
            "inputSchema": {
                **object_schema,
                "properties": {
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "page_token": {"type": "string"},
                },
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
        },
        {
            "name": "youtube_get_video",
            "description": "Read editable source metadata and all existing localizations for one YouTube video.",
            "inputSchema": {
                **object_schema,
                "properties": {"video_id": {"type": "string"}},
                "required": ["video_id"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
        },
        {
            "name": "youtube_get_channel_settings",
            "description": "Read locally stored translation defaults for a YouTube channel.",
            "inputSchema": {**object_schema, "properties": {"channel_id": {"type": "string"}}},
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "youtube_save_channel_settings",
            "description": "Store source language, target languages, instructions, and UI language locally per channel.",
            "inputSchema": {
                **object_schema,
                "properties": {
                    "channel_id": {"type": "string"},
                    "source_language": {"type": "string", "default": "auto"},
                    "target_languages": {"type": "array", "items": {"type": "string"}},
                    "instructions": {"type": "string"},
                    "interface_language": {"type": "string", "enum": ["en", "uk"], "default": "uk"},
                },
                "required": ["target_languages"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "youtube_save_draft",
            "description": "Save generated localization drafts locally without changing YouTube.",
            "inputSchema": {
                **object_schema,
                "properties": {
                    "video_id": {"type": "string"},
                    "translations": {"type": "object"},
                    "selected_languages": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["video_id", "translations"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "openWorldHint": True,
            },
        },
        {
            "name": "youtube_get_draft",
            "description": "Restore a local draft and report whether source metadata has changed.",
            "inputSchema": {
                **object_schema,
                "properties": {"video_id": {"type": "string"}},
                "required": ["video_id"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
        },
        {
            "name": "youtube_prepare_update",
            "description": "Build an exact localization diff and one-time confirmation token; does not modify YouTube.",
            "inputSchema": {
                **object_schema,
                "properties": {
                    "video_id": {"type": "string"},
                    "translations": {"type": "object"},
                    "selected_languages": {"type": "array", "items": {"type": "string"}},
                    "source_language": {"type": "string"},
                },
                "required": ["video_id", "translations"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
        },
        {
            "name": "youtube_commit_update",
            "description": "Save a previously previewed diff to YouTube. Call only after explicit user approval of that exact preview.",
            "inputSchema": {
                **object_schema,
                "properties": {"confirmation_token": {"type": "string"}},
                "required": ["confirmation_token"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "openWorldHint": True,
            },
        },
    ]


def dispatch(server: YouTubeLocalizer, name: str, args: dict[str, Any]) -> dict[str, Any]:
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "youtube_configure_oauth": server.configure_oauth,
        "youtube_auth_start": server.auth_start,
        "youtube_auth_status": server.auth_status,
        "youtube_prepare_disconnect": server.prepare_disconnect,
        "youtube_commit_disconnect": server.commit_disconnect,
        "youtube_list_videos": server.list_videos,
        "youtube_get_video": server.get_video,
        "youtube_get_channel_settings": server.get_channel_settings,
        "youtube_save_channel_settings": server.save_channel_settings,
        "youtube_save_draft": server.save_draft,
        "youtube_get_draft": server.get_draft,
        "youtube_prepare_update": server.prepare_update,
        "youtube_commit_update": server.commit_update,
    }
    handler = handlers.get(name)
    if not handler:
        return tool_error(f"Unknown tool: {name}")
    try:
        return result_payload(handler(args))
    except ToolFailure as exc:
        return tool_error(str(exc))
    except Exception as exc:  # keep protocol alive without leaking a traceback to the user
        print(f"Unexpected tool error in {name}: {exc!r}", file=sys.stderr, flush=True)
        return tool_error(f"Unexpected local error while running {name}.")


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> None:
    server = YouTubeLocalizer()
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            request_id = message.get("id")
            if method == "initialize" and request_id is not None:
                requested_version = message.get("params", {}).get("protocolVersion")
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": requested_version or "2025-06-18",
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                        },
                    }
                )
            elif method == "tools/list" and request_id is not None:
                send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": tool_definitions()}})
            elif method == "tools/call" and request_id is not None:
                params = message.get("params", {})
                args = params.get("arguments") or {}
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": dispatch(server, str(params.get("name", "")), args),
                    }
                )
            elif request_id is not None:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        except Exception as exc:
            print(f"Protocol error: {exc!r}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
