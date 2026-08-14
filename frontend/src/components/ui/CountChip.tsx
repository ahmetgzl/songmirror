import type { IconType } from 'react-icons'

import { cn } from '@/lib/cn'

export type CountChipTone = 'success' | 'danger' | 'warning' | 'neutral'

const TONE_CLASSES: Record<CountChipTone, string> = {
  success: 'bg-success-soft text-success',
  danger: 'bg-danger-soft text-danger',
  warning: 'bg-warning-soft text-warning',
  neutral: 'bg-neutral-soft text-neutral',
}

interface CountChipProps {
  tone: CountChipTone
  sign?: string
  icon?: IconType
  label?: string
  value: number
  className?: string
}

/** font-mono tabular figures so ticking numbers never wobble. */
export function CountChip({ tone, sign = '', icon: Icon, label, value, className }: CountChipProps) {
  return (
    <span
      aria-label={label ? `${value} ${label} this pass` : undefined}
      title={label ? `${value} ${label} this pass` : undefined}
      className={cn(
        'inline-flex h-6 items-center gap-1.5 rounded-chip border border-border px-2 font-mono text-xs font-bold tabular-nums',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {Icon ? <Icon className="size-3.5 shrink-0" aria-hidden="true" /> : sign}
      <span>{value}</span>
      {label ? <span className="hidden font-sans text-[10px] font-semibold sm:inline">{label}</span> : null}
    </span>
  )
}
