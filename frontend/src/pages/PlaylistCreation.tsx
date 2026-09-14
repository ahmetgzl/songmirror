import { t, useTranslation } from '@/i18n'
import { useEffect, useState } from 'react'
import { LuArrowLeft } from 'react-icons/lu'
import { Link, NavLink, useMatch } from 'react-router-dom'

import { cn } from '@/lib/cn'

import CreatePlaylist from './CreatePlaylist'
import Imports from './Imports'

/** Creation and its history belong to the playlist library. Keep an opened
 * editor mounted when viewing history so switching tabs preserves the draft. */
export default function PlaylistCreation() {
  useTranslation()
  const showHistory = Boolean(useMatch('/playlists/create/history'))
  const [editorOpened, setEditorOpened] = useState(!showHistory)

  useEffect(() => {
    if (!showHistory) setEditorOpened(true)
  }, [showHistory])

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link to="/playlists" className="mb-3 inline-flex min-h-8 items-center gap-1.5 text-sm text-text-3 hover:text-text">
          <LuArrowLeft className="size-4 rtl:-scale-x-100" aria-hidden="true" />
          {t('Playlists')}
        </Link>
        <h1 className="text-xl font-bold tracking-tight text-text sm:text-[22px]">{t('Create Playlist')}</h1>
        <p className="mt-1 text-sm text-text-3">{t('Build a playlist from text, a file, or a playlist link')}</p>
      </div>

      <nav aria-label={t('Playlist creation')} className="flex gap-5 border-b border-border">
        {[
          { to: '/playlists/create', label: t('New playlist') },
          { to: '/playlists/create/history', label: t('Import history') },
        ].map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end
            className={({ isActive }) => cn(
              '-mb-px inline-flex min-h-11 items-center border-b-2 px-1 text-sm font-medium transition-colors',
              isActive ? 'border-accent text-accent' : 'border-transparent text-text-3 hover:border-border-strong hover:text-text',
            )}
          >
            {label}
          </NavLink>
        ))}
      </nav>

      {(editorOpened || !showHistory) && (
        <section hidden={showHistory} aria-label={t('New playlist')}>
          <CreatePlaylist />
        </section>
      )}
      {showHistory && <Imports />}
    </div>
  )
}
