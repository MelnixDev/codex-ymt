# Google OAuth setup

Read this reference only when connecting a YouTube account or diagnosing authentication.

## One-time Google Cloud setup

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or create a project.
2. Enable [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) for the project.
3. Configure the [Google Auth Platform](https://console.cloud.google.com/auth/overview). Add the creator's Google account as a test user while the app remains in testing.
4. Create an OAuth client from the [Clients page](https://console.cloud.google.com/auth/clients) with application type **Desktop app**.
5. Download the OAuth client JSON. Give Codex only its local file path; call `youtube_configure_oauth` with `client_json_path` so the secret is read locally instead of pasted into the conversation.
6. Call `youtube_auth_start`, open its Google authorization URL, approve access, then call `youtube_auth_status`.

`youtube_configure_oauth` accepts only the local JSON path. For a headless environment, configure `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` in the MCP process environment rather than passing secrets through the Codex task.

Only the Google **Desktop app** OAuth client format is accepted. Web application clients are rejected. If a different Desktop client is configured, Codex YMT removes the incompatible local token and requires authorization again; configuring the same client keeps its token.

The plugin requests `https://www.googleapis.com/auth/youtube.force-ssl`. It uses a temporary loopback callback on `127.0.0.1`, stores the resulting refresh token locally, and never sends it to a separate plugin backend.

Google defines this scope broadly enough to view, edit, and permanently delete YouTube videos and other channel data. Codex YMT deliberately exposes no deletion tool, but the credential still carries the underlying Google scope. Keep the OAuth JSON and stored token private, and [revoke access](https://myaccount.google.com/connections) if either may have been exposed.

Official references: [YouTube OAuth for installed apps](https://developers.google.com/youtube/v3/guides/auth/installed-apps), [YouTube Data API overview](https://developers.google.com/youtube/v3/getting-started), and [OAuth app verification](https://support.google.com/cloud/answer/13461325?hl=en).

## Disconnect

Use `youtube_prepare_disconnect`, show its effects, and ask for explicit approval before calling `youtube_commit_disconnect`. A successful commit revokes the refresh token (or access token when no refresh token exists) through Google's [revocation endpoint](https://developers.google.com/identity/openid-connect/reference#revocation_endpoint), then deletes only the local token file.

If Google reports `invalid_token`, treat the connection as already revoked and remove the local token. For network or other Google errors, keep the token so the user can retry. OAuth client configuration, channel settings, and drafts are never removed by the disconnect tools.

## Common failures

- `access_denied`: confirm the Google account is an allowed test user and retry.
- `redirect_uri_mismatch`: confirm the OAuth client type is **Desktop app**, not Web application.
- `accessNotConfigured`: enable YouTube Data API v3 in the same Google Cloud project as the OAuth client.
- `insufficientPermissions`: reconnect so Google grants the requested YouTube scope.
- No refresh token: revoke the app's access in the Google account and reconnect; the authorization flow requests offline access and consent.

YouTube API quota and Google account policies still apply. Saving localizations requires a channel account that can edit the selected video.
