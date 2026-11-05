// Inline icons from the design. Purely decorative: every one is aria-hidden, so the
// accessible names and text around them don't change.

const round = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round', strokeLinejoin: 'round' } as const

export function InfoIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="8" {...round} strokeWidth="1.6" />
      <path d="M10 9v5M10 6.2v.1" {...round} strokeWidth="1.8" />
    </svg>
  )
}

export function ErrorIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="8" {...round} strokeWidth="1.7" />
      <path d="M10 5.8v5M10 13.8v.1" {...round} strokeWidth="1.9" />
    </svg>
  )
}

export function CheckIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 10.5l4 4 8-9" {...round} strokeWidth="2.2" />
    </svg>
  )
}

export function PauseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M7 5v10M13 5v10" {...round} strokeWidth="2" />
    </svg>
  )
}

export function ClockIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="8" {...round} strokeWidth="1.6" />
      <path d="M10 5.5V10l3 2" {...round} strokeWidth="1.8" />
    </svg>
  )
}

/** The small clock and pause marks inside the Closing and Paused badges. */
export function BadgeClockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="4.8" {...round} strokeWidth="1.4" />
      <path d="M6 3.4V6l1.6 1.2" {...round} strokeWidth="1.4" />
    </svg>
  )
}

export function BadgePauseIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M4 2.5v7M8 2.5v7" {...round} strokeWidth="1.8" />
    </svg>
  )
}

export function ChevronIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5 8l5 5 5-5" {...round} strokeWidth="1.8" />
    </svg>
  )
}

/** Coral disc with a tick, next to "You're the highest bidder" and "You won". */
export function LeadIcon() {
  return (
    <svg className="lead-icon" width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <circle cx="11" cy="11" r="10.5" className="lead-icon-disc" />
      <path d="M6.5 11.5l3 3 6-7" className="lead-icon-tick" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** The icon for a bid message, by its tone. */
export function NoticeIcon({ tone }: { tone: 'success' | 'error' | 'info' }) {
  if (tone === 'success') return <CheckIcon />
  if (tone === 'error') return <ErrorIcon />
  return <InfoIcon />
}
