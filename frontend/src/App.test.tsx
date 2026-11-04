import { fireEvent, screen, within } from '@testing-library/react'
import { expect, test } from 'vitest'
import { formatMoney } from './lib/money.ts'
import { setupApp } from './test/setupApp.tsx'

const MINUTE = 60_000

test('lists auctions: running ones first, soonest to end on top, then ended ones', async () => {
  setupApp('/', {
    seed(backend) {
      const later = backend.createAuction({ title: 'Armchair', startPrice: 100, endAt: Date.now() + 30 * MINUTE })
      backend.createAuction({ title: 'Camera', startPrice: 100, endAt: Date.now() + 5 * MINUTE })
      const ended = backend.createAuction({ title: 'Painting', startPrice: 100, endAt: Date.now() + 5 * MINUTE })
      backend.bid(later.id, { bidderId: 'b1', bidderName: 'Riya', amount: 150_000, isBot: true })
      backend.closeNow(ended.id)
    },
  })

  const cards = await screen.findAllByRole('listitem')
  expect(cards.map((card) => within(card).getByRole('heading').textContent)).toEqual([
    'Camera',
    'Armchair',
    'Painting',
  ])
  expect(within(cards[0]).getByText('No bids yet')).toBeTruthy()
  expect(within(cards[1]).getByText(formatMoney(150_000))).toBeTruthy()
  expect(within(cards[2]).getAllByText('Ended').length).toBeGreaterThan(0)
})

test('an empty list says so', async () => {
  setupApp('/')
  expect(await screen.findByText('No auctions right now')).toBeTruthy()
})

test('clicking an auction opens its page', async () => {
  setupApp('/', {
    seed: (backend) => void backend.createAuction({ title: 'Camera', startPrice: 100, endAt: Date.now() + 5 * MINUTE }),
  })

  fireEvent.click(await screen.findByRole('link', { name: /Camera/ }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Camera' })).toBeTruthy()
  expect(window.location.pathname).toMatch(/^\/auctions\//)
})

test('unknown paths show a not-found page', () => {
  setupApp('/nope')
  expect(screen.getByRole('heading', { name: 'Page not found' })).toBeTruthy()
})
