# Optional live smoke-test checklist

Use this checklist to validate a release candidate against a real YouTube channel. It is optional and must never run from CI. Do not commit OAuth files, tokens, video metadata, drafts, terminal logs, or screenshots containing private channel data.

## Safe preparation

1. Create a dedicated **unlisted** test video with a recognizable title and description.
2. Record its current default language and every existing localization in YouTube Studio so they can be compared after testing.
3. Use a Google Cloud Desktop OAuth client intended for testing. If the OAuth consent screen is external and remains in **Testing**, expect its refresh token to expire after seven days.
4. Install the exact release candidate and confirm the plugin version before connecting YouTube.

## Read-only and local checks

These steps do not authorize a YouTube write:

1. Connect the test account and confirm that `youtube_auth_status` reports a connection without exposing credentials.
2. List recent videos and fetch the unlisted test video by ID.
3. Save and restore a local draft, then confirm that YouTube Studio is unchanged.
4. Prepare one new localization and verify the exact diff, character counts, UTF-8 byte count, preserved-localization count, default-language change, and confirmation expiry.
5. Let one preview expire and confirm that its token cannot be committed.

Stop here unless the user separately and explicitly approves the displayed live-write diff.

## Optional live write — explicit approval required

1. After explicit approval, commit only one reviewed language to the unlisted test video. A `videos.update` request costs 50 YouTube API quota units.
2. Confirm in YouTube Studio that the approved localization was saved and every unselected localization was preserved.
3. Prepare another preview, change the video's metadata directly in YouTube Studio, and confirm that the stale preview is rejected without a write.
4. If the result is wrong, correct it in YouTube Studio. Do not attempt an automated rollback; Codex YMT does not provide one.

## Cleanup

Disconnect Google only if the user explicitly asks to revoke access and approves the disconnect preview. Otherwise leave the connection unchanged. Remove test drafts or local data manually only after confirming their exact location and contents.

Never perform the live-write or disconnect steps automatically, from CI, or merely because this checklist was opened.
