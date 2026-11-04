import { describe, expect, test } from 'vitest'
import { MAX_AMOUNT } from '../api/contract.ts'
import { currencySymbol, formatMoney, minorDigits, nextBid, niceStep, parseAmount } from './money.ts'

describe('formatMoney', () => {
  test('drops decimals for whole amounts', () => {
    expect(formatMoney(150000, 'INR', 'en-US')).toBe('₹1,500')
    expect(formatMoney(150000, 'USD', 'en-US')).toBe('$1,500')
  })

  test('keeps decimals when there are paise or cents', () => {
    expect(formatMoney(150050, 'USD', 'en-US')).toBe('$1,500.50')
  })

  test('uses the viewer locale for grouping', () => {
    expect(formatMoney(10000000, 'INR', 'en-IN')).toBe('₹1,00,000')
  })

  test('handles zero-decimal currencies', () => {
    expect(minorDigits('JPY')).toBe(0)
    expect(formatMoney(1500, 'JPY', 'en-US')).toBe('¥1,500')
  })
})

test('currencySymbol', () => {
  expect(currencySymbol('INR', 'en-US')).toBe('₹')
  expect(currencySymbol('USD', 'en-US')).toBe('$')
})

describe('parseAmount', () => {
  test.each([
    ['1500', 150000],
    ['1,500', 150000],
    [' 1500.5 ', 150050],
    ['1500.05', 150005],
    ['0.01', 1],
    ['₹2,000', 200000],
  ])('parses %s', (input, expected) => {
    expect(parseAmount(input, 'INR')).toEqual({ ok: true, value: expected })
  })

  test.each(['', '   ', 'abc', '-5', '0', '0.00', '1.234', '1.2.3', '1e5'])('rejects %j', (input) => {
    expect(parseAmount(input, 'INR').ok).toBe(false)
  })

  test('rejects amounts above the contract maximum', () => {
    const tooBig = String(MAX_AMOUNT) // in major units, so 100x the maximum in minor units
    expect(parseAmount(tooBig, 'INR').ok).toBe(false)
  })

  test('allows zero only when asked (start prices)', () => {
    expect(parseAmount('0', 'INR', { allowZero: true })).toEqual({ ok: true, value: 0 })
    expect(parseAmount('0', 'INR').ok).toBe(false)
  })

  test('rejects decimals for zero-decimal currencies', () => {
    expect(parseAmount('15.5', 'JPY').ok).toBe(false)
    expect(parseAmount('1500', 'JPY')).toEqual({ ok: true, value: 1500 })
  })
})

describe('bid suggestions', () => {
  test('niceStep is about 2.5% of the price, rounded to 1, 2 or 5 units', () => {
    expect(niceStep(100000, 2)).toBe(5000) // 1,000 -> step 50
    expect(niceStep(1500000, 2)).toBe(50000) // 15,000 -> step 500
    expect(niceStep(0, 2)).toBe(100) // never below one major unit
    expect(niceStep(3000, 2)).toBe(100)
  })

  test('nextBid steps up from the high bid, or from the start price before any bid', () => {
    expect(nextBid(null, 100000, 2)).toBe(105000)
    expect(nextBid(120000, 100000, 2)).toBe(125000)
    expect(nextBid(null, 0, 2)).toBe(100)
  })
})
