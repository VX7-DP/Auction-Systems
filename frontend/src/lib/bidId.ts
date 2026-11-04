type CryptoLike = Pick<Crypto, 'getRandomValues'> & { randomUUID?: () => string }

/**
 * A fresh bid_id (UUID v4). crypto.randomUUID only exists in secure contexts, so a
 * phone opening the dev server over plain HTTP on the LAN falls back to getRandomValues.
 */
export function newBidId(cryptoImpl: CryptoLike = globalThis.crypto): string {
  if (typeof cryptoImpl.randomUUID === 'function') return cryptoImpl.randomUUID()
  const bytes = cryptoImpl.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
