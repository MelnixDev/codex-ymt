# Codex YMT

**YouTube Metadata Translator for Codex**

[![Release](https://img.shields.io/github/v/release/MelnixDev/codex-ymt)](https://github.com/MelnixDev/codex-ymt/releases)
[![Tests](https://github.com/MelnixDev/codex-ymt/actions/workflows/tests.yml/badge.svg)](https://github.com/MelnixDev/codex-ymt/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)

> [!IMPORTANT]
> **Early development:** start with an unlisted test video and review every overwrite before publishing. Version `0.0.x` may change as the workflow is tested by more creators.

Codex YMT is an open-source **Codex plugin** and local **MCP server** for YouTube metadata localization. It works as a YouTube title translator and YouTube description translator inside Codex: generate natural translations, review each language, preserve existing localizations, and publish only the changes you explicitly approve through the YouTube Data API v3.

It is designed for multilingual YouTube metadata and creator workflows. It can help maintain localized titles and descriptions for multilingual discoverability, but it does not promise YouTube SEO rankings. It does not translate audio, subtitles, thumbnails, or video content.

## Contents

- [What it does](#what-it-does)
- [Safety model](#safety-model)
- [Requirements](#requirements)
- [Install](#install)
- [Set up Google OAuth](#set-up-google-oauth)
- [First safe run](#first-safe-run)
- [Example prompts](#example-prompts)
- [Update or uninstall](#update-or-uninstall)
- [Privacy and local data](#privacy-and-local-data)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Official documentation](#official-documentation)

## What it does

- Translates YouTube video titles and descriptions into multiple languages with Codex.
- Lets you review, edit, or regenerate each language independently.
- Stores source language, target languages, and translation instructions per channel.
- Saves local drafts and restores them in a later Codex task.
- Shows exact additions and overwrites before publishing.
- Preserves existing YouTube localizations that you did not select.
- Refuses to publish if the video changed after the preview was prepared.
- Uses the video's ETag and `If-Match` to reject concurrent changes during the final API request.
- Revokes Google access through a separate preview-and-confirm disconnect flow.
- Uses your own Google OAuth client and has no separate plugin backend.
- Works in English or Ukrainian conversations.
- Requires no separate OpenAI or Anthropic API key; translation is performed by Codex.

## Safety model

Codex YMT intentionally exposes a narrow set of YouTube operations.

| The plugin can | The plugin cannot |
| --- | --- |
| Read videos owned by the connected channel | Delete a video |
| Read titles, descriptions, languages, and localizations | Upload or replace video content |
| Save drafts and channel preferences locally | Change public, private, or unlisted visibility |
| Update reviewed title and description localizations | Edit comments, playlists, captions, monetization, or thumbnails |

Publishing is split into two server-enforced operations:

1. `youtube_prepare_update` returns the exact diff and a short-lived confirmation token without changing YouTube.
2. `youtube_commit_update` accepts that token and writes the reviewed language selection.

Before commit, the plugin fetches the video again and refuses to write if its title, description, default language, localizations, or ETag changed after preview. The final request includes `If-Match`, so YouTube can reject a change that races with the commit itself. The update merges existing, unselected localizations because YouTube requires localization updates to include the existing localized data.

> [!WARNING]
> Google describes the required `youtube.force-ssl` OAuth scope broadly: it can authorize viewing, editing, and permanently deleting YouTube videos and other channel data. Codex YMT does **not** expose deletion, upload, visibility, comment, or caption tools, but a stolen token would still carry the underlying Google scope. Keep the OAuth JSON and local token files private, and [revoke access in your Google Account](https://myaccount.google.com/connections) if they may have been exposed.

See [SECURITY.md](SECURITY.md) for the complete security boundaries and reporting process.

## Requirements

- Codex or the ChatGPT desktop app with plugin and local MCP support.
- Python 3.10 or newer available as `python3`.
- A Google account that can edit the target YouTube channel.
- A Google Cloud project with YouTube Data API v3 enabled.
- A Google OAuth client with application type **Desktop app**.

The MCP runtime is dependency-free and uses only the Python standard library.

## Install

Add this repository as a Codex marketplace and install the plugin:

```bash
codex plugin marketplace add MelnixDev/codex-ymt
codex plugin add codex-ymt@codex-ymt
```

Restart the desktop app and begin a new task so Codex loads the newly installed skill and MCP tools.

You can confirm the installation from a terminal:

```bash
codex plugin list
```

## Set up Google OAuth

Each creator uses their own Google Cloud project and OAuth client. Codex YMT does not provide a shared OAuth application or proxy your Google credentials through a third-party backend.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. Open the [YouTube Data API v3 library page](https://console.cloud.google.com/apis/library/youtube.googleapis.com) and enable the API for that project.
3. Configure the [Google Auth Platform](https://console.cloud.google.com/auth/overview). For personal testing, keep the app in testing and add your Google account as a test user.
4. Open [OAuth clients](https://console.cloud.google.com/auth/clients), create a client, and select **Desktop app** as the application type.
5. Download the OAuth client JSON and keep it outside this repository. Web application OAuth clients are not supported.
6. In Codex, ask: `Connect Codex YMT to YouTube.`
7. When asked, provide only the local filesystem path to the downloaded JSON. Do not paste its contents into the conversation.
8. Open the Google authorization URL returned by the plugin, choose the channel-owning Google account, and approve access.

The plugin uses PKCE, a temporary `127.0.0.1` loopback callback, and offline access so it can refresh the local token. The authorization URL is opened in your normal browser; the plugin never receives your Google password.

For a headless setup, `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` may be configured in the MCP process environment. Codex YMT does not accept client secrets as tool arguments, which keeps them out of the Codex task and tool history.

## First safe run

1. Create or choose an **unlisted test video**.
2. Ask Codex to translate one or two target languages without publishing.
3. Review title and description character counts and every `add` or `overwrite` entry.
4. Ask Codex to prepare the final diff.
5. Confirm only after the displayed diff is correct.
6. Open YouTube Studio and verify the saved localizations.

A request to translate, draft, regenerate, or prepare is never treated as permission to publish.

## Example prompts

Generate a draft without writing to YouTube:

```text
Translate the title and description of my latest YouTube video into Ukrainian,
Polish, and German. Show me the review, but do not publish anything yet.
```

Apply channel-specific style instructions:

```text
Remember that this channel uses informal language, preserves product names in
English, and never translates URLs or timestamps. Use Ukrainian as the source
language and Polish, German, and English as the default target languages.
```

Continue an unfinished draft:

```text
Restore the localization draft for this video and regenerate only Polish.
Keep the other approved languages unchanged.
```

Prepare a write for final approval:

```text
Prepare the exact YouTube diff for the selected languages. Show all overwrites
and preserved localizations, then ask before saving.
```

## Update or uninstall

Refresh the Git-backed marketplace and reinstall the current plugin version:

```bash
codex plugin marketplace upgrade codex-ymt
codex plugin add codex-ymt@codex-ymt
```

Restart the desktop app and use a new task after updating.

### Upgrading from v0.0.1

`youtube_configure_oauth` no longer accepts direct `client_id` or `client_secret` arguments. Provide `client_json_path`, or configure the environment variables documented above. Reconfiguring with a different OAuth client removes the incompatible local token and requires Google authorization again; reconfiguring the same client preserves its token.

Remove the installed plugin:

```bash
codex plugin remove codex-ymt@codex-ymt
```

Optionally remove its marketplace source:

```bash
codex plugin marketplace remove codex-ymt
```

Before uninstalling, ask Codex to disconnect YouTube. Codex YMT will preview the exact effect, request confirmation, revoke the Google token, and delete only the local token file. If remote revocation fails, the local token is retained so the operation can be retried.

Uninstalling the plugin itself does not automatically revoke Google authorization or delete local data. See the next section for a complete disconnect or manual fallback.

## Privacy and local data

Codex YMT has no separate backend, analytics, advertising, or telemetry. It stores the following locally:

- Google OAuth client details and access/refresh tokens.
- Per-channel source language, target languages, interface language, and translation instructions.
- Per-video translation drafts and short-lived pending confirmations.

Where supported, files are written atomically with owner-only permissions (`0600`). Tokens are not currently encrypted with the operating system keychain. Video metadata and translations appear in the Codex task so Codex can perform the requested work, and YouTube metadata is sent directly to Google through the YouTube Data API.

To disconnect completely:

1. Ask Codex: `Disconnect Codex YMT from YouTube.`
2. Review the disconnect preview and explicitly approve it.
3. Uninstall the plugin if you no longer need it.
4. Optionally delete the plugin data directory shown by your Codex installation, or the fallback `~/.config/codex-ymt/` directory, to remove OAuth client configuration, settings, and drafts.

If the plugin cannot run, [revoke the Google connection manually](https://myaccount.google.com/connections) before deleting local files.

Read [PRIVACY.md](PRIVACY.md) before using the plugin with private or commercially sensitive video metadata.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Plugin does not appear | Run `codex plugin list`, restart the desktop app, and start a new task. |
| `python3` is unavailable | Install Python 3.10+ and verify `python3 --version`. |
| `access_denied` | Add the Google account under OAuth test users and retry consent. |
| `redirect_uri_mismatch` | Recreate the OAuth client as **Desktop app**, not Web application. |
| Web OAuth client rejected | Download credentials for a **Desktop app** client; web clients are intentionally unsupported. |
| `accessNotConfigured` | Enable YouTube Data API v3 in the same project as the OAuth client. |
| `insufficientPermissions` | Reconnect and approve the requested YouTube scope. |
| No refresh token | Revoke the Google connection, then connect again to trigger fresh consent. |
| Video cannot be found | Confirm the connected account can edit that exact channel and video. |
| Preview became stale | Fetch the video again, prepare a new diff, and approve the new preview. |
| YouTube rejects a translation | Keep titles at 100 Unicode characters or fewer, descriptions at 5,000 UTF-8 bytes or fewer, and remove `<` or `>`. |
| Update preview became stale during commit | Another client changed the video. Fetch it again, prepare a new diff, and approve that new preview. |
| Disconnect revocation failed | The local token was retained. Check connectivity and prepare a new disconnect preview. |

YouTube API quota and Google account policies still apply.

## Versioning

- `0.0.x`: early releases and focused fixes while the workflow is validated.
- `0.1.0`: a larger milestone after broader OAuth, install, update, and publishing tests.
- `1.0.0`: a stable workflow with documented compatibility expectations.

## Development

Clone the repository and run the dependency-free offline tests:

```bash
python3 scripts/test_youtube_mcp.py
```

The tests cover existing-localization preservation, confirmation-token gating, ETag concurrency protection, UTF-8 metadata limits, Desktop OAuth validation, safe token reset/revocation, path redaction, and MCP initialization.

Validate the plugin manifest with the Codex `plugin-creator` validator when that system skill is available:

```bash
python3 /path/to/plugin-creator/scripts/validate_plugin.py .
```

## Official documentation

- [Build Codex plugins](https://developers.openai.com/codex/plugins/build)
- [Codex CLI](https://developers.openai.com/codex/cli)
- [YouTube Data API v3 overview](https://developers.google.com/youtube/v3/getting-started)
- [OAuth 2.0 for mobile and desktop apps](https://developers.google.com/youtube/v3/guides/auth/installed-apps)
- [Google token revocation endpoint](https://developers.google.com/identity/openid-connect/reference#revocation_endpoint)
- [`videos.update` reference](https://developers.google.com/youtube/v3/docs/videos/update)
- [YouTube Data API revision history](https://developers.google.com/youtube/v3/revision_history)
- [Google OAuth app verification](https://support.google.com/cloud/answer/13461325?hl=en)

## Disclaimer

Codex YMT is an independent open-source project. It is not affiliated with or endorsed by YouTube, Google, OpenAI, or Anthropic.

## License

MIT — see [LICENSE](LICENSE).
