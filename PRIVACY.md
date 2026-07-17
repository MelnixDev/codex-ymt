# Privacy

Codex YMT has no separate backend, user account, telemetry, advertising, or analytics.

The project maintainers do not receive your Google credentials, tokens, channel settings, drafts, or video metadata through the plugin. Normal GitHub activity, such as opening an issue, is separate from the plugin and is governed by GitHub's own policies. Never include private channel data or credentials in an issue.

## Data stored locally

The MCP server stores these items in the writable plugin data directory supplied by Codex:

- Google OAuth client ID and client secret
- Google access and refresh tokens
- Per-channel source language, target languages, and translation instructions
- Per-video localization drafts

If Codex does not supply a plugin data directory, the fallback is `~/.config/codex-ymt/`. Files are written atomically with owner-only permissions (`0600`) where the operating system supports them.

Tokens are not currently encrypted with the operating system keychain. Anyone who gains access to the local user account may be able to read them.

## Data sent to Google

The plugin sends OAuth requests and YouTube Data API requests directly to Google. These requests contain the information required to authenticate, read channel and video metadata, and publish localizations explicitly approved by the user.

The requested OAuth scope is:

```text
https://www.googleapis.com/auth/youtube.force-ssl
```

Google's account policies, API quota, and privacy terms apply.

## Data processed by Codex

Video titles, descriptions, existing localizations, channel instructions, and generated translations appear in the Codex task so Codex can perform the translation and review workflow. OpenAI's terms and privacy controls for the user's Codex account apply to that processing.

The Google OAuth client secret and refresh token should never be pasted into the conversation. Use the local OAuth JSON path workflow instead.

## Delete or revoke access

To disconnect completely:

1. [Revoke the application's access](https://myaccount.google.com/connections) from the Google Account security settings.
2. Disable or uninstall the Codex YMT plugin.
3. Delete the plugin's local data directory, or the fallback `~/.config/codex-ymt/` directory.

Deleting only the local files does not revoke an already issued Google token; revoke access at Google as well.
