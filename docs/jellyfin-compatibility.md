# Jellyfin connections and missing playlists

SongMirror supports Jellyfin 10.x and 12.x using the same `Authorization: MediaBrowser` header. Connection checks, playlist discovery, track counts, and cover uploads all use this format. Existing server URLs, API keys, and optional user IDs continue to work; no version setting or replacement key is required for the upgrade.

Older SongMirror versions used `X-Emby-Token`. Jellyfin 12 disables legacy authorization by default, so those requests can receive HTTP 401 even when the key is valid. The old playlist browser also converted request failures into an empty list, which made this look like missing playlists. Update SongMirror, then refresh **Playlists**. You do not need to enable legacy authentication in Jellyfin.

If loading still fails, the playlist card now displays the error and offers **Retry**. Check the following settings in **Accounts → Jellyfin**:

1. The server URL must be reachable from SongMirror. With Docker, `localhost` refers to the SongMirror container; use the Jellyfin container name on a shared network, or the appropriate host address. Reverse-proxy base paths such as `/jellyfin` are retained, and pasted `/web/` or `/web/index.html` URLs are accepted.
2. The API key must still exist in Jellyfin's **Dashboard → API Keys**. A revoked key needs replacement; upgrading by itself does not require a new key.
3. If you entered a **User ID**, confirm it still identifies the intended Jellyfin user. It scopes playlist discovery and track counts to that user's access. Leave it empty for server-wide API-key browsing.

Playlist discovery reads all result pages. A failed track-count request leaves that playlist visible with an unknown count, and a failed refresh preserves the previously saved list with an error notice. A successful response containing no playlists still shows the normal empty-library state. Jellyfin remains a browse-only service in SongMirror; the local download mirror supplies its audio files and playlist covers.

Compatibility is covered by protocol-level regression tests for the 10.8/10.11 and 12.0/12.1 authentication behavior, including legacy authentication disabled, plus the actual SongMirror connection and playlist API routes. These tests use mocked Jellyfin responses rather than live server installations.

Upstream references: [Jellyfin 12 authorization handling](https://github.com/jellyfin/jellyfin/blob/v12.0/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs), [Jellyfin 10.11 authorization handling](https://github.com/jellyfin/jellyfin/blob/v10.11.11/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs), and [the upgrade-related API-key report](https://github.com/jellyfin/jellyfin/issues/17245).
