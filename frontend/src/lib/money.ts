import { MAX_AMOUNT } from '../api/contract.ts'
import { CURRENCY } from '../config.ts'

/** Digits after the decimal point for a currency: 2 for INR, USD and EUR, 0 for JPY. */
export function minorDigits(currency: string = CURRENCY): number {
  return new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions()
    .maximumFractionDigits ?? 2
}

/** Formats an amount in minor units, without decimals when it is a whole amount. */
export function formatMoney(minor: number, currency: string = CURRENCY, locale?: string): string {
  const digits = minorDigits(currency)
  const scale = 10 ** digits
  const whole = minor % scale === 0
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: whole ? 0 : digits,
    maximumFractionDigits: digits,
  }).format(minor / scale)
}

export type ParsedAmount = { ok: true; value: number } | { ok: false; error: string }

const AMOUNT_PATTERN = /^(\d[\d,]*)(?:\.(\d+))?$/

/**
 * Parses what a person typed ("1,500", "1500.50", "₹2,000") into minor units.
 * Works on the digits as text, so no floating-point rounding creeps in.
 */
export function parseAmount(
  input: string,
  currency: string = CURRENCY,
  options: { allowZero?: boolean } = {},
): ParsedAmount {
  const text = input.replace(/\s/g, '').replace(/^\p{Sc}/u, '')
  const match = AMOUNT_PATTERN.exec(text)
  if (!match) return { ok: false, error: 'Enter an amount, like 1500.' }
  const digits = minorDigits(currency)
  const fraction = match[2] ?? ''
  if (fraction.length > digits) {
    return {
      ok: false,
      error: digits === 0 ? 'Enter a whole amount.' : `Use at most ${digits} decimal places.`,
    }
  }
  const minorText = match[1].replace(/,/g, '') + fraction.padEnd(digits, '0')
  const value = BigInt(minorText)
  if (value < 0n || (value === 0n && !options.allowZero)) {
    return { ok: false, error: 'Enter an amount above zero.' }
  }
  if (value > BigInt(MAX_AMOUNT)) return { ok: false, error: 'That amount is too large.' }
  return { ok: true, value: Number(value) }
}

/** The currency sign on its own ("₹", "$"), for input prefixes. */
export function currencySymbol(currency: string = CURRENCY, locale?: string): string {
  const part = new Intl.NumberFormat(locale, { style: 'currency', currency })
    .formatToParts(0)
    .find((p) => p.type === 'currency')
  return part?.value ?? currency
}

/** A friendly bid step: about 2.5% of the price, rounded up to 1, 2 or 5 units. */
export function niceStep(minor: number, digits: number = minorDigits()): number {
  const target = minor * 0.025
  let step = 10 ** digits // never below one major unit
  for (let magnitude = step; ; magnitude *= 10) {
    for (const factor of [1, 2, 5]) {
      step = factor * magnitude
      if (step >= target) return step
    }
  }
}

/**
 * The amount offered by the one-tap bid button. Before any bid it steps up from the
 * start price, which satisfies the server whether the first bid must equal or beat it.
 */
export function nextBid(
  highAmount: number | null,
  startPrice: number,
  digits: number = minorDigits(),
): number {
  const base = highAmount ?? startPrice
  return Math.min(base + niceStep(base, digits), MAX_AMOUNT)
}
