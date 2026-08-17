import { useCallback } from 'react'
import useSWR from 'swr'

import { api, errorMessage } from '@/api'
import type { ProviderPlaylistDetail } from '@/types'

/** On-demand playlist contents. The server's SQLite-backed playlist cache makes
 * reopens instant; the visible Refresh action explicitly bypasses that cache. */
export function usePlaylistDetail(
  provider: string | null,
  playlistId: string | null,
  expectedCount?: number | null,
) {
  const key = provider && playlistId
    ? ['playlist-detail', provider, playlistId, expectedCount]
    : null
  const { data, error, isLoading, isValidating, mutate } = useSWR<ProviderPlaylistDetail>(
    key,
    () => api.getPlaylistDetail(provider as string, playlistId as string, { expectedCount }),
    {
      revalidateOnFocus: false,
      dedupingInterval: 2_000,
    },
  )

  const refresh = useCallback(async () => {
    await mutate(
      api.getPlaylistDetail(provider as string, playlistId as string, {
        refresh: true,
        expectedCount,
      }),
      { revalidate: false },
    )
  }, [expectedCount, mutate, playlistId, provider])

  return {
    detail: data ?? null,
    loading: isLoading,
    refreshing: isValidating,
    error: error ? errorMessage(error) : null,
    refresh,
  }
}
