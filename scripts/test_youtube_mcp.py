#!/usr/bin/env python3
"""Offline tests for review gating and localization preservation."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

from youtube_mcp import (
    GOOGLE_REVOKE_URL,
    SERVER_VERSION,
    GoogleApiFailure,
    ToolFailure,
    YouTubeLocalizer,
    dispatch,
    tool_definitions,
)


VIDEO = {
    "id": "abc123xyz00",
    "etag": "\"etag-1\"",
    "channelId": "channel-1",
    "channelTitle": "Demo",
    "title": "Original title",
    "description": "Original description",
    "defaultLanguage": "en",
    "defaultAudioLanguage": None,
    "categoryId": "22",
    "tags": ["demo"],
    "localizations": {
        "de": {"title": "Bestehend", "description": "Bleibt erhalten"},
        "fr": {"title": "Ancien", "description": "Ancienne description"},
    },
    "localization_languages": ["de", "fr"],
    "url": "https://www.youtube.com/watch?v=abc123xyz00",
}

API_VIDEO = {
    "id": VIDEO["id"],
    "etag": VIDEO["etag"],
    "snippet": {
        "channelId": VIDEO["channelId"],
        "channelTitle": VIDEO["channelTitle"],
        "title": VIDEO["title"],
        "description": VIDEO["description"],
        "publishedAt": "2026-07-01T12:00:00Z",
        "defaultLanguage": VIDEO["defaultLanguage"],
        "defaultAudioLanguage": VIDEO["defaultAudioLanguage"],
        "categoryId": VIDEO["categoryId"],
        "tags": VIDEO["tags"],
    },
    "status": {"privacyStatus": "unlisted"},
    "localizations": VIDEO["localizations"],
}


class FakeYouTubeLocalizer(YouTubeLocalizer):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.video = json.loads(json.dumps(VIDEO))
        self.sent: list[dict] = []
        self.update_error: Exception | None = None
        self.form_requests: list[dict] = []
        self.form_error: Exception | None = None
        self.form_response: dict = {}

    def _get_video(self, _video_id: str) -> dict:
        return json.loads(json.dumps(self.video))

    def _api(
        self,
        resource: str,
        params: dict,
        method: str = "GET",
        body: dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        self.sent.append(
            {
                "resource": resource,
                "params": params,
                "method": method,
                "body": body,
                "extra_headers": extra_headers,
            }
        )
        if self.update_error:
            raise self.update_error
        return {"id": self.video["id"]}

    def _form_request(self, url: str, values: dict) -> dict:
        self.form_requests.append({"url": url, "values": values})
        if self.form_error:
            raise self.form_error
        return json.loads(json.dumps(self.form_response))


class ReadYouTubeLocalizer(YouTubeLocalizer):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        second_video = json.loads(json.dumps(API_VIDEO))
        second_video["id"] = "second98765"
        second_video["snippet"]["title"] = "Second video"
        self.api_videos = {
            API_VIDEO["id"]: json.loads(json.dumps(API_VIDEO)),
            second_video["id"]: second_video,
        }
        self.calls: list[dict] = []

    def _api(
        self,
        resource: str,
        params: dict,
        method: str = "GET",
        body: dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        self.calls.append(
            {
                "resource": resource,
                "params": params,
                "method": method,
                "body": body,
                "extra_headers": extra_headers,
            }
        )
        if resource == "channels":
            return {
                "items": [
                    {
                        "id": VIDEO["channelId"],
                        "snippet": {"title": VIDEO["channelTitle"]},
                        "contentDetails": {
                            "relatedPlaylists": {"uploads": "uploads-playlist"}
                        },
                    }
                ]
            }
        if resource == "playlistItems":
            return {
                "items": [
                    {"contentDetails": {"videoId": API_VIDEO["id"]}},
                    {"contentDetails": {"videoId": "second98765"}},
                ],
                "nextPageToken": "next-page",
            }
        if resource == "videos":
            requested = str(params["id"]).split(",")
            return {"items": [self.api_videos[video_id] for video_id in reversed(requested)]}
        raise AssertionError(f"Unexpected API resource: {resource}")


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.server = FakeYouTubeLocalizer(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepare_preserves_unselected_existing_localizations(self) -> None:
        preview = self.server.prepare_update(
            {
                "video_id": VIDEO["id"],
                "translations": {
                    "fr": {"title": "Nouveau", "description": "Nouvelle description"},
                    "uk": {"title": "Нове", "description": "Новий опис"},
                },
                "selected_languages": ["uk"],
            }
        )
        pending = self.server._pending[preview["confirmation_token"]]
        self.assertEqual(pending["merged_localizations"]["de"], VIDEO["localizations"]["de"])
        self.assertEqual(pending["merged_localizations"]["fr"], VIDEO["localizations"]["fr"])
        self.assertEqual(preview["preserved_existing_localizations"], 2)

    def test_commit_requires_preview_token(self) -> None:
        with self.assertRaises(ToolFailure):
            self.server.commit_update({"confirmation_token": "not-a-token"})
        self.assertEqual(self.server.sent, [])

    def test_commit_rejects_changed_video(self) -> None:
        preview = self.server.prepare_update(
            {
                "video_id": VIDEO["id"],
                "translations": {"uk": {"title": "Нове", "description": "Новий опис"}},
            }
        )
        self.server.video["title"] = "Changed elsewhere"
        with self.assertRaisesRegex(ToolFailure, "changed after preview"):
            self.server.commit_update({"confirmation_token": preview["confirmation_token"]})
        self.assertEqual(self.server.sent, [])

    def test_commit_sends_merged_localizations(self) -> None:
        preview = self.server.prepare_update(
            {
                "video_id": VIDEO["id"],
                "translations": {"uk": {"title": "Нове", "description": "Новий опис"}},
            }
        )
        result = self.server.commit_update({"confirmation_token": preview["confirmation_token"]})
        self.assertTrue(result["saved"])
        body = self.server.sent[0]["body"]
        self.assertEqual(body["localizations"]["de"], VIDEO["localizations"]["de"])
        self.assertIn("uk", body["localizations"])
        self.assertEqual(self.server.sent[0]["extra_headers"], {"If-Match": VIDEO["etag"]})

    def test_successful_update_confirmation_is_one_time(self) -> None:
        preview = self.server.prepare_update(
            {
                "video_id": VIDEO["id"],
                "translations": {"uk": {"title": "Нове", "description": "Новий опис"}},
            }
        )
        token = preview["confirmation_token"]
        self.server.commit_update({"confirmation_token": token})
        with self.assertRaisesRegex(ToolFailure, "missing or unknown"):
            self.server.commit_update({"confirmation_token": token})
        self.assertEqual(len(self.server.sent), 1)

    def test_default_language_update_preserves_safe_snippet_fields(self) -> None:
        self.server.video["defaultLanguage"] = None
        self.server.video["defaultAudioLanguage"] = "en"
        preview = self.server.prepare_update(
            {
                "video_id": VIDEO["id"],
                "source_language": "en",
                "translations": {"uk": {"title": "Нове", "description": "Новий опис"}},
            }
        )
        self.assertEqual(preview["default_language_change"], {"from": None, "to": "en"})
        self.server.commit_update({"confirmation_token": preview["confirmation_token"]})

        request = self.server.sent[0]
        self.assertEqual(request["params"], {"part": "snippet,localizations"})
        self.assertEqual(
            request["body"]["snippet"],
            {
                "title": VIDEO["title"],
                "description": VIDEO["description"],
                "categoryId": VIDEO["categoryId"],
                "defaultLanguage": "en",
                "tags": VIDEO["tags"],
                "defaultAudioLanguage": "en",
            },
        )
        self.assertNotIn("status", request["body"])

    def test_title_limit(self) -> None:
        with self.assertRaisesRegex(ToolFailure, "limit is 100"):
            self.server.prepare_update(
                {
                    "video_id": VIDEO["id"],
                    "translations": {"uk": {"title": "x" * 101, "description": "ok"}},
                }
            )

    def test_description_limit_uses_utf8_bytes(self) -> None:
        accepted = self.server.prepare_update(
            {
                "video_id": VIDEO["id"],
                "translations": {"uk": {"title": "Нове", "description": "ї" * 2500}},
            }
        )
        self.assertEqual(accepted["changes"][0]["description_characters"], 2500)
        self.assertEqual(accepted["changes"][0]["description_bytes"], 5000)
        with self.assertRaisesRegex(ToolFailure, "5002 UTF-8 bytes"):
            self.server.prepare_update(
                {
                    "video_id": VIDEO["id"],
                    "translations": {"uk": {"title": "Нове", "description": "ї" * 2501}},
                }
            )

    def test_rejects_angle_brackets(self) -> None:
        with self.assertRaisesRegex(ToolFailure, "title.*cannot contain"):
            self.server.prepare_update(
                {
                    "video_id": VIDEO["id"],
                    "translations": {"uk": {"title": "Нове <відео>", "description": "ok"}},
                }
            )
        with self.assertRaisesRegex(ToolFailure, "description.*cannot contain"):
            self.server.prepare_update(
                {
                    "video_id": VIDEO["id"],
                    "translations": {"uk": {"title": "Нове", "description": "<b>опис</b>"}},
                }
            )

    def test_prepare_requires_etag(self) -> None:
        self.server.video.pop("etag")
        with self.assertRaisesRegex(ToolFailure, "no ETag"):
            self.server.prepare_update(
                {
                    "video_id": VIDEO["id"],
                    "translations": {"uk": {"title": "Нове", "description": "Опис"}},
                }
            )

    def test_commit_handles_etag_precondition_failure(self) -> None:
        preview = self.server.prepare_update(
            {
                "video_id": VIDEO["id"],
                "translations": {"uk": {"title": "Нове", "description": "Опис"}},
            }
        )
        token = preview["confirmation_token"]
        self.server.update_error = GoogleApiFailure(412, "Precondition Failed")
        with self.assertRaisesRegex(ToolFailure, "changed during commit"):
            self.server.commit_update({"confirmation_token": token})
        self.assertNotIn(token, self.server._pending)

    def test_configure_oauth_from_local_json(self) -> None:
        oauth_path = Path(self.temp.name) / "client.json"
        oauth_path.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": "demo.apps.googleusercontent.com",
                        "client_secret": "secret-value",
                    }
                }
            ),
            encoding="utf-8",
        )
        result = self.server.configure_oauth({"client_json_path": str(oauth_path)})
        self.assertEqual(result["source"], "local_json_file")
        stored = json.loads(self.server.credentials_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["client_id"], "demo.apps.googleusercontent.com")

    def _oauth_json(self, name: str, client_id: str, kind: str = "installed") -> Path:
        path = Path(self.temp.name) / name
        path.write_text(
            json.dumps(
                {
                    kind: {
                        "client_id": client_id,
                        "client_secret": "secret-value",
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_configure_oauth_requires_local_json_path(self) -> None:
        with self.assertRaisesRegex(ToolFailure, "client_json_path is required"):
            self.server.configure_oauth(
                {
                    "client_id": "demo.apps.googleusercontent.com",
                    "client_secret": "secret-value",
                }
            )

    def test_configure_oauth_rejects_web_client(self) -> None:
        path = self._oauth_json("web.json", "web.apps.googleusercontent.com", kind="web")
        with self.assertRaisesRegex(ToolFailure, "Web OAuth clients are not supported"):
            self.server.configure_oauth({"client_json_path": str(path)})

    def test_same_oauth_client_preserves_token(self) -> None:
        path = self._oauth_json("same.json", "same.apps.googleusercontent.com")
        self.server.configure_oauth({"client_json_path": str(path)})
        self.server.token_path.write_text('{"refresh_token":"keep-me"}', encoding="utf-8")
        result = self.server.configure_oauth({"client_json_path": str(path)})
        self.assertFalse(result["token_cleared"])
        self.assertTrue(self.server.token_path.exists())

    def test_valid_access_token_does_not_refresh(self) -> None:
        self.server.token_path.write_text(
            json.dumps({"access_token": "current-token", "expires_at": time.time() + 600}),
            encoding="utf-8",
        )
        self.assertEqual(self.server._access_token(), "current-token")
        self.assertEqual(self.server.form_requests, [])

    def test_expired_access_token_refreshes_and_preserves_refresh_token(self) -> None:
        self.server.credentials_path.write_text(
            json.dumps(
                {
                    "client_id": "demo.apps.googleusercontent.com",
                    "client_secret": "secret-value",
                }
            ),
            encoding="utf-8",
        )
        self.server.token_path.write_text(
            json.dumps(
                {
                    "access_token": "expired-token",
                    "refresh_token": "refresh-value",
                    "expires_at": 0,
                }
            ),
            encoding="utf-8",
        )
        self.server.form_response = {"access_token": "fresh-token", "expires_in": 3600}

        self.assertEqual(self.server._access_token(), "fresh-token")
        self.assertEqual(
            self.server.form_requests[0]["values"],
            {
                "client_id": "demo.apps.googleusercontent.com",
                "client_secret": "secret-value",
                "refresh_token": "refresh-value",
                "grant_type": "refresh_token",
            },
        )
        stored = json.loads(self.server.token_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["refresh_token"], "refresh-value")
        self.assertEqual(stat.S_IMODE(self.server.token_path.stat().st_mode), 0o600)

    def test_changed_oauth_client_clears_token(self) -> None:
        first = self._oauth_json("first.json", "first.apps.googleusercontent.com")
        second = self._oauth_json("second.json", "second.apps.googleusercontent.com")
        self.server.configure_oauth({"client_json_path": str(first)})
        self.server.token_path.write_text('{"refresh_token":"old-token"}', encoding="utf-8")
        result = self.server.configure_oauth({"client_json_path": str(second)})
        self.assertTrue(result["token_cleared"])
        self.assertTrue(result["reconnect_required"])
        self.assertFalse(self.server.token_path.exists())

    def test_local_json_errors_do_not_expose_path(self) -> None:
        path = Path(self.temp.name) / "private-client.json"
        path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(ToolFailure) as raised:
            self.server.configure_oauth({"client_json_path": str(path)})
        self.assertNotIn(str(path), str(raised.exception))

    def test_corrupt_token_json_returns_safe_error(self) -> None:
        self.server.token_path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(ToolFailure) as raised:
            self.server._access_token()
        self.assertNotIn(str(self.server.token_path), str(raised.exception))
        self.assertIn("invalid", str(raised.exception))

    def test_prepare_disconnect_without_token_is_noop(self) -> None:
        result = self.server.prepare_disconnect({})
        self.assertFalse(result["ready"])
        self.assertFalse(result["connected"])

    def test_disconnect_requires_valid_confirmation(self) -> None:
        self.server.token_path.write_text('{"refresh_token":"token-value"}', encoding="utf-8")
        with self.assertRaisesRegex(ToolFailure, "missing or unknown"):
            self.server.commit_disconnect({"confirmation_token": "unknown"})
        preview = self.server.prepare_disconnect({})
        token = preview["confirmation_token"]
        self.server._disconnect_pending[token]["expires_at"] = time.time() - 1
        with self.assertRaisesRegex(ToolFailure, "expired"):
            self.server.commit_disconnect({"confirmation_token": token})
        self.assertTrue(self.server.token_path.exists())

    def test_disconnect_revokes_and_deletes_local_token(self) -> None:
        self.server.token_path.write_text('{"refresh_token":"token-value"}', encoding="utf-8")
        preview = self.server.prepare_disconnect({})
        result = self.server.commit_disconnect(
            {"confirmation_token": preview["confirmation_token"]}
        )
        self.assertTrue(result["remote_revoked"])
        self.assertTrue(result["local_token_deleted"])
        self.assertFalse(self.server.token_path.exists())
        self.assertEqual(self.server.form_requests[0]["url"], GOOGLE_REVOKE_URL)
        self.assertEqual(self.server.form_requests[0]["values"], {"token": "token-value"})

    def test_successful_disconnect_confirmation_is_one_time(self) -> None:
        self.server.token_path.write_text('{"refresh_token":"token-value"}', encoding="utf-8")
        preview = self.server.prepare_disconnect({})
        token = preview["confirmation_token"]
        self.server.commit_disconnect({"confirmation_token": token})
        with self.assertRaisesRegex(ToolFailure, "missing or unknown"):
            self.server.commit_disconnect({"confirmation_token": token})
        self.assertEqual(len(self.server.form_requests), 1)

    def test_disconnect_rejects_changed_connection(self) -> None:
        self.server.token_path.write_text('{"refresh_token":"first-token"}', encoding="utf-8")
        preview = self.server.prepare_disconnect({})
        self.server.token_path.write_text('{"refresh_token":"second-token"}', encoding="utf-8")
        with self.assertRaisesRegex(ToolFailure, "connection changed"):
            self.server.commit_disconnect(
                {"confirmation_token": preview["confirmation_token"]}
            )
        self.assertTrue(self.server.token_path.exists())
        self.assertEqual(self.server.form_requests, [])

    def test_disconnect_treats_invalid_token_as_already_revoked(self) -> None:
        self.server.token_path.write_text('{"access_token":"token-value"}', encoding="utf-8")
        self.server.form_error = GoogleApiFailure(400, "Invalid token", "invalid_token")
        preview = self.server.prepare_disconnect({})
        result = self.server.commit_disconnect(
            {"confirmation_token": preview["confirmation_token"]}
        )
        self.assertTrue(result["already_revoked"])
        self.assertFalse(self.server.token_path.exists())

    def test_disconnect_network_failure_retains_local_token(self) -> None:
        self.server.token_path.write_text('{"refresh_token":"token-value"}', encoding="utf-8")
        self.server.form_error = ToolFailure("Could not reach Google API")
        preview = self.server.prepare_disconnect({})
        token = preview["confirmation_token"]
        with self.assertRaisesRegex(ToolFailure, "Could not reach"):
            self.server.commit_disconnect({"confirmation_token": token})
        self.assertTrue(self.server.token_path.exists())
        self.assertIn(token, self.server._disconnect_pending)


class ProtocolTests(unittest.TestCase):
    def test_tool_names_are_unique(self) -> None:
        names = [tool["name"] for tool in tool_definitions()]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 13)

    def test_server_version_matches_plugin_manifest(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(SERVER_VERSION, manifest["version"])

    def test_write_tool_annotations_are_conservative(self) -> None:
        tools = {tool["name"]: tool for tool in tool_definitions()}
        self.assertTrue(tools["youtube_commit_update"]["annotations"]["destructiveHint"])
        self.assertFalse(tools["youtube_auth_status"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["youtube_commit_disconnect"]["annotations"]["destructiveHint"])

    def test_stdio_initialize_and_tool_list(self) -> None:
        script = Path(__file__).with_name("youtube_mcp.py")
        messages = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                "",
            ]
        )
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=messages,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "codex-ymt")
        self.assertEqual(responses[0]["result"]["serverInfo"]["version"], SERVER_VERSION)
        self.assertEqual(len(responses[1]["result"]["tools"]), 13)

    def test_stdio_rejects_non_object_params(self) -> None:
        script = Path(__file__).with_name("youtube_mcp.py")
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": ["not-an-object"],
                }
            )
            + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        response = json.loads(completed.stdout)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("expected an object", response["error"]["message"])

    def test_stdio_rejects_non_object_tool_arguments(self) -> None:
        script = Path(__file__).with_name("youtube_mcp.py")
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "youtube_get_video",
                        "arguments": ["not-an-object"],
                    },
                }
            )
            + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        response = json.loads(completed.stdout)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("arguments must be an object", response["error"]["message"])


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.server = ReadYouTubeLocalizer(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_list_videos_preserves_upload_order_and_pagination(self) -> None:
        result = self.server.list_videos({"max_results": 2, "page_token": "current-page"})
        self.assertEqual(result["channel"], {"id": "channel-1", "title": "Demo"})
        self.assertEqual([video["id"] for video in result["videos"]], [VIDEO["id"], "second98765"])
        self.assertEqual(result["next_page_token"], "next-page")
        playlist_call = next(call for call in self.server.calls if call["resource"] == "playlistItems")
        self.assertEqual(playlist_call["params"]["pageToken"], "current-page")
        self.assertEqual(playlist_call["params"]["maxResults"], 2)

    def test_get_video_returns_write_safety_fields(self) -> None:
        result = self.server.get_video({"video_id": VIDEO["id"]})
        self.assertEqual(result["etag"], VIDEO["etag"])
        self.assertEqual(result["privacyStatus"], "unlisted")
        self.assertEqual(result["localizations"], VIDEO["localizations"])
        self.assertEqual(result["tags"], VIDEO["tags"])
        self.assertEqual(result["categoryId"], VIDEO["categoryId"])

    def test_invalid_max_results_is_a_tool_error(self) -> None:
        for value in (None, "many", True, 1.5, [], {}):
            with self.subTest(value=value):
                result = dispatch(self.server, "youtube_list_videos", {"max_results": value})
                self.assertTrue(result["isError"])
                self.assertIn("max_results must be an integer", result["content"][0]["text"])
        self.assertEqual(self.server.calls, [])


class GoogleErrorTests(unittest.TestCase):
    def test_google_errors_are_actionable_and_keep_structured_details(self) -> None:
        cases = (
            (401, "Unauthorized", "authError", "Reconnect YouTube"),
            (403, "Forbidden", "quotaExceeded", "quota is exhausted"),
            (403, "Forbidden", "insufficientPermissions", "OAuth scope"),
            (400, "Bad request", "localizationValidationError", "rejected the video metadata"),
            (404, "Missing", "videoNotFound", "not found"),
        )
        for status, message, reason, expected in cases:
            with self.subTest(status=status, reason=reason):
                failure = GoogleApiFailure(status, message, reason)
                self.assertEqual(failure.status, status)
                self.assertEqual(failure.reason, reason)
                self.assertEqual(failure.api_message, message)
                self.assertIn(expected, str(failure))

    def test_unknown_google_error_is_bounded_and_single_line(self) -> None:
        failure = GoogleApiFailure(500, " first\n" + "x" * 600, "backendError")
        rendered = str(failure)
        self.assertTrue(rendered.startswith("Google API error (500): first "))
        self.assertNotIn("\n", rendered)
        self.assertLessEqual(len(rendered), len("Google API error (500): ") + 500)

    def test_disconnect_invalid_token_behavior_is_unchanged(self) -> None:
        failure = GoogleApiFailure(400, "Invalid token", "invalid_token")
        self.assertEqual(failure.status, 400)
        self.assertEqual(failure.reason, "invalid_token")
        self.assertIn("Invalid token", str(failure))

    def test_network_error_does_not_expose_low_level_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = YouTubeLocalizer(Path(temporary))
            private_detail = "sensitive low-level network detail"
            with patch("youtube_mcp.urlopen", side_effect=URLError(private_detail)):
                with self.assertRaises(ToolFailure) as raised:
                    server._open_json(Request("https://www.googleapis.com/youtube/v3/videos"))
            self.assertNotIn(private_detail, str(raised.exception))
            self.assertIn("Check network connectivity", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
