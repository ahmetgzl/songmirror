const MARGIN = 12
const STAGE_PADDING = 6
const GAP = 14 + STAGE_PADDING

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max))

/** Position against the visible viewport, including zoom and nested scroll areas. */
export function layoutTour(element: Element, popover: HTMLElement, frame: HTMLElement): boolean {
  const visual = window.visualViewport
  const left = (visual?.offsetLeft ?? 0) + MARGIN
  const top = (visual?.offsetTop ?? 0) + MARGIN
  const right = left + Math.min(visual?.width ?? innerWidth, document.documentElement.clientWidth) - MARGIN * 2
  const bottom = top + (visual?.height ?? innerHeight) - MARGIN * 2

  popover.style.display = 'flex'
  popover.style.maxWidth = `${right - left}px`
  popover.style.maxHeight = `${bottom - top}px`
  let size = popover.getBoundingClientRect()
  let target = element.getBoundingClientRect()
  const header = document.querySelector('[data-tour="shell-header"]')?.getBoundingClientRect()
  const visibleTop = Math.max(top, header && header.height ? header.bottom + MARGIN : top)
  const beside = target.right + GAP + size.width <= right || target.left - GAP - size.width >= left
  const belowTarget = bottom - visibleTop - target.height - GAP
  if (!beside && belowTarget >= 160 && belowTarget < size.height) {
    // Keep the title and navigation visible; long translated guidance can
    // scroll independently instead of pushing the footer off a short screen.
    popover.style.maxHeight = `${belowTarget}px`
    size = popover.getBoundingClientRect()
  }

  // Only reserve space beneath the target when the tooltip cannot fit beside it.
  // This prevents the desktop placement from needlessly jumping to the page top.
  const availableBottom = beside ? bottom : bottom - size.height - GAP
  if (target.top < visibleTop || target.bottom > availableBottom) {
    const desiredTop = target.height <= availableBottom - visibleTop
      ? clamp(target.top, visibleTop, availableBottom - target.height)
      : visibleTop
    const delta = target.top - desiredTop
    if (Math.abs(delta) > 1) {
      const previousTop = target.top
      // Scroll the actual enclosing panel as well as the document when necessary.
      element.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' })
      target = element.getBoundingClientRect()
      window.scrollBy({ top: target.top - desiredTop, behavior: 'instant' })
      target = element.getBoundingClientRect()
      if (Math.abs(previousTop - target.top) > 1) return true
    }
  }

  const rtl = document.documentElement.dir === 'rtl'
  const start = rtl ? target.right - size.width : target.left
  const candidates = {
    right: { x: target.right + GAP, y: clamp(target.top - STAGE_PADDING, top, bottom - size.height) },
    left: { x: target.left - GAP - size.width, y: clamp(target.top - STAGE_PADDING, top, bottom - size.height) },
    bottom: { x: clamp(start, left, right - size.width), y: target.bottom + GAP },
    top: { x: clamp(start, left, right - size.width), y: target.top - GAP - size.height },
  }
  const order = rtl ? ['left', 'right', 'bottom', 'top'] as const : ['right', 'left', 'bottom', 'top'] as const
  const side = order.find(name => {
    const point = candidates[name]
    return point.x >= left && point.y >= top && point.x + size.width <= right && point.y + size.height <= bottom
  })
  const point = side ? candidates[side] : { x: start, y: bottom - size.height }
  popover.style.inset = `${clamp(point.y, top, bottom - size.height)}px auto auto ${clamp(point.x, left, right - size.width)}px`
  popover.dataset.placement = side ?? 'viewport'

  // A separate frame sits above the dimmer. An outline on the target itself
  // is clipped by the spotlight's rounded cutout and by overflow containers.
  frame.style.inset = `${target.top - STAGE_PADDING}px auto auto ${target.left - STAGE_PADDING}px`
  frame.style.width = `${target.width + STAGE_PADDING * 2}px`
  frame.style.height = `${target.height + STAGE_PADDING * 2}px`
  frame.hidden = false
  return false
}
