import { t, useTranslation } from '@/i18n'
import { FeatureGuide } from '@/components/onboarding/FeatureGuide'
import { useMemo, useState } from 'react'

import { api } from '@/api'
import { ConflictList } from '@/components/transfers/ConflictList'
import { TransferLiveFeed } from '@/components/transfers/TransferLiveFeed'
import { TransferProgress } from '@/components/transfers/TransferProgress'
import { TransferSetupForm } from '@/components/transfers/TransferSetupForm'
import { TransferHistory } from '@/components/transfers/TransferHistory'
import { Segmented } from '@/components/ui/Segmented'
import { Card } from '@/components/ui/Card'
import { LoadingStatus, Skeleton } from '@/components/ui/Skeleton'
import { useAccounts } from '@/hooks/useAccounts'
import { useEventStream } from '@/hooks/useEventStream'
import { useProviderPlaylists } from '@/hooks/useProviderPlaylists'
import { useTransfer } from '@/hooks/useTransfer'
import { capabilitiesOf } from '@/lib/accountCapabilities'

export default function Transfers() {
  useTranslation()
  const { accounts, loading: accountsLoading, error: accountsError } = useAccounts()
  const connectedAccounts = useMemo(() => accounts?.filter((a) => a.state === 'connected') ?? [], [accounts])
  const connectedIds = useMemo(
    () => connectedAccounts.filter((account) => capabilitiesOf(account).library_read).map((account) => account.id),
    [connectedAccounts],
  )
  const { entries, refreshProvider } = useProviderPlaylists(connectedIds)

  const [jobId, setJobId] = useState<string | null>(null)
  const [view, setView] = useState('new')
  const { job, error: jobError, refresh: refreshJob } = useTransfer(jobId)

  const { events, connected, clear } = useEventStream()
  const transferEvents = useMemo(() => events.filter((e) => e.tag === 'transfer'), [events])

  function handleStarted(newJobId: string) {
    // Fresh live view for the new job — a previous transfer's events
    // shouldn't linger in what's meant to read as "this job's progress".
    clear()
    setJobId(newJobId)
    setView('new')
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-text sm:text-[22px]">{t("Transfers")}</h1>
        <p className="mt-1 text-sm text-text-3">
          {t('Copy a playlist once, including between two accounts on the same service.')}
        </p>
      </div>

      {view === 'new' && <FeatureGuide page="transfers" />}

      <div><Segmented ariaLabel={t('Transfers view')} value={view} onChange={setView}
        options={[{ value: 'new', label: t('New transfer') }, { value: 'history', label: t('Transfer history') }]} /></div>

      {view === 'history' ? <TransferHistory onOpen={id => { setJobId(id); setView('new') }} /> : <>

      {accountsError && (
        <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{t("Could not load accounts: {{accountsError}}", { accountsError: accountsError })}</p>
      )}

      {accountsLoading && !accounts ? (
        <LoadingStatus label={t("Loading accounts…")}>
          <Skeleton className="h-72 w-full rounded-card" />
        </LoadingStatus>
      ) : (
        <TransferSetupForm accounts={connectedAccounts} entries={entries} onRefreshPlaylists={refreshProvider} onStarted={handleStarted} />
      )}

      {jobId && (
        <>
          <TransferProgress
            job={job}
            error={jobError}
            controls={
              job && !job.historical
                ? {
                    onPause: async () => {
                      await api.pauseTransfer(job.id)
                      await refreshJob()
                    },
                    onResume: async () => {
                      await api.resumeTransfer(job.id)
                      await refreshJob()
                    },
                    onStop: async () => {
                      await api.stopTransfer(job.id)
                      await refreshJob()
                    },
                  }
                : undefined
            }
          />

          {!job?.historical && <Card className="p-4 sm:p-6">
            <TransferLiveFeed events={transferEvents} connected={connected} />
          </Card>}

          {job && !job.historical && job.conflicts.length > 0 && (
            <ConflictList
              jobId={job.id}
              conflicts={job.conflicts}
              busy={['queued', 'running', 'busy', 'paused'].includes(job.status)}
              onResolved={() => void refreshJob()}
              onApply={async () => {
                await api.applyResolvedTracks(job.id)
                await refreshJob()
              }}
            />
          )}
        </>
      )}
      </>}
    </div>
  )
}
