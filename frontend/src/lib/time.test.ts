import { describe, expect, test } from 'vitest'
import { formatRemaining } from './time.ts'

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

describe('formatRemaining', () => {
  test.each([
    [-5 * SECOND, '0:00'],
    [0, '0:00'],
    [400, '0:01'], // rounds up so "0:00" only shows once time is really up
    [9 * SECOND, '0:09'],
    [4 * MINUTE + 59 * SECOND, '4:59'],
    [59 * MINUTE + 59 * SECOND, '59:59'],
    [HOUR, '1:00:00'],
    [HOUR + 5 * MINUTE + 3 * SECOND, '1:05:03'],
    [2 * DAY + 4 * HOUR + 10 * MINUTE, '2d 4h'],
  ])('%i ms -> %s', (ms, expected) => {
    expect(formatRemaining(ms)).toBe(expected)
  })
})
