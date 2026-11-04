/** Countdown text for the time left: "4:59", "1:05:03" or "2d 4h". */
export function formatRemaining(ms: number): string {
  if (ms <= 0) return '0:00'
  const total = Math.ceil(ms / 1000)
  const days = Math.floor(total / 86_400)
  const hours = Math.floor((total % 86_400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}:${pad(minutes)}:${pad(seconds)}`
  return `${minutes}:${pad(seconds)}`
}

/** "5 Nov 2026, 3:45 pm" in the viewer's time zone and locale. */
export function formatDateTime(iso: string, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(iso),
  )
}

/** "3:45:12 pm" in the viewer's time zone, for bid history rows. */
export function formatClock(iso: string, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { timeStyle: 'medium' }).format(new Date(iso))
}
