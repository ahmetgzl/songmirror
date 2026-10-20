import { useId, useState } from 'react'
import { flushSync } from 'react-dom'
import { Link } from 'react-router-dom'
import { LuChevronDown } from 'react-icons/lu'

import { t, useTranslation } from '@/i18n'
import { cn } from '@/lib/cn'
import { formatNumber } from '@/lib/format'
import { useFeatureGuides } from '@/hooks/useFeatureGuides'

import { featureGuide, type FeatureGuideId } from './guides'
import { useFeatureTour } from './useFeatureTour'
import { Button } from '../ui/Button'

const STORAGE_PREFIX = 'songmirror-guide-v1:'

/** A page keeps its own disclosure preference, including Settings sections.
 * Key the inner component so client-side navigation reads the correct preference. */
export function FeatureGuide({ page }: { page: FeatureGuideId }) {
  const [visible] = useFeatureGuides()
  return visible ? <GuideDisclosure key={page} page={page} /> : null
}

function GuideDisclosure({ page }: { page: FeatureGuideId }) {
  useTranslation()
  const guide = featureGuide(page)
  const headingId = useId()
  const contentId = useId()
  const tour = useFeatureTour(page)
  const [open, setOpen] = useState(() => {
    try {
      return window.localStorage.getItem(`${STORAGE_PREFIX}${page}`) !== 'hidden'
    } catch {
      return true
    }
  })

  function toggle() {
    const next = !open
    setOpen(next)
    try {
      window.localStorage.setItem(`${STORAGE_PREFIX}${page}`, next ? 'visible' : 'hidden')
    } catch {
      // Guidance remains usable when browser storage is unavailable.
    }
  }

  const List = guide.choices ? 'ul' : 'ol'

  return (
    <section aria-labelledby={headingId} className="rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-2 sm:px-5">
        <h2 id={headingId} className="py-1 text-sm font-semibold text-text">
          {guide.title ?? t('Quick guide: {{feature}}', { feature: guide.feature })}
        </h2>
        <div className="ms-auto flex items-center gap-3">
          <Button
            data-tour-launcher={page}
            variant="secondary"
            size="sm"
            onClick={(event) => {
              const launcher = event.currentTarget
              // Expose dashboard tasks before the tour resolves its targets.
              if (page === 'dashboard' && !open) flushSync(() => setOpen(true))
              tour.start(launcher)
            }}
          >
            {tour.visited ? t('Replay tour') : t('Start tour')}
          </Button>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={contentId}
            onClick={toggle}
            className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-control px-1 text-xs font-medium text-text-2 hover:text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {open ? t('Hide guide') : t('Show guide')}
            <LuChevronDown className={cn('size-3.5', open && 'rotate-180')} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div id={contentId} hidden={!open} className="border-t border-border px-4 pb-4 pt-4 sm:px-5">
        {guide.intro && <p className="mb-4 text-sm leading-relaxed text-text-2">{guide.intro}</p>}
        <List className={cn('grid gap-4', guide.choices ? 'sm:grid-cols-2 xl:grid-cols-4' : 'md:grid-cols-3')}>
          {guide.steps.map((step, index) => (
            <li key={index} className="flex min-w-0 gap-2.5">
              {!guide.choices && <span aria-hidden="true" className="pt-0.5 text-xs font-semibold text-text-3">{formatNumber(index + 1)}.</span>}
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-text">
                  {step.to ? (
                    <Link data-tour={page === 'dashboard' ? `dashboard-task-${index}` : undefined} to={step.to} className="rounded-sm text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
                      {step.title}
                    </Link>
                  ) : step.title}
                </h3>
                <p className="mt-1 text-[13px] leading-relaxed text-text-2">{step.body}</p>
              </div>
            </li>
          ))}
        </List>
        {guide.note && <p className="mt-4 border-t border-border pt-3 text-xs leading-relaxed text-text-2">{guide.note}</p>}
      </div>
    </section>
  )
}
