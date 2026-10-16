import { t, useTranslation } from '@/i18n'
import { FeatureGuide } from '@/components/onboarding/FeatureGuide'
import { useMemo, useState } from 'react'
import { LuPlus } from 'react-icons/lu'
import { Link, useNavigate } from 'react-router-dom'

import { LinkCard } from '@/components/playlists/LinkCard'
import { LinkEditorModal } from '@/components/playlists/LinkEditorModal'
import { PlaylistDetailModal } from '@/components/playlists/PlaylistDetailModal'
import { ProviderPlaylistsCard } from '@/components/playlists/ProviderPlaylistsCard'
import type { PlaylistSort } from '@/components/playlists/ProviderPlaylistsCard'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingStatus, Skeleton } from '@/components/ui/Skeleton'
import { SelectField } from '@/components/ui/SelectField'
import { BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES, BUTTON_VARIANT_CLASSES } from '@/components/ui/buttonStyles'
import { useAccounts } from '@/hooks/useAccounts'
import { useLinks } from '@/hooks/useLinks'
import { useProviderPlaylists } from '@/hooks/useProviderPlaylists'
import { canSyncAccount, capabilitiesOf } from '@/lib/accountCapabilities'
import { cn } from '@/lib/cn'
import type { Account, PlaylistLink, ProviderPlaylist } from '@/types'

const SORT_STORAGE_KEY = 'songmirror-playlist-sort'

function savedSort(): PlaylistSort {
  try {
    const value = window.localStorage.getItem(SORT_STORAGE_KEY)
    if (value === 'name-asc' || value === 'name-desc') return value
  } catch {
    // Sorting still works for this visit when storage is unavailable.
  }
  return 'service'
}

export default function Playlists() {
  useTranslation()
  const navigate = useNavigate()
  const { accounts, loading: accountsLoading, error: accountsError } = useAccounts()
  const connectedAccounts = useMemo(
    () => accounts?.filter((account) => account.state === 'connected' && capabilitiesOf(account).library_read) ?? [],
    [accounts],
  )
  const syncAccounts = useMemo(() => accounts?.filter(canSyncAccount) ?? [], [accounts])
  const connectedIds = useMemo(() => connectedAccounts.map((a) => a.id), [connectedAccounts])
  const { entries, refresh: refreshPlaylists } = useProviderPlaylists(connectedIds)
  const { links, loading: linksLoading, error: linksError, refresh: refreshLinks } = useLinks()

  const [editorTarget, setEditorTarget] = useState<PlaylistLink | 'new' | null>(null)
  const [playlistSort, setPlaylistSort] = useState<PlaylistSort>(savedSort)
  const [viewerTarget, setViewerTarget] = useState<{
    account: Account
    playlist: ProviderPlaylist
  } | null>(null)

  function changeSort(value: PlaylistSort) {
    setPlaylistSort(value)
    try {
      window.localStorage.setItem(SORT_STORAGE_KEY, value)
    } catch {
      // Keep the selected order even if the device cannot save it.
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text sm:text-[22px]">{t("Playlists")}</h1>
          <p className="mt-1 max-w-2xl text-sm text-text-3">{t("Browse what's on each connected service, and pair up playlists that don't share a name.")}</p>
        </div>
        <Button icon={<LuPlus className="size-4" aria-hidden="true" />} onClick={() => navigate('/playlists/create')}>
          {t('Create Playlist')}
        </Button>
      </div>

      <FeatureGuide page="playlists" />

      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 className="text-[17px] font-bold text-text">{t("Browse")}</h2>
          {connectedAccounts.length > 0 && (
            <div className="w-full sm:w-56">
              <SelectField
                label={t('Sort playlists')}
                value={playlistSort}
                onChange={(event) => changeSort(event.target.value as PlaylistSort)}
                options={[
                  { value: 'service', label: t('Service order') },
                  { value: 'name-asc', label: t('Name (A–Z)') },
                  { value: 'name-desc', label: t('Name (Z–A)') },
                ]}
              />
            </div>
          )}
        </div>

        {accountsError && (
          <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{t("Could not load accounts: {{accountsError}}", { accountsError: accountsError })}</p>
        )}

        {accountsLoading && !accounts ? (
          <LoadingStatus label={t("Loading accounts…")}>
            <div className="flex flex-col gap-4">
              {[0, 1].map((i) => (
                <Skeleton key={i} className="h-40 w-full rounded-card" />
              ))}
            </div>
          </LoadingStatus>
        ) : connectedAccounts.length > 0 ? (
          <div className="flex flex-col gap-4">
            {connectedAccounts.map((account) => (
              <ProviderPlaylistsCard
                key={account.id}
                account={account}
                entry={entries[account.id]}
                sort={playlistSort}
                onOpenPlaylist={(playlist) => setViewerTarget({ account, playlist })}
                onRetry={() => void refreshPlaylists()}
              />
            ))}
          </div>
        ) : !accountsError ? (
          <div data-tour="playlist-empty"><EmptyState
            title={t('No connected playlist accounts')}
            description={t('Connect an account with library access on the Accounts page to browse playlists.')}
            action={<Link to="/accounts" className={cn(BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES.sm, BUTTON_VARIANT_CLASSES.primary)}>{t('Accounts')}</Link>}
          /></div>
        ) : null}
      </section>

      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-baseline gap-2.5">
            <h2 className="text-[17px] font-bold text-text">{t("Pairings")}</h2>
            {links && (
              <span className="font-mono text-[11.5px] text-text-3">
                {t("{{count, number}} link · {{linksFilter, number}} active", { count: links.length, linksFilter: links.filter((l) => l.enabled).length, defaultValue_one: "{{count, number}} link · {{linksFilter, number}} active", defaultValue_other: "{{count, number}} links · {{linksFilter, number}} active" })}
              </span>
            )}
          </div>
          <Button
            data-tour="playlist-pairings"
            variant="secondary"
            onClick={() => setEditorTarget('new')}
            disabled={syncAccounts.length < 2}
            title={syncAccounts.length < 2 ? t("Connect at least 2 full-library services first") : undefined}
          >
            {t("+ New pairing")}
          </Button>
        </div>
        <p className="text-sm text-text-3">
          {t("Pairings tell a sync which playlists belong together. Matching names need no pairing; create or run a sync on the Sync page to copy changes.")}
        </p>

        {linksError && (
          <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{t("Could not load pairings: {{linksError}}", { linksError: linksError })}</p>
        )}

        {linksLoading && !links ? (
          <LoadingStatus label={t("Loading pairings…")}>
            <div className="flex flex-col gap-3">
              {[0, 1].map((i) => (
                <Skeleton key={i} className="h-24 w-full rounded-card" />
              ))}
            </div>
          </LoadingStatus>
        ) : links && links.length > 0 ? (
          <div className="flex flex-col gap-3">
            {links.map((link) => (
              <LinkCard
                key={link.id}
                link={link}
                accounts={accounts ?? []}
                playlistEntries={entries}
                onEdit={() => setEditorTarget(link)}
                onChanged={() => void refreshLinks()}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            title={t("No pairings yet")}
            description={t("Pair differently named playlists for a sync. A pairing alone does not start copying.")}
          />
        )}
      </section>

      <LinkEditorModal
        open={editorTarget !== null}
        onClose={() => setEditorTarget(null)}
        link={editorTarget === 'new' ? null : editorTarget}
        accounts={accounts ?? []}
        playlistEntries={entries}
        onSaved={() => {
          setEditorTarget(null)
          void refreshLinks()
        }}
      />
      <PlaylistDetailModal
        account={viewerTarget?.account ?? null}
        playlist={viewerTarget?.playlist ?? null}
        onClose={() => setViewerTarget(null)}
        onChanged={() => void refreshPlaylists()}
      />
    </div>
  )
}
