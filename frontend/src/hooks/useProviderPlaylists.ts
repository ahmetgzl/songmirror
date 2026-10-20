import { t } from '@/i18n'
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, errorMessage } from '../api'
import { readPersistedResource, writePersistedResource } from '../lib/persistedResource'
import type { ProviderPlaylist } from '../types'

export interface ProviderPlaylistsEntry {
  playlists: ProviderPlaylist[]
  /** No successful snapshot yet; a background refresh keeps this false. */
  loading: boolean
  refreshing: boolean
  error: string | null
}

const playlistMemory = new Map<string, ProviderPlaylist[]>()
const playlistRequests = new Map<string, Promise<ProviderPlaylist[]>>()

function playlistResource(providerId: string): string {
  return `playlists:${providerId}`
}

function isProviderPlaylistArray(value: unknown): value is ProviderPlaylist[] {
  return (
    Array.isArray(value) &&
    value.every(
      (playlist) =>
        playlist !== null &&
        typeof playlist === 'object' &&
        'id' in playlist &&
        typeof playlist.id === 'string' &&
        'name' in playlist &&
        typeof playlist.name === 'string' &&
        'count' in playlist &&
        (playlist.count === null || typeof playlist.count === 'number') &&
        'image' in playlist &&
        typeof playlist.image === 'string' &&
        (!('external_url' in playlist) || typeof playlist.external_url === 'string'),
    )
  )
}

function cachedPlaylists(providerId: string): ProviderPlaylist[] | undefined {
  const inMemory = playlistMemory.get(providerId)
  if (inMemory) return inMemory

  const persisted = readPersistedResource(playlistResource(providerId), isProviderPlaylistArray)
  if (persisted) playlistMemory.set(providerId, persisted)
  return persisted
}

/** One provider request is shared by every mounted consumer. This matters when
 * the Playlists page and a picker remount close together, and in React's
 * development StrictMode where effects intentionally run twice. */
function fetchProviderPlaylists(providerId: string): Promise<ProviderPlaylist[]> {
  const existing = playlistRequests.get(providerId)
  if (existing) return existing

  const request = api
    .getPlaylists(providerId)
    .then((playlists) => {
      if (!isProviderPlaylistArray(playlists)) {
        throw new Error(t("The server returned invalid {{providerId}} playlist data.", { providerId: providerId }))
      }
      playlistMemory.set(providerId, playlists)
      writePersistedResource(playlistResource(providerId), playlists)
      return playlists
    })
    .finally(() => playlistRequests.delete(providerId))

  playlistRequests.set(providerId, request)
  return request
}

function entriesFromCache(providerIds: string[]): Record<string, ProviderPlaylistsEntry> {
  return Object.fromEntries(
    providerIds.map((providerId) => {
      const cached = cachedPlaylists(providerId)
      return [
        providerId,
        {
          playlists: cached ?? [],
          loading: cached === undefined,
          refreshing: true,
          error: null,
        },
      ]
    }),
  )
}

/** Fetches each account independently, sharing requests and painting its last
 * successful browser snapshot immediately. Empty snapshots are valid, refresh
 * failures preserve saved rows, and targeted refreshes leave other accounts
 * alone. Callers must separate initial loading from background refreshing. */
export function useProviderPlaylists(providerIds: string[]) {
  const idsKey = providerIds.slice().sort().join(',')
  const [entries, setEntries] = useState<Record<string, ProviderPlaylistsEntry>>(() =>
    entriesFromCache(idsKey ? idsKey.split(',') : []),
  )
  const generation = useRef(0)

  const refreshIds = useCallback(async (requested: string[]) => {
    const ids = idsKey ? idsKey.split(',') : []
    const thisGeneration = generation.current

    setEntries((previous) =>
      Object.fromEntries(
        ids.map((providerId) => {
          if (!requested.includes(providerId) && previous[providerId]) return [providerId, previous[providerId]]
          const cached = cachedPlaylists(providerId)
          return [providerId, { playlists: cached ?? previous[providerId]?.playlists ?? [],
            loading: cached === undefined, refreshing: true, error: null }]
        }),
      ),
    )

    await Promise.all(
      requested.map(async (providerId) => {
        try {
          const playlists = await fetchProviderPlaylists(providerId)
          if (generation.current !== thisGeneration) return
          setEntries((previous) => ({
            ...previous,
            [providerId]: { playlists, loading: false, refreshing: false, error: null },
          }))
        } catch (err) {
          if (generation.current !== thisGeneration) return
          setEntries((previous) => ({
            ...previous,
            [providerId]: {
              playlists: previous[providerId]?.playlists ?? cachedPlaylists(providerId) ?? [],
              loading: false,
              refreshing: false,
              error: errorMessage(err),
            },
          }))
        }
      }),
    )
  }, [idsKey])

  const refresh = useCallback(() => refreshIds(idsKey ? idsKey.split(',') : []), [refreshIds, idsKey])
  const refreshProvider = useCallback((providerId: string) => {
    const ids = idsKey ? idsKey.split(',') : []
    return refreshIds(ids.includes(providerId) ? [providerId] : [])
  }, [refreshIds, idsKey])

  useEffect(() => {
    generation.current += 1
    void refresh()
    return () => {
      generation.current += 1
    }
  }, [refresh])

  return { entries, refresh, refreshProvider }
}
