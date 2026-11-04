import type { MockBackend } from './mockBackend.ts'

const SECOND = 1000
const MINUTE = 60 * SECOND

// Fixed fake bidders so the seeded history reads naturally.
const RIYA = { bidderId: 'b0000000-0000-4000-8000-000000000001', bidderName: 'Riya', isBot: true }
const MARCO = { bidderId: 'b0000000-0000-4000-8000-000000000002', bidderName: 'Marco', isBot: true }
const NINA = { bidderId: 'b0000000-0000-4000-8000-000000000003', bidderName: 'Nina' }

/** Demo auctions for `npm run dev:mock`: one about to end, some busy, one quiet, one ended. */
export function seedDemoAuctions(backend: MockBackend): void {
  const now = Date.now()
  const camera = backend.createAuction({ title: 'Vintage film camera', startPrice: 1_500_000, endAt: now + 4 * MINUTE })
  backend.bid(camera.id, { ...RIYA, amount: 1_550_000 })
  backend.bid(camera.id, { ...NINA, amount: 1_600_000 })
  backend.bid(camera.id, { ...MARCO, amount: 1_650_000 })

  const tickets = backend.createAuction({
    title: 'Front-row concert tickets (pair)',
    startPrice: 800_000,
    endAt: now + 75 * SECOND,
  })
  backend.bid(tickets.id, { ...MARCO, amount: 850_000 })

  const chair = backend.createAuction({ title: 'Mid-century teak armchair', startPrice: 2_000_000, endAt: now + 25 * MINUTE })
  backend.bid(chair.id, { ...NINA, amount: 2_100_000 })

  backend.createAuction({ title: 'Limited-edition sneakers', startPrice: 1_200_000, endAt: now + 2 * 60 * MINUTE })

  const painting = backend.createAuction({ title: 'Original watercolour painting', startPrice: 500_000, endAt: now + MINUTE })
  backend.bid(painting.id, { ...RIYA, amount: 550_000 })
  backend.bid(painting.id, { ...NINA, amount: 600_000 })
  backend.closeNow(painting.id)
}
