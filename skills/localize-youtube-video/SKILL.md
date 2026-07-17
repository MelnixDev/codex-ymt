---
name: localize-youtube-video
description: Translate, review, draft, regenerate, and save YouTube video title and description localizations. Use when a creator asks Codex to localize YouTube metadata, translate a video's title or description, manage per-channel target languages or translation instructions, restore a localization draft, or publish approved localizations through the YouTube Data API.
---

# Localize a YouTube video

Use the `codex-ymt` MCP tools for YouTube data and local storage. Generate translations directly with Codex; do not ask for an OpenAI or Anthropic API key.

## Connect

1. Call `youtube_auth_status`.
2. If OAuth is not configured, read [references/google-oauth.md](references/google-oauth.md). Ask for the local path to a downloaded Desktop OAuth JSON and call `youtube_configure_oauth` with `client_json_path`. For headless environments, the user can configure `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` outside the conversation.
3. If disconnected, call `youtube_auth_start`, give the returned URL to the user, and wait for them to finish Google consent. Then call `youtube_auth_status` again.
4. Never request that the user paste a secret into the conversation when a local OAuth JSON file is available. Never expose stored credentials or tokens in the response.

## Disconnect

1. Call `youtube_prepare_disconnect` and show its exact effects and preserved local data.
2. Ask for explicit confirmation. A request to inspect connection status or explain disconnection is not confirmation.
3. Only after approval, call `youtube_commit_disconnect` with the returned confirmation token.
4. If revocation fails, explain that the local token was deliberately retained so the user can retry. Never delete OAuth client configuration, channel settings, or drafts as part of disconnect.

## Resolve the video and preferences

1. Accept a video ID or extract it from a YouTube URL. If neither is supplied, call `youtube_list_videos` and let the user choose; when they clearly ask for the latest video, use the first result.
2. Call `youtube_get_video` and `youtube_get_channel_settings`.
3. Call `youtube_get_draft` before generating. Offer to restore a non-stale draft; clearly label a stale draft if the source metadata changed.
4. Use target languages from the request, otherwise use the saved channel defaults. If neither exists, ask which languages to use.
5. Use the video's `defaultLanguage` as source language. If missing, use the saved channel source language. Ask when both are missing or `auto` and the language cannot be identified reliably.

## Translate

For every target language:

- Write natural creator-facing copy, not a word-for-word rendering.
- Follow the saved channel instructions and the user's current instructions; current instructions win on conflict.
- Preserve URLs, timestamps, chapter lines, handles, product names, coupon codes, and intentional formatting.
- Localize hashtags only when the localized form is natural and useful.
- Do not invent claims, names, dates, links, or calls to action absent from the source.
- Keep titles at most 100 Unicode characters and descriptions at most 5,000 UTF-8 bytes. Do not use `<` or `>` in either field.
- Preserve the source language localization unless the user explicitly selects that language as a target.

After generation, call `youtube_save_draft`. Save every generated language and mark only the languages the user currently wants to publish as selected.

## Review and revise

Show a compact review grouped by language with title, description, title character count, and description character/byte counts. Identify unchanged existing localizations and proposed overwrites. Let the user edit or regenerate individual languages without regenerating approved ones, then update the draft.

Do not save to YouTube during generation or revision.

## Preview and save

1. Call `youtube_prepare_update` with only the selected, reviewed translations and the resolved source language.
2. Show the exact returned diff, including overwritten values, preserved localization count, warnings, and any default-language change.
3. Ask for explicit confirmation to save. A request to translate, draft, review, or prepare is not confirmation.
4. Call `youtube_commit_update` with the returned confirmation token only after the user explicitly approves that displayed diff in the current conversation.
5. If commit reports that the video changed after preview, fetch it again, prepare a new diff, and ask for confirmation again.

Never reuse a confirmation token for a different diff or infer approval from earlier messages.

## Channel settings

Use `youtube_save_channel_settings` when the user asks to remember source language, default target languages, custom translation instructions, or interface language for a channel. Keep settings per channel. Match the conversation language for normal responses; Ukrainian and English are both supported.

## Privacy

Explain when relevant: credentials, channel settings, drafts, and pending approval state remain in the plugin's local data directory. Video metadata is sent to Google through the YouTube Data API and appears in the Codex conversation so Codex can translate it. The plugin has no separate backend or analytics.
