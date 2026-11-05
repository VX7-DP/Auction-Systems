import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import type { Transport } from '../api/transport.ts'
import { formatMoney, nextBid } from '../lib/money.ts'
import type { MockBackend } from '../mock/mockBackend.ts'
import { setupApp } from '../test/setupApp.tsx'

const MINUTE = 60_000
const RIYA = { bidderId: 'b0000000-0000-4000-8000-000000000001', bidderName: 'Riya', isBot: true }
const BEN = { bidderId: 'b0000000-0000-4000-8000-000000000002', bidderName: 'Ben' }

let auctionId = ''

function openAuction(options: { signedInAs?: string; wrap?: (t: Transport) => Transport; minutes?: number } = {}) {
  let id = ''
  const app = setupApp('/', {
    wrap: options.wrap,
    seed(backend) {
      id = backend.createAuction({
        title: 'Vintage camera',
        startPrice: 100_000,
        endAt: Date.now() + (options.minutes ?? 5) * MINUTE,
      }).id
      if (options.signedInAs) backend.signInAs(options.signedInAs)
    },
  })
  auctionId = id
  act(() => {
    window.history.pushState(null, '', `/auctions/${id}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  return app
}

/** Wait until the page is live (socket open and resynced). */
async function live() {
  await screen.findByText('Live updates on')
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
}

/** A bid placed by someone else (another browser or a bot), straight on the server. */
async function serverBid(backend: MockBackend, bidder: typeof RIYA | typeof BEN, amount: number) {
  await act(async () => {
    backend.bid(auctionId, { ...bidder, amount })
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

const priceCard = () => screen.getByRole('region', { name: 'Current bid' })
const history = () => screen.getByRole('region', { name: 'Bid history' })

describe('auction page', () => {
  test('shows the snapshot: opening price, countdown and an empty history', async () => {
    openAuction()
    expect(await screen.findByRole('heading', { level: 1, name: 'Vintage camera' })).toBeTruthy()
    expect(within(priceCard()).getByText(formatMoney(100_000))).toBeTruthy()
    expect(within(priceCard()).getByText(/^4:5\d$|^5:00$/)).toBeTruthy()
    expect(within(history()).getByText('No bids yet.')).toBeTruthy()
  })

  test('a bot bid arrives live, labelled as a bot', async () => {
    const { backend } = openAuction()
    await live()
    await serverBid(backend, RIYA, 120_000)

    await waitFor(() => expect(within(priceCard()).getByText(formatMoney(120_000))).toBeTruthy())
    expect(within(priceCard()).getByText('Riya')).toBeTruthy()
    expect(within(priceCard()).getByText('Bot')).toBeTruthy()
    const row = within(history()).getAllByRole('listitem')[0]
    expect(within(row).getByText('Bot')).toBeTruthy()
  })

  test('a guest picks a name inline, then bids and leads', async () => {
    openAuction()
    fireEvent.change(await screen.findByLabelText('Display name'), { target: { value: 'Asha' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start bidding' }))

    const amount = nextBid(null, 100_000)
    fireEvent.click(await screen.findByRole('button', { name: `Bid ${formatMoney(amount)}` }))

    expect(await screen.findByText(`Your bid of ${formatMoney(amount)} is in.`)).toBeTruthy()
    expect(await screen.findByText("You're the highest bidder")).toBeTruthy()
    const row = within(history()).getAllByRole('listitem')[0]
    expect(within(row).getByText('You')).toBeTruthy()
  })

  test('a custom amount must beat the current high bid', async () => {
    const { backend } = openAuction({ signedInAs: 'Asha' })
    await live()
    await serverBid(backend, BEN, 150_000)
    await waitFor(() => expect(within(priceCard()).getByText(formatMoney(150_000))).toBeTruthy())

    fireEvent.change(screen.getByLabelText('Or enter your own amount'), { target: { value: '1500' } })
    fireEvent.click(screen.getByRole('button', { name: 'Bid' }))
    expect(await screen.findByText(`Bid more than ${formatMoney(150_000)}.`)).toBeTruthy()
    expect(backend.requests.filter((r) => r.path.endsWith('/bids'))).toHaveLength(0)
  })

  test('a first bid must beat the opening price, and is refused before it is sent', async () => {
    const { backend } = openAuction({ signedInAs: 'Asha' })
    await live()

    fireEvent.change(screen.getByLabelText('Or enter your own amount'), { target: { value: '1000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Bid' }))
    expect(await screen.findByText(`Bid more than the opening price of ${formatMoney(100_000)}.`)).toBeTruthy()
    expect(backend.requests.filter((r) => r.path.endsWith('/bids'))).toHaveLength(0)
  })

  test('an auction that ended without bids does not invite a first bid', async () => {
    const { backend } = openAuction()
    await live()
    act(() => backend.closeNow(auctionId))

    expect(await screen.findByText('No bids were placed.')).toBeTruthy()
    expect(within(priceCard()).getByText('Opening price')).toBeTruthy()
    expect(within(priceCard()).queryByText('Winning bid')).toBeNull()
    expect(screen.queryByText(/Be the first/)).toBeNull()
  })

  test('shows the outbid notice when someone beats my bid', async () => {
    const { backend } = openAuction({ signedInAs: 'Asha' })
    await live()
    fireEvent.click(await screen.findByRole('button', { name: /^Bid / }))
    await screen.findByText("You're the highest bidder")

    await serverBid(backend, RIYA, 500_000)

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain("You've been outbid.")
    expect(within(alert).getByText('Bot')).toBeTruthy()
    expect(alert.textContent).toContain(formatMoney(500_000))
    fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText("You've been outbid.")).toBeNull()
  })

  test('a missed event (seq gap) is repaired from a fresh snapshot', async () => {
    const { backend } = openAuction()
    await live()
    const snapshotsBefore = backend.requests.filter((r) => r.path === `/api/auctions/${auctionId}`).length

    backend.dropNextEvent(auctionId)
    await serverBid(backend, BEN, 110_000) // lost in transit
    await serverBid(backend, RIYA, 130_000) // arrives with a gap

    await waitFor(() => expect(within(priceCard()).getByText(formatMoney(130_000))).toBeTruthy())
    await waitFor(() => expect(within(history()).getAllByRole('listitem')).toHaveLength(2))
    expect(within(history()).getByText(formatMoney(110_000))).toBeTruthy()
    const snapshotsAfter = backend.requests.filter((r) => r.path === `/api/auctions/${auctionId}`).length
    expect(snapshotsAfter).toBeGreaterThan(snapshotsBefore)
  })

  test('after a dropped connection it reconnects and catches up', async () => {
    const { backend } = openAuction()
    await live()

    act(() => backend.disconnectSockets(auctionId))
    expect(await screen.findByText('Reconnecting…')).toBeTruthy()
    await serverBid(backend, BEN, 140_000) // happens while we're offline

    expect(await screen.findByText('Live updates on', {}, { timeout: 3000 })).toBeTruthy()
    await waitFor(() => expect(within(priceCard()).getByText(formatMoney(140_000))).toBeTruthy())
  })

  test('a stale page gets "someone bid higher" and catches up', async () => {
    const { backend } = openAuction({ signedInAs: 'Asha' })
    await live()
    backend.dropNextEvent(auctionId)
    await serverBid(backend, BEN, 900_000) // we never hear about it

    fireEvent.click(screen.getByRole('button', { name: /^Bid / }))

    expect(await screen.findByText('Someone bid higher first. Try a higher amount.')).toBeTruthy()
    await waitFor(() => expect(within(priceCard()).getByText(formatMoney(900_000))).toBeTruthy())
  })

  test('a retry after a lost response reuses the bid_id, so the bid is placed once', async () => {
    let failNextBid = true
    const { backend } = openAuction({
      signedInAs: 'Asha',
      wrap: (transport) => ({
        ...transport,
        async fetch(url, init) {
          const response = await transport.fetch(url, init)
          if (url.endsWith('/bids') && failNextBid) {
            failNextBid = false
            throw new TypeError('Failed to fetch') // the bid reached the server, the answer didn't
          }
          return response
        },
      }),
    })
    await live()

    fireEvent.click(screen.getByRole('button', { name: /^Bid / }))
    expect(await screen.findByText(/We couldn't confirm your bid/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Try again/ }))
    expect(await screen.findByText(/is in\.$/)).toBeTruthy()

    const bids = backend.requests.filter((r) => r.path.endsWith('/bids'))
    expect(bids).toHaveLength(2)
    expect((bids[0].body as { bid_id: string }).bid_id).toBe((bids[1].body as { bid_id: string }).bid_id)
    expect(backend.snapshot(auctionId).recent_bids).toHaveLength(1)
  })

  test('shows the paused state', async () => {
    const { backend } = openAuction({ signedInAs: 'Asha' })
    await live()
    act(() => backend.pause(auctionId))

    expect(await screen.findByText(/Bidding is paused for a moment\. It will resume shortly; no bids are lost\./)).toBeTruthy()
    expect((screen.getByRole('button', { name: /^Bid ₹|^Bid \$/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  test('when the auction closes it shows the winner and removes the bid box', async () => {
    const { backend } = openAuction({ signedInAs: 'Asha' })
    await live()
    await serverBid(backend, BEN, 300_000)
    act(() => backend.closeNow(auctionId))

    expect(await screen.findByText(/Auction ended\. Won by/)).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Place a bid' })).toBeNull()
    expect(within(priceCard()).getByText('Winning bid')).toBeTruthy()
  })

  test('my win is announced', async () => {
    const { backend } = openAuction({ signedInAs: 'Asha' })
    await live()
    fireEvent.click(screen.getByRole('button', { name: /^Bid / }))
    await screen.findByText("You're the highest bidder")
    act(() => backend.closeNow(auctionId))

    expect(await screen.findByText('You won this auction')).toBeTruthy()
  })

  test('an unknown auction shows not found', async () => {
    setupApp('/auctions/00000000-0000-4000-8000-00000000abcd')
    expect(await screen.findByRole('heading', { name: 'Auction not found' })).toBeTruthy()
  })
})
