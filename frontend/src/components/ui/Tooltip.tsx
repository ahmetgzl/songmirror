import { cloneElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { cn } from '@/lib/cn'

interface TooltipPosition {
  top: number
  left: number
}

interface TooltipChildProps {
  'aria-describedby'?: string
}

const VIEWPORT_GUTTER = 8
const TOOLTIP_GAP = 8

/** Hover/focus tooltip rendered at the document layer. Portalling is essential:
 * several dashboard and dialog cards deliberately clip their contents, and no
 * z-index can escape an overflow clipping boundary. Placement prefers above
 * the trigger, flips below when needed, and remains inside the viewport. */
export function Tooltip({
  content,
  children,
  className,
}: {
  content: ReactNode
  children: ReactElement<TooltipChildProps>
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<TooltipPosition | null>(null)
  const triggerRef = useRef<HTMLSpanElement>(null)
  const tooltipRef = useRef<HTMLSpanElement>(null)
  const tooltipId = useId()

  function close() {
    setOpen(false)
    setPosition(null)
  }

  function measure() {
    const trigger = triggerRef.current
    const tooltip = tooltipRef.current
    if (!trigger || !tooltip) return

    const triggerRect = trigger.getBoundingClientRect()
    const tooltipRect = tooltip.getBoundingClientRect()
    const left = Math.max(
      VIEWPORT_GUTTER,
      Math.min(triggerRect.right - tooltipRect.width, window.innerWidth - tooltipRect.width - VIEWPORT_GUTTER),
    )
    const above = triggerRect.top - tooltipRect.height - TOOLTIP_GAP
    const below = triggerRect.bottom + TOOLTIP_GAP
    const preferredTop = above >= VIEWPORT_GUTTER ? above : below
    const top = Math.max(
      VIEWPORT_GUTTER,
      Math.min(preferredTop, window.innerHeight - tooltipRect.height - VIEWPORT_GUTTER),
    )

    setPosition((current) => (
      current?.top === top && current.left === left ? current : { top, left }
    ))
  }

  useLayoutEffect(() => {
    if (!open) return
    measure()
    const frame = window.requestAnimationFrame(measure)
    return () => window.cancelAnimationFrame(frame)
  }, [open])

  useEffect(() => {
    if (!open) return
    const observer = new ResizeObserver(measure)
    if (triggerRef.current) observer.observe(triggerRef.current)
    if (tooltipRef.current) observer.observe(tooltipRef.current)
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [open])

  const describedBy = [children.props['aria-describedby'], open ? tooltipId : null]
    .filter(Boolean)
    .join(' ') || undefined

  return (
    <span
      ref={triggerRef}
      className={cn('inline-flex', className)}
      onPointerEnter={() => setOpen(true)}
      onPointerLeave={close}
      onFocusCapture={() => setOpen(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) close()
      }}
      onKeyDownCapture={(event) => {
        if (event.key === 'Escape') close()
      }}
    >
      {cloneElement(children, { 'aria-describedby': describedBy })}
      {open && createPortal(
        <span
          ref={tooltipRef}
          id={tooltipId}
          role="tooltip"
          style={position ? {
            position: 'fixed',
            top: position.top,
            left: position.left,
          } : { position: 'fixed', visibility: 'hidden' }}
          className="pointer-events-none z-[100] block max-h-[calc(100vh-1rem)] w-64 overflow-hidden rounded-control border border-border bg-surface px-3.5 py-2.5 text-[12px] leading-relaxed text-text-2 shadow-lg"
        >
          {content}
        </span>,
        document.body,
      )}
    </span>
  )
}
