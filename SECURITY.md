# Security policy

## Reporting a vulnerability

Do not open a public issue containing OAuth credentials, tokens, private video metadata, or a working exploit. Use GitHub's private vulnerability reporting for this repository when available.

Include the affected version, operating system, Codex surface, reproduction steps, and the expected security boundary. Remove all real credentials and private channel data from logs and examples.

## Security boundaries

Codex YMT intentionally exposes no MCP tool for:

- deleting a YouTube video
- uploading or replacing video content
- changing video visibility
- editing comments, playlists, memberships, or monetization
- bulk-updating multiple videos in one tool call

The only remote write tool commits a previously prepared localization diff for one video.

Every YouTube write is protected by both a metadata fingerprint and the video resource ETag. The final `videos.update` request sends `If-Match`, allowing YouTube to reject a concurrent change that happens after the plugin's last read.

Google disconnection is also two-phase. The preview token is short-lived and bound to the specific stored Google token, so a changed connection cannot be revoked using an older confirmation.

The repository must never contain real OAuth client JSON files, Google tokens, private channel metadata, or creator drafts. If any such data is committed, revoke the affected credentials before attempting repository cleanup because removing a secret from the latest revision does not make it safe again.

## Known limitations

- Google provides a broad [`youtube.force-ssl` OAuth scope](https://developers.google.com/youtube/v3/guides/auth/installed-apps) for this workflow. The plugin's tool surface is narrower than the credential's underlying scope.
- OAuth credentials and tokens use owner-only local files but are not encrypted with the operating system keychain.
- Explicit human confirmation is an agent-workflow rule. The MCP server enforces a separate preview token and state check, but it cannot cryptographically prove who approved the commit.
- The plugin does not yet provide automatic rollback after a successful YouTube update.
- OAuth client configuration, settings, and drafts remain after the disconnect tool revokes and deletes the Google token. Remove the data directory separately if complete local deletion is required.

For high-value channels, test first with an unlisted video, review every `overwrite` entry, and [revoke Google access](https://myaccount.google.com/connections) immediately if local credentials may have been exposed.
