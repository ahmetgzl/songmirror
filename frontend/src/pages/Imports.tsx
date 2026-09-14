import { t, useTranslation } from '@/i18n'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { LuHistory, LuRefreshCw } from 'react-icons/lu'
import useSWR from 'swr'

import { errorMessage, importApi } from '@/api'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { Pill } from '@/components/ui/Pill'
import { ServiceLogo } from '@/components/ui/ServiceLogo'
import { LoadingStatus, Skeleton } from '@/components/ui/Skeleton'
import { serviceLogoId, tagText } from '@/lib/constants'
import { formatDateTime, formatTrackCount } from '@/lib/format'
import type { ImportJob, ImportStatus } from '@/types'

function statusTone(status: ImportStatus): string {
  switch (status) {
    case 'done':
      return 'bg-success-soft text-success'
    case 'failed':
      return 'bg-danger-soft text-danger'
    case 'cancelled':
      return 'bg-neutral-soft text-neutral'
    case 'matching':
    case 'creating':
    case 'parsing':
      return 'bg-accent-soft text-accent'
    case 'paused':
      return 'bg-neutral-soft text-neutral'
    case 'ready':
      return 'bg-warning-soft text-warning'
    default:
      return 'bg-neutral-soft text-neutral'
  }
}

function statusLabel(status: ImportStatus): string {
  switch (status) {
    case 'parsing':
      return t('parsing')
    case 'matching':
      return t('matching')
    case 'ready':
      return t('ready')
    case 'creating':
      return t('creating')
    case 'done':
      return t('done')
    case 'failed':
      return t('failed')
    case 'cancelled':
      return t('cancelled')
    case 'paused':
      return t('paused')
    default:
      return status
  }
}

function sourceKindLabel(kind: ImportJob['source_kind']): string {
  if (kind === 'text') return t('Text')
  if (kind === 'file') return t('File')
  return t('URL')
}

export default function Imports() {
  useTranslation()
  const navigate = useNavigate()
  const { data: imports, error: loadError, isLoading: loading, isValidating, mutate } = useSWR(
    '/api/imports',
    importApi.listImports,
    {
      revalidateOnMount: true,
      shouldRetryOnError: false,
      refreshInterval: (jobs) => jobs?.some((job) => ['parsing', 'matching', 'creating'].includes(job.status)) ? 3000 : 0,
    },
  )
  const [actionError, setError] = useState('')
  const error = actionError || (loadError ? errorMessage(loadError) : '')
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [resumingId, setResumingId] = useState<string | null>(null)

  async function loadImports() {
    setError('')
    try {
      await mutate()
    } catch {
      // SWR exposes the refresh failure while retaining any previous rows.
    }
  }

  async function handleDelete() {
    if (!pendingDeleteId) return
    setDeleting(true)
    setError('')
    try {
      await importApi.deleteImport(pendingDeleteId)
      setPendingDeleteId(null)
      await loadImports()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setDeleting(false)
    }
  }

  async function handleResume(id: string) {
    setResumingId(id)
    setError('')
    try {
      await importApi.resumeImport(id)
      navigate(`/playlists/create?resume=${encodeURIComponent(id)}`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setResumingId(null)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-[17px] font-bold text-text">{t('Import history')}</h2>
          <p className="mt-1 text-sm text-text-3">{t('Your playlist import history')}</p>
        </div>
        <Button variant="secondary" size="sm" icon={<LuRefreshCw className="size-4" aria-hidden="true" />} loading={isValidating} onClick={() => void loadImports()}>
          {t('Refresh')}
        </Button>
      </div>

      {error && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">
          <p>{error}</p>
          {loadError && <Button variant="secondary" size="sm" onClick={() => void loadImports()} loading={isValidating}>{t('Retry')}</Button>}
        </div>
      )}

      {loading && !imports ? (
        <LoadingStatus label={t('Loading import history…')}>
          <p className="mb-3 text-sm text-text-3" aria-hidden="true">{t('Loading import history…')}</p>
          <div className="space-y-3">
            {[0, 1, 2].map((row) => <Skeleton key={row} className="h-24 w-full rounded-card" />)}
          </div>
        </LoadingStatus>
      ) : imports?.length === 0 ? (
        <EmptyState
          title={t('No imports yet')}
          description={t('Create your first playlist import')}
          action={
            <Button className="mt-2" onClick={() => navigate('/playlists/create')}>{t('Create Playlist')}</Button>
          }
        />
      ) : (
        <div className="space-y-3">
          {imports?.map((job) => {
            const logoId = serviceLogoId(job.source_provider || job.destination_account)
            const canResume = job.status === 'ready' || job.status === 'paused' || job.status === 'failed'
            const canDelete = !['creating', 'matching'].includes(job.status)
            return (
              <Card key={job.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
                <div className="flex min-w-0 items-center gap-4">
                  <div className="grid size-10 shrink-0 place-items-center rounded-control bg-surface-2">
                    {logoId ? (
                      <ServiceLogo service={logoId} className={`size-5 ${tagText(logoId)}`} />
                    ) : (
                      <LuHistory className="size-5 text-text-3" aria-hidden="true" />
                    )}
                  </div>
                  <div className="min-w-0">
                    <Link to={`/playlists/create?resume=${encodeURIComponent(job.id)}`} className="block truncate font-medium text-text hover:text-accent">{job.destination_name}</Link>
                    <div className="mt-0.5 text-sm text-text-3">
                      {sourceKindLabel(job.source_kind)} • {formatTrackCount(job.total_tracks)} •{' '}
                      {formatDateTime(job.created_at)}
                    </div>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                  <Pill toneClasses={statusTone(job.status)} label={statusLabel(job.status)} />
                  {canResume && (
                    <Button size="sm" onClick={() => void handleResume(job.id)} loading={resumingId === job.id}>
                      {t('Resume')}
                    </Button>
                  )}
                  {canDelete && (
                    <Button size="sm" variant="danger-ghost" onClick={() => setPendingDeleteId(job.id)}>
                      {t('Delete')}
                    </Button>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(pendingDeleteId)}
        title={t('Delete')}
        description={t('Delete this import?')}
        confirmLabel={t('Delete')}
        danger
        loading={deleting}
        onConfirm={() => void handleDelete()}
        onCancel={() => {
          if (!deleting) setPendingDeleteId(null)
        }}
      />
    </div>
  )
}
