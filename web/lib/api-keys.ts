/**
 * API key generation and hashing.
 *
 * Format: `ak_live_<32 random base32 chars>`
 *
 * We store SHA-256 of the plaintext key in `agents.api_key_hash` and a
 * display prefix (first 12 chars) in `agents.api_key_prefix`. The plaintext
 * is returned to the user exactly once at creation and never retrievable again.
 */

import { createHash, randomBytes } from "node:crypto";

const KEY_PREFIX = "ak_live_";
const KEY_RANDOM_BYTES = 20; // → 32 base32 chars
const BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";

function toBase32(bytes: Buffer): string {
  let bits = 0;
  let value = 0;
  let output = "";
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      output += BASE32_ALPHABET[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) {
    output += BASE32_ALPHABET[(value << (5 - bits)) & 31];
  }
  return output;
}

export interface GeneratedKey {
  plaintext: string;
  hash: string;
  prefix: string;
}

export function generateApiKey(): GeneratedKey {
  const random = randomBytes(KEY_RANDOM_BYTES);
  const plaintext = KEY_PREFIX + toBase32(random).slice(0, 32);
  return {
    plaintext,
    hash: hashApiKey(plaintext),
    prefix: plaintext.slice(0, 12), // "ak_live_xxxx"
  };
}

export function hashApiKey(plaintext: string): string {
  return createHash("sha256").update(plaintext).digest("hex");
}
