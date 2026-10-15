import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { driver, type Driver } from 'driver.js'
import 'driver.js/dist/driver.css'
import './tour.css'

import { t, useTranslation } from '@/i18n'

import { featureGuide, type FeatureGuideId } from './guides'
import { featureTour, findTourTarget } from './tours'

const STORAGE_PREFIX = 'songmirror-tour-v1:'

export function useFeatureTour(page: FeatureGuideId) {
  const { i18n } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const active = useRef<Driver | null>(null)
  const stop = useRef<(() => void) | null>(null)
  const [visited, setVisited] = useState(() => {
    try {
      return ['completed', 'skipped'].includes(window.localStorage.getItem(`${STORAGE_PREFIX}${page}`) ?? '')
    } catch {
      return false
    }
  })

  useEffect(() => () => stop.current?.(), [location.key, i18n.resolvedLanguage])

  function start(launcher: HTMLElement) {
    stop.current?.()
    const steps = featureTour(page).filter(step => findTourTarget(step.targets))
    if (!steps.length) {
      const guide = featureGuide(page)
      steps.push({ targets: ['#main-content h1'], title: guide.feature,
        body: guide.intro ?? guide.note ?? guide.steps[0].body })
    }
    const root = document.getElementById('root')
    const previousInert = root?.inert ?? false
    let completed = false
    let cleaned = false
    let positionFrame: number | undefined
    function position() {
      if (positionFrame !== undefined) cancelAnimationFrame(positionFrame)
      positionFrame = requestAnimationFrame(() => {
        positionFrame = undefined
        const element = tour.getActiveElement()
        if (!tour.isActive() || !element) return
        const box = element.getBoundingClientRect()
        // Leave room below a small-screen target for its explanation. A
        // resize can move a formerly visible desktop control off screen.
        if (window.innerWidth < 1024) {
          const headerBottom = document.querySelector('[data-tour="shell-header"]')?.getBoundingClientRect().bottom ?? 0
          const top = headerBottom + 12
          const popoverHeight = document.querySelector('.songmirror-tour')?.getBoundingClientRect().height ?? 0
          if (box.top < top || box.bottom > window.innerHeight - popoverHeight - 28) {
            window.scrollBy({ top: box.top - top, behavior: 'instant' })
          }
        } else if (box.top >= window.innerHeight || box.bottom <= 0) {
          element.scrollIntoView({ block: 'center', behavior: 'instant' })
        }
        tour.refresh()
      })
    }
    const resize = new ResizeObserver(() => active.current?.refresh())
    const mutation = new MutationObserver(() => {
      const element = active.current?.getActiveElement()
      if (element && (!element.isConnected || !element.getClientRects().length || element.closest('[hidden]'))) {
        if (tour.hasNextStep()) tour.moveNext()
        else close()
      }
    })
    function tourKeys(event: KeyboardEvent) {
      // Escape must also work between steps, while the next spotlight is
      // being positioned and the library's own keyboard handler is waiting.
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopImmediatePropagation()
        close()
        return
      }
      if (document.documentElement.dir !== 'rtl' || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      if (event.key === 'ArrowRight') {
        if (tour.hasPreviousStep()) tour.movePrevious()
      } else if (tour.hasNextStep()) {
        tour.moveNext()
      } else {
        completed = true
        close()
      }
    }

    const tour = driver({
      animate: false,
      smoothScroll: false,
      overlayOpacity: 0.68,
      overlayClickBehavior: 'none',
      stagePadding: 6,
      stageRadius: 8,
      popoverOffset: 14,
      popoverClass: 'songmirror-tour',
      showProgress: true,
      showButtons: ['previous', 'next', 'close'],
      disableActiveInteraction: true,
      skipMissingElement: true,
      nextBtnText: t('Next'),
      prevBtnText: t('Back'),
      doneBtnText: t('Done'),
      closeBtnLabel: t('Skip tour'),
      steps: steps.map(step => ({
        element: () => findTourTarget(step.targets)!,
        // Content is set as text in onPopoverRender, never parsed as HTML.
        popover: { title: ' ', description: ' ', side: 'bottom', align: 'start' },
      })),
      onPopoverRender(popover, { index }) {
        const step = steps[index ?? 0]
        popover.wrapper.dir = document.documentElement.dir || 'ltr'
        popover.wrapper.setAttribute('aria-modal', 'true')
        popover.title.textContent = step.title
        popover.description.textContent = step.body
        popover.closeButton.textContent = t('Skip tour')
        popover.title.style.paddingInlineEnd = `${popover.closeButton.offsetWidth + 12}px`
        popover.progress.textContent = t('Step {{current, number}} of {{total, number}}', {
          current: (index ?? 0) + 1, total: steps.length,
        })
        popover.progress.setAttribute('aria-live', 'polite')
        if (step.to) {
          const link = document.createElement('a')
          link.href = step.to
          link.className = 'songmirror-tour-link'
          link.textContent = step.linkLabel ?? step.title
          link.addEventListener('click', event => {
            event.preventDefault()
            close()
            navigate(step.to!)
          })
          popover.description.appendChild(link)
        }
        resize.observe(popover.wrapper)
        position()
      },
      onHighlighted(element) {
        if (element) resize.observe(element)
      },
      onDoneClick() {
        completed = true
        close()
      },
      onCloseClick: () => close(),
      onDestroyed: () => cleanup(),
    })

    function cleanup() {
      if (cleaned) return
      cleaned = true
      resize.disconnect()
      mutation.disconnect()
      if (positionFrame !== undefined) cancelAnimationFrame(positionFrame)
      window.removeEventListener('resize', position)
      window.removeEventListener('keyup', tourKeys, true)
      if (root) root.inert = previousInert
      active.current = null
      stop.current = null
      setVisited(true)
      try {
        window.localStorage.setItem(`${STORAGE_PREFIX}${page}`, completed ? 'completed' : 'skipped')
      } catch {
        // A blocked preference store must never trap someone in a tour.
      }
      requestAnimationFrame(() => {
        if (!active.current && launcher.isConnected) launcher.focus()
      })
    }

    function close() {
      tour.destroy()
      // Teardown can happen before the first highlight has finished rendering,
      // when the library has no active step on which to call onDestroyed.
      cleanup()
    }

    active.current = tour
    stop.current = close
    window.addEventListener('resize', position)
    window.addEventListener('keyup', tourKeys, true)
    if (root) root.inert = true
    mutation.observe(document.getElementById('main-content') ?? document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'],
    })
    tour.drive()
  }

  return { start, visited }
}
