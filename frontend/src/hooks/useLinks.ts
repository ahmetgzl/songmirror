import { api } from '../api'
import { usePersistedResource } from '../lib/persistedResource'
import type { PlaylistLink } from '../types'

function isPlaylistLinkArray(value: unknown): value is PlaylistLink[] {
  return (
    Array.isArray(value) &&
    value.every(
      (link) =>
        link !== null &&
        typeof link === 'object' &&
        'id' in link &&
        typeof link.id === 'string' &&
        'name' in link &&
        typeof link.name === 'string' &&
        'members' in link &&
        link.members !== null &&
        typeof link.members === 'object',
    )
  )
}

/** GET /api/links — the saved cross-service playlist pairings. */
export function useLinks() {
  const { data: links, loading, refreshing, error, refresh } = usePersistedResource(
    'links',
    api.getLinks,
    isPlaylistLinkArray,
  )

  return { links, loading, refreshing, error, refresh }
}
