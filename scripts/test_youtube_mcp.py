#!/usr/bin/env python3
"""Offline tests for review gating and localization preservation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from youtube_mcp import GoogleApiFailure, ToolFailure, YouTubeLocalizer, tool_definitions


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


class FakeYouTubeLocalizer(YouTubeLocalizer):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.video = json.loads(json.dumps(VIDEO))
        self.sent: list[dict] = []
        self.update_error: Exception | None = None

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


class ProtocolTests(unittest.TestCase):
    def test_tool_names_are_unique(self) -> None:
        names = [tool["name"] for tool in tool_definitions()]
        self.assertEqual(len(names), len(set(names)))

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
        self.assertGreaterEqual(len(responses[1]["result"]["tools"]), 10)


if __name__ == "__main__":
    unittest.main()
