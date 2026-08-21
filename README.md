# Codex YMT

**Open-source YouTube Title & Description Translator for Codex**

[![Release](https://img.shields.io/github/v/release/MelnixDev/codex-ymt)](https://github.com/MelnixDev/codex-ymt/releases)
[![Tests](https://github.com/MelnixDev/codex-ymt/actions/workflows/tests.yml/badge.svg)](https://github.com/MelnixDev/codex-ymt/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)

> [!IMPORTANT]
> **Early development:** start with an unlisted test video and review every overwrite before publishing. Version `0.0.x` may change as the workflow is tested by more creators.

Codex YMT is an open-source **Codex plugin** and local **MCP server** for YouTube metadata translation and localization. Use it as a YouTube title translator and YouTube description translator inside Codex: generate natural translations, review each language, preserve existing localizations, and publish only the changes you explicitly approve through the YouTube Data API v3.

It is designed for multilingual YouTube metadata, international creator workflows, and multilingual YouTube SEO. It helps maintain localized video titles and descriptions for global discoverability, but it does not promise search rankings. It does not translate audio, subtitles, thumbnails, or video content.

## Contents

- [How it works](#how-it-works)
- [What it does](#what-it-does)
- [Safety model](#safety-model)
- [Requirements](#requirements)
- [Install](#install)
- [60-second first request](#60-second-first-request)
- [Set up Google OAuth](#set-up-google-oauth)
- [Use another YouTube channel](#use-another-youtube-channel)
- [First safe run](#first-safe-run)
- [Example review and approval](#example-review-and-approval)
- [Example prompts](#example-prompts)
- [Update or uninstall](#update-or-uninstall)
- [Privacy and local data](#privacy-and-local-data)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Development](#development)
- [Official documentation](#official-documentation)

## How it works

1. **Install Codex YMT** and connect the YouTube channel you manage.
2. **Ask Codex in normal language** which video and languages you want.
3. **Codex creates the translations** and saves them as a local draft. Nothing is published yet.
4. **Review every language** and edit or regenerate individual titles and descriptions.
5. **Preview the exact YouTube diff**, including additions, overwrites, and preserved localizations.
6. **Explicitly approve the displayed diff** to save only the selected languages to YouTube.

> [!NOTE]
> Codex writes the translation text. The local MCP server reads YouTube metadata, stores drafts and channel preferences, and publishes only reviewed localizations after confirmation. There is no separate translation backend or additional OpenAI/Anthropic API key.

Codex YMT adds YouTube's built-in localized metadata. It does not replace the original title or description, translate subtitles or audio, or change the video's visibility. If the source video's default language is missing, the required default-language change appears in the preview before anything is saved.

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

## 60-second first request

After installation and the one-time Google Cloud setup below, start a new Codex task and send:

```text
Connect Codex YMT to YouTube. Then translate the title and description of my
latest video into Ukrainian and Polish. Save a local draft and show me the
review, but do not publish anything yet.
```

Codex will ask for the local path to your downloaded Desktop OAuth JSON when needed. Give it only the path, not the JSON contents. Complete Google consent in the browser, return to Codex, and confirm that the channel title and video are correct.

The first Google Cloud setup takes longer than 60 seconds. Later translation requests use the saved local connection until it expires or you disconnect it.

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

## Use another YouTube channel

Codex YMT keeps one active Google OAuth connection at a time. To switch to another YouTube channel or Brand Account:

1. If one Google account manages multiple channels, set the target channel as the [default channel for third-party apps](https://support.google.com/youtube/answer/3046478?hl=en).
2. Ask Codex to disconnect Codex YMT from YouTube.
3. Review and approve the disconnect preview. This revokes and removes only the active token; the OAuth client configuration, per-channel settings, and translation drafts are preserved.
4. Ask Codex to connect Codex YMT to YouTube again and use the Google account that manages the target channel.
5. List the latest videos and confirm the returned channel title before preparing any localization update. Stop if the wrong channel is shown.

The same Desktop OAuth client can be reused. If its consent screen is in **Testing**, add every Google account used for channel access as a test user. Preferences remain separated by YouTube channel ID, and drafts remain separated by video ID.

Codex YMT does not provide its own channel picker. YouTube may use the Google account's default channel for third-party API applications. If a Brand Account is missing, confirm that the connected Google account is allowed to manage it, set the correct default channel, reconnect, and verify the returned channel title.

## First safe run

1. Create or choose an **unlisted test video**.
2. Ask Codex to translate one or two target languages without publishing.
3. Review title and description character counts and every `add` or `overwrite` entry.
4. Ask Codex to prepare the final diff.
5. Confirm only after the displayed diff is correct.
6. Open YouTube Studio and verify the saved localizations.

A request to translate, draft, regenerate, or prepare is never treated as permission to publish.

## Example review and approval

Before saving, Codex should show a review similar to this:

```text
Video: Spring product update
Source language: English

Ukrainian — add
Title: Весняне оновлення продукту
Description: 184 characters / 307 UTF-8 bytes

Polish — overwrite
Previous title: Wiosenna aktualizacja
New title: Wiosenna aktualizacja produktu
Description: 172 characters / 179 UTF-8 bytes

Existing localizations preserved: 3
Nothing has been saved to YouTube yet.
```

After you ask Codex to prepare the final update, it shows the exact diff and asks for confirmation. Approve only that displayed preview, for example:

```text
I reviewed this preview. Save only Ukrainian and Polish.
```

If you change any translation after the preview, prepare and review a new diff. If the YouTube video changes before commit, the plugin rejects the stale preview instead of overwriting newer metadata.

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

### Upgrading from v0.0.2

No local data migration or Google reconnection is required. Version `0.0.3` keeps the same MCP tools and successful response shapes while adding regression coverage, defensive input validation, and clearer Google API errors.

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
| Connection expires after seven days | If an external OAuth app remains in **Testing**, Google may issue a refresh token that expires in seven days. Reconnect, or move the consent screen to the appropriate publishing status after reviewing Google's requirements. |
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
| YouTube API quota is exhausted | Wait for the quota reset or review the project quota in Google Cloud. A `videos.update` call costs 50 quota units. |

YouTube API quota and Google account policies still apply.

## FAQ

### Can Codex YMT delete a video or change its visibility?

No. The plugin exposes no tool for deleting videos, uploading content, or changing public, private, or unlisted visibility. Google grants a broader OAuth scope than the plugin uses, so the local OAuth files must still be protected.

### Does it translate subtitles, audio, or thumbnails?

No. It translates only YouTube's localized video title and description fields.

### Do I need an OpenAI or Anthropic API key?

No additional AI-provider key is required. Codex generates the translations; your own Google OAuth client is used only to read YouTube metadata and publish approved localizations.

### Can it publish automatically after translation?

No. Translation and revision create a local draft. Publishing requires a fresh diff followed by explicit approval of that exact preview.

### What happens to existing YouTube localizations?

Unselected languages are preserved. A selected language is labeled `add`, `overwrite`, or `unchanged` before approval.

### Can I use multiple YouTube channels?

Yes, one active Google connection at a time. Follow [Use another YouTube channel](#use-another-youtube-channel) when switching. Channel preferences are stored per channel ID; drafts are stored per video ID.

### Where is my data stored?

OAuth credentials, tokens, channel settings, and drafts stay in the plugin's local data directory. Codex YMT has no separate backend, analytics, advertising, or telemetry. See [Privacy and local data](#privacy-and-local-data).

## Versioning

- `0.0.x`: early releases and focused fixes while the workflow is validated.
- `0.1.0`: a larger milestone after broader OAuth, install, update, and publishing tests.
- `1.0.0`: a stable workflow with documented compatibility expectations.

## Development

Clone the repository and run the dependency-free offline tests:

```bash
python3 scripts/test_youtube_mcp.py
```

The tests cover existing-localization preservation, one-time confirmation tokens, ETag concurrency protection, UTF-8 metadata limits, Desktop OAuth validation and refresh, safe token reset/revocation, upload-list pagination, default-language writes, path redaction, defensive MCP input handling, and protocol initialization.

Before a release, follow the [optional live smoke-test checklist](docs/release-checklist.md). Its read-only and preview checks can be completed without writing to YouTube. The isolated live-write step requires separate, explicit approval and must use an unlisted test video.

Validate the plugin manifest with the Codex `plugin-creator` validator when that system skill is available:

```bash
python3 /path/to/plugin-creator/scripts/validate_plugin.py .
```

Run the dependency-free repository metadata checks used by CI:

```bash
python3 scripts/validate_repository.py
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
- [Google OAuth refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- [YouTube Data API quota costs](https://developers.google.com/youtube/v3/determine_quota_cost)

## Disclaimer

Codex YMT is an independent open-source project. It is not affiliated with or endorsed by YouTube, Google, OpenAI, or Anthropic.

## License

MIT — see [LICENSE](LICENSE).
