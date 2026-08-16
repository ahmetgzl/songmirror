import { useDeferredValue, useMemo, useState } from 'react'
import type { IconType } from 'react-icons'
import {
  LuArrowDownUp,
  LuClockAlert,
  LuListMinus,
  LuListPlus,
  LuRefreshCw,
  LuSearch,
  LuSearchX,
  LuSlidersHorizontal,
  LuX,
} from 'react-icons/lu'

import { EventFeedList } from '@/components/events/EventFeedList'
import { useEventStream } from '@/hooks/useEventStream'
import type { EventCounterKey } from '@/hooks/useEventStream'
import { cn } from '@/lib/cn'
import { serviceLogoId, tagDot, tagLabel, tagText } from '@/lib/constants'
import type { EventKind, SyncEvent } from '@/types'

import { CountChip, type CountChipTone } from '../ui/CountChip'
import { ServiceLogo } from '../ui/ServiceLogo'

const COUNTER_META: Array<{
  key: EventCounterKey
  label: string
  title: string
  description: string
  icon: IconType
  tone: CountChipTone
}> = [
  {
    key: 'added', label: 'added', title: 'Added this pass',
    description: 'Playlist entries written to a service.', icon: LuListPlus, tone: 'success',
  },
  {
    key: 'removed', label: 'removed', title: 'Removed this pass',
    description: 'Confirmed playlist removals that were actually applied.', icon: LuListMinus, tone: 'danger',
  },
  {
    key: 'held', label: 'held', title: 'Protected this pass',
    description: 'Changes SongMirror did not apply because the evidence or replacement was not safe yet.',
    icon: LuClockAlert, tone: 'warning',
  },
  {
    key: 'repaired', label: 'repaired', title: 'Identity drift repaired',
    description: 'The physical provider entry stayed put while its canonical metadata changed. No playlist write.',
    icon: LuRefreshCw, tone: 'neutral',
  },
  {
    key: 'missing', label: 'missing', title: 'Catalog matches missing',
    description: 'Tracks that could not be found safely on a destination service.', icon: LuSearchX, tone: 'neutral',
  },
]

const HOLD_REASON_LABELS: Record<string, string> = {
  unconfirmed_absence: 'Awaiting a second trusted read',
  confirmed_removal_disabled: 'Confirmed; removal mirroring is off',
  removal_cap: 'Confirmed; over the removal cap',
  replacement_blocked: 'Replacement could not be completed safely',
  uncertain_match: 'Catalog match was uncertain',
}

type KindFilter = 'all' | EventKind | 'system'
type SortOrder = 'oldest' | 'newest'

const KIND_OPTIONS: Array<{ value: KindFilter; label: string }> = [
  { value: 'all', label: 'All activity' },
  { value: 'add', label: 'Added' },
  { value: 'remove', label: 'Removed' },
  { value: 'hold', label: 'Held / protected' },
  { value: 'repair', label: 'Identity repaired' },
  { value: 'miss', label: 'Missing match' },
  { value: 'warn', label: 'Warnings' },
  { value: 'system', label: 'Notes & summaries' },
]

const COUNT_FORMATTER = new Intl.NumberFormat('en-US')

function CounterDetails({
  title,
  description,
  value,
  providers,
  reasons,
}: {
  title: string
  description: string
  value: number
  providers: Record<string, number>
  reasons?: Record<string, number>
}) {
  const providerRows = Object.entries(providers).sort((a, b) => b[1] - a[1] || tagLabel(a[0]).localeCompare(tagLabel(b[0])))
  const reasonRows = Object.entries(reasons ?? {}).sort((a, b) => b[1] - a[1])
  return (
    <div className="flex flex-col gap-2.5">
      <div>
        <p className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-text-3">{title}</p>
        <p className="mt-0.5 text-[13px] font-bold tabular-nums text-text">{COUNT_FORMATTER.format(value)}</p>
        <p className="mt-1 text-[11.5px] leading-relaxed text-text-2">{description}</p>
      </div>
      {providerRows.length > 0 ? (
        <div className="border-t border-border pt-2">
          <p className="mb-1.5 font-mono text-[9.5px] font-semibold uppercase tracking-wide text-text-3">By service</p>
          <div className="flex flex-col gap-1.5">
            {providerRows.map(([tag, count]) => {
              const logo = serviceLogoId(tag)
              return (
                <div key={tag} className="flex items-center gap-2">
                  {logo ? (
                    <ServiceLogo service={logo} className={cn('size-3.5 shrink-0', tagText(tag))} />
                  ) : (
                    <span className={cn('size-2 shrink-0 rounded-full', tagDot(tag))} aria-hidden="true" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-text-2">{tagLabel(tag)}</span>
                  <span className="font-mono text-[11px] font-bold tabular-nums text-text">{COUNT_FORMATTER.format(count)}</span>
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        <p className="border-t border-border pt-2 text-[11.5px] text-text-3">No events in this class yet.</p>
      )}
      {reasonRows.length > 0 && (
        <div className="border-t border-border pt-2">
          <p className="mb-1.5 font-mono text-[9.5px] font-semibold uppercase tracking-wide text-text-3">Why held</p>
          <div className="flex flex-col gap-1.5">
            {reasonRows.map(([reason, count]) => (
              <div key={reason} className="flex items-start justify-between gap-3 text-[11.5px]">
                <span className="leading-snug text-text-2">{HOLD_REASON_LABELS[reason] ?? 'Other safety hold'}</span>
                <span className="shrink-0 font-mono font-bold tabular-nums text-text">{COUNT_FORMATTER.format(count)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function matchesKind(event: SyncEvent, filter: KindFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'system') return ['note', 'summary', 'section', 'download'].includes(event.kind)
  return event.kind === filter
}

/** A live signal desk: current-pass counters disclose their service/evidence
 * ledger, while the persisted event stream can be searched, sliced and sorted
 * without changing what the sync engine records. */
export function LiveFeed() {
  const { events, counters, breakdown, holdReasons, connected } = useEventStream()
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')
  const [kind, setKind] = useState<KindFilter>('all')
  const [sort, setSort] = useState<SortOrder>('oldest')
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase())

  const providers = useMemo(
    () => [...new Set(events.map((event) => event.tag).filter(Boolean))]
      .sort((a, b) => tagLabel(a).localeCompare(tagLabel(b))),
    [events],
  )

  const visibleEvents = useMemo(() => {
    const filtered = events.filter((event) => {
      if (provider !== 'all' && event.tag !== provider) return false
      if (!matchesKind(event, kind)) return false
      if (!deferredQuery) return true
      return `${event.message} ${tagLabel(event.tag)} ${event.kind}`.toLocaleLowerCase().includes(deferredQuery)
    })
    return sort === 'newest' ? filtered.slice().reverse() : filtered
  }, [deferredQuery, events, kind, provider, sort])

  const filtered = query.trim() !== '' || provider !== 'all' || kind !== 'all'

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span
            className={cn('size-2 rounded-full', connected ? 'bg-success' : 'bg-neutral')}
            aria-hidden="true"
          />
          <span className="font-mono text-[10.5px] font-semibold tracking-wide text-text-3">LIVE FEED</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 font-mono text-[10.5px] tracking-wide text-text-3">THIS PASS</span>
          {COUNTER_META.map((counter) => (
            <CountChip
              key={counter.key}
              tone={counter.tone}
              icon={counter.icon}
              label={counter.label}
              value={counters[counter.key]}
              tooltip={(
                <CounterDetails
                  title={counter.title}
                  description={counter.description}
                  value={counters[counter.key]}
                  providers={breakdown[counter.key]}
                  reasons={counter.key === 'held' ? holdReasons : undefined}
                />
              )}
            />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2 rounded-control border border-border bg-surface-2/45 p-2 sm:flex-row sm:items-center">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">Search activity</span>
          <LuSearch className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-text-3" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search tracks, playlists…"
            className="h-8 w-full rounded-control border border-border-strong bg-field pl-8 pr-8 text-xs text-text placeholder:text-text-3 focus:border-accent focus:outline-none"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="Clear activity search"
              className="absolute right-1.5 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-chip text-text-3 hover:bg-surface-2 hover:text-text"
            >
              <LuX className="size-3.5" aria-hidden="true" />
            </button>
          )}
        </label>

        <label className="relative min-w-36">
          <span className="sr-only">Filter by service</span>
          <LuSlidersHorizontal className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-text-3" aria-hidden="true" />
          <select
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            aria-label="Filter by service"
            className="h-8 w-full appearance-none rounded-control border border-border-strong bg-field pl-8 pr-7 text-xs text-text focus:border-accent focus:outline-none"
          >
            <option value="all">All services</option>
            {providers.map((tag) => <option key={tag} value={tag}>{tagLabel(tag)}</option>)}
          </select>
        </label>

        <select
          value={kind}
          onChange={(event) => setKind(event.target.value as KindFilter)}
          aria-label="Filter by activity type"
          className="h-8 min-w-36 rounded-control border border-border-strong bg-field px-2.5 text-xs text-text focus:border-accent focus:outline-none"
        >
          {KIND_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>

        <label className="relative min-w-32">
          <span className="sr-only">Sort activity</span>
          <LuArrowDownUp className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-text-3" aria-hidden="true" />
          <select
            value={sort}
            onChange={(event) => setSort(event.target.value as SortOrder)}
            aria-label="Sort activity"
            className="h-8 w-full appearance-none rounded-control border border-border-strong bg-field pl-8 pr-7 text-xs text-text focus:border-accent focus:outline-none"
          >
            <option value="oldest">Oldest first</option>
            <option value="newest">Newest first</option>
          </select>
        </label>

        {filtered && (
          <button
            type="button"
            onClick={() => { setQuery(''); setProvider('all'); setKind('all') }}
            className="h-8 shrink-0 rounded-control px-2.5 text-xs font-semibold text-text-2 hover:bg-surface-2 hover:text-text"
          >
            Reset
          </button>
        )}
      </div>

      {filtered && (
        <p className="font-mono text-[10.5px] text-text-3" aria-live="polite">
          Showing {COUNT_FORMATTER.format(visibleEvents.length)} of {COUNT_FORMATTER.format(events.length)} events
        </p>
      )}

      <EventFeedList
        events={visibleEvents}
        newestFirst={sort === 'newest'}
        emptyTitle={filtered ? 'No matching activity' : 'No activity yet'}
        emptyDescription={filtered
          ? 'Try a different service, event type, or search term.'
          : 'Start a sync to see live progress here. Every track added, removed, protected, or repaired will show up in real time.'}
        ariaLabel="Live sync activity"
      />
    </div>
  )
}
