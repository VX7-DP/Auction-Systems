import { expect, test } from 'vitest'
import { newBidId } from './bidId.ts'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

test('makes distinct v4 UUIDs', () => {
  const a = newBidId()
  expect(a).toMatch(UUID_V4)
  expect(newBidId()).not.toBe(a)
})

test('falls back to getRandomValues where randomUUID is missing (plain HTTP)', () => {
  const insecure = { getRandomValues: crypto.getRandomValues.bind(crypto) }
  const id = newBidId(insecure)
  expect(id).toMatch(UUID_V4)
})
