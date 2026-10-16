import { t } from '@/i18n'
import type { ProviderPlaylist } from '@/types'

export function playlistLibraryRank(playlist: ProviderPlaylist): number {
  return playlist.library_kind === 'video' ? 1 : 0
}

export function playlistLibraryLabel(kind: ProviderPlaylist['library_kind']): string | undefined {
  if (kind === 'music') return t('YouTube Music playlists')
  if (kind === 'video') return t('YouTube videos')
}
