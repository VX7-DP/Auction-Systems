import { describe, expect, test } from 'vitest'
import { matchRoute, safeNext } from './router.ts'

describe('matchRoute', () => {
  test.each([
    ['/', { name: 'list' }],
    ['/auctions/abc-123', { name: 'auction', id: 'abc-123' }],
    ['/auctions/abc-123/', { name: 'auction', id: 'abc-123' }],
    ['/sign-in', { name: 'sign-in' }],
    ['/admin', { name: 'admin' }],
    ['/nope', { name: 'not-found' }],
    ['/auctions/', { name: 'not-found' }],
    ['/auctions/a/b', { name: 'not-found' }],
    ['/auctions/%E0%A4%A', { name: 'not-found' }], // a broken %-escape
  ])('%s', (path, expected) => {
    expect(matchRoute(path)).toEqual(expected)
  })
})

describe('safeNext', () => {
  test('keeps same-site paths', () => {
    expect(safeNext('/auctions/1')).toBe('/auctions/1')
  })

  test.each([null, '', 'https://evil.example', '//evil.example', '/\\evil.example', 'auctions'])(
    'falls back to / for %j',
    (next) => {
      expect(safeNext(next)).toBe('/')
    },
  )
})
