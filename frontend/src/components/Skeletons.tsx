/** Loading placeholders. Hidden from assistive tech: the loading text next to them is what's read. */
export function CardSkeletons() {
  return (
    <div className="skel-grid" aria-hidden="true">
      {['72%', '56%', '64%'].map((width) => (
        <div className="skel-card" key={width}>
          <span className="skel" style={{ height: 22, width }} />
          <div className="skel-row">
            <span className="skel" style={{ height: 50, flex: 1 }} />
            <span className="skel" style={{ height: 50, flex: 1 }} />
          </div>
        </div>
      ))}
    </div>
  )
}

export function AuctionSkeleton() {
  return (
    <div className="skel-auction" aria-hidden="true">
      <span className="skel" style={{ height: 40, width: '70%' }} />
      <div className="skel-statement">
        <span className="skel" style={{ height: 20, width: 144 }} />
        <span className="skel" style={{ height: 80, flex: 1 }} />
      </div>
    </div>
  )
}
