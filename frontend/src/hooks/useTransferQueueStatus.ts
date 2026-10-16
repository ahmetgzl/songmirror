import useSWR from 'swr'

import { api } from '@/api'
import type { TransferJob, TransferStatus } from '@/types'

function legacyQueueCandidate(job: TransferJob) {
  // Older servers set running before joining the shared queue. A concurrent
  // sync proves this copy cannot yet be reading through that engine slot.
  return job.status === 'running' && (!job.phase || job.phase === 'reading') &&
    job.total === 0 && job.processed === 0 && !job.source.playlist_name
}

export function useTransferQueueStatus(jobs: TransferJob[]) {
  const { data } = useSWR(jobs.some(legacyQueueCandidate) ? 'transfer-queue-sync-status' : null,
    api.getSyncStatus, { refreshInterval: 4000, dedupingInterval: 1000 })
  return (job: TransferJob): TransferStatus =>
    legacyQueueCandidate(job) && data?.running ? 'queued' : job.status
}
