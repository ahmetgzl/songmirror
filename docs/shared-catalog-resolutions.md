# Shared catalog resolutions

SongMirror keeps each account's resolve cache in its existing location, but can
read successful matches from other profiles of the same provider. Matching a
track manually on one Deezer account therefore makes that catalog ID available
to another Deezer account during sync, transfer, and import matching. A cached
local miss can also be filled by a successful sibling match.

## Selection rules

- A positive local mapping always wins, including an automatic local mapping.
- Otherwise, sibling manual choices take precedence over sibling automatic
  matches. All valid choices in the winning group must agree on one catalog ID;
  conflicting choices are ignored and normal local resolution continues.
- Sibling misses are never inherited. A local miss stays cached when there is
  no usable sibling match.
- Positive ISRC candidate lists can fill missing or empty local lists. Candidates
  are combined by catalog ID, retain their recording metadata, and still pass
  the destination's existing recording compatibility checks. Malformed candidates
  are ignored.
- Matching-version upgrades discard old automatic matches and ISRC results,
  while preserving manually chosen mappings, for local and sibling caches alike.

Only profiles of the same provider participate. Providers whose IDs belong to a
particular server, including Navidrome/Subsonic, do not share caches between
profiles. Global catalog references are validated and normalized without network
requests before reuse; account-local Apple library IDs are excluded. Availability
in a destination account or region remains subject to the provider's checks.

## Local edits and persistence

The Mappings page shows the effective matches, including inherited choices before
the selected account has created its own cache file. Saving a manual choice makes
it an explicit local mapping. Forgetting a mapping adds a per-account exclusion
for that search key, so a sibling choice cannot immediately restore it. Other
accounts and their files are unaffected; ordinary local matching can still resolve
the forgotten track on a later pass.

Inherited entries that have not been edited are not copied into the borrowing
account's file, even when other results are saved. A sibling correction or
deletion is consequently visible the next time the cache is loaded. Existing
atomic writes and concurrent edit protection still apply to local changes.

Default profiles, custom profile directories, and explicit cache-file overrides
keep their current paths; no migration is required. Reading a sibling cache does
not construct its provider client or reuse its credentials. Authentication,
tokens, playlists, and pending transfer selections remain scoped to their account
or job. Missing or corrupt sibling files are skipped; a corrupt primary file
continues to fail visibly rather than being overwritten.
