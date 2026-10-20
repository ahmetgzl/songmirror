# ListenBrainz and Navidrome / Subsonic

## ListenBrainz loved recordings

Connect a public ListenBrainz profile from **Accounts** using its MusicBrainz username. SongMirror reads the profile's loved recordings without a user token. This connection supplies tracks to other services; it does not change ListenBrainz feedback or create playlists there.

Create a one-way sync and choose ListenBrainz as the source. This selects its **Loved Recordings** collection automatically. Include the destination accounts, then choose each destination's native favorites or a named playlist under **Playlists**. Preview the sync before enabling writes. Subsequent runs reconcile the same collection, so unloving a recording can remove its mirror when removal mirroring is enabled.

The reader requests every page of positive feedback with metadata. A MusicBrainz recording MBID identifies a recording when available; otherwise its ListenBrainz recording MSID is retained. Titles and artist credits supply the metadata matching fallback because the feedback response does not guarantee an ISRC or duration. Missing metadata remains in the source and produces a warning containing its recording identifier; unsuccessful matches appear in the sync's missing results. Destination removals are held while any source recording lacks its title or artist, so a metadata failure cannot delete a previously matched copy. A failed or incomplete page stops the source read instead of returning an empty collection.

For environment-managed connections, set `LISTENBRAINZ_USER`. Additional accounts created in the UI keep their usernames and cache paths in separate profiles.

## Navidrome and Subsonic-compatible servers

In **Accounts**, connect **Navidrome / Subsonic** with the server's base URL, username, and password. Include any reverse-proxy prefix, such as `https://music.example/navidrome`. A trailing `/rest` is also accepted. Docker must be able to reach that address; `localhost` inside the container refers to the container itself.

The connection supports browsing playlists, playlist sync and transfers in both directions, playlist names, descriptions and visibility, and **Starred Songs** as the native favorites collection. Only the owner can modify a playlist. SongMirror matches incoming tracks against files already present in the server's library; it does not download or upload missing audio. Unavailable recordings stay unresolved.

OpenSubsonic's optional ISRC and artist fields are retained when returned. Modern Navidrome supports the standard ISRC field, so SongMirror does not need its unofficial `/api/song` endpoint or a second authentication session. Servers without ISRCs use title, artist, duration and recording-version matching. Catalog searches can only find what the server indexes, including when an ISRC is present on the source.

Playlist additions are appended in source order. Removals address selected positions after a fresh read, so removing one repeated song keeps its other occurrences. Incomplete playlist reads and provider error envelopes stop the operation. A new playlist's requested visibility is set explicitly because server defaults can differ. Saved manual matches for this provider use the server's song IDs.

Environment-managed connections use `SUBSONIC_URL`, `SUBSONIC_USER`, and `SUBSONIC_PASSWORD`. Prefer HTTPS outside a trusted local network. The password is stored in the account's private settings, never returned to the browser, and used to generate a fresh salted authentication token for each request. Custom accounts keep their credentials and resolve caches separate.

## API references

- [ListenBrainz feedback API](https://listenbrainz.readthedocs.io/en/latest/users/api/recordings.html)
- [ListenBrainz feedback identifiers and pagination](https://listenbrainz.readthedocs.io/en/latest/users/feedback-json.html)
- [OpenSubsonic song fields, including ISRC](https://opensubsonic.netlify.app/docs/responses/child/)
- [OpenSubsonic playlist updates](https://opensubsonic.netlify.app/docs/endpoints/updateplaylist/)
- [Navidrome's OpenSubsonic implementation tracker](https://github.com/navidrome/navidrome/issues/2695)
