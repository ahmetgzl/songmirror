# Playlist picker caching

After a library loads successfully, its playlist metadata is saved in browser memory and local storage under that account's ID. Returning to Transfers, switching services, or reloading the page can show the saved library immediately while a fresh request runs in the background. A first visit still needs a provider response.

The source and existing-destination pickers show when saved playlists are being updated. **Refresh** reloads only that selected account. Concurrent reads for the same account share a request. A failed update leaves the last successful list available and displays the error so it can be retried; a successful empty response replaces the old list. Accounts on the same service keep separate libraries.

Background updates preserve a custom destination name and description. Selecting another source or switching back to Create new restores the defaults for that selection. If a successful refresh removes the selected source or makes the destination read-only, copying stays disabled until a valid playlist is selected.

This cache contains playlist metadata, not credentials or songs. Clearing browser storage removes it. If storage is unavailable, memory caching still works during the current page session. Invalid or old-schema snapshots are ignored and fetched again. The transfer worker always reads the live source and destination at copy time.
