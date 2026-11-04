import type { Connection } from '../live/auctionFeed.ts'

export function ConnectionPill({ connection }: { connection: Connection }) {
  if (connection === 'closed') return null
  const label = { live: 'Live updates on', connecting: 'Connecting…', reconnecting: 'Reconnecting…' }[connection]
  return (
    <span className={`connection connection-${connection}`} role="status">
      <span className="dot" aria-hidden="true" />
      {label}
    </span>
  )
}
