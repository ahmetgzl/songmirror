import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { driver, type Driver } from 'driver.js'
import 'driver.js/dist/driver.css'
import './tour.css'

import { t, useTranslation } from '@/i18n'

import { featureGuide, type FeatureGuideId } from './guides'
import { featureTour, findTourTarget } from './tours'
import { layoutTour } from './tourLayout'

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
    const frame = document.createElement('div')
    frame.className = 'songmirror-tour-highlight'
    frame.setAttribute('aria-hidden', 'true')
    frame.hidden = true
    document.body.appendChild(frame)
    let completed = false
    let cleaned = false
    let positionFrame: number | undefined
    function position() {
      if (cleaned || positionFrame !== undefined) return
      // Driver positions on its own animation frame after scroll/resize. Apply
      // our viewport-aware placement after that pass and after translated text fits.
      positionFrame = requestAnimationFrame(() => {
        tour.refresh()
        positionFrame = requestAnimationFrame(() => {
          positionFrame = undefined
          const element = tour.getActiveElement()
          const popover = document.querySelector<HTMLElement>('.songmirror-tour')
          if (!tour.isActive() || !element || !popover) return
          if (layoutTour(element, popover, frame)) position()
        })
      })
    }
    const resize = new ResizeObserver(position)
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
        position()
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
      frame.remove()
      if (positionFrame !== undefined) cancelAnimationFrame(positionFrame)
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position, true)
      window.visualViewport?.removeEventListener('resize', position)
      window.visualViewport?.removeEventListener('scroll', position)
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
    window.addEventListener('scroll', position, { capture: true, passive: true })
    window.visualViewport?.addEventListener('resize', position)
    window.visualViewport?.addEventListener('scroll', position)
    window.addEventListener('keyup', tourKeys, true)
    if (root) root.inert = true
    mutation.observe(document.getElementById('main-content') ?? document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'],
    })
    tour.drive()
  }

  return { start, visited }
}
