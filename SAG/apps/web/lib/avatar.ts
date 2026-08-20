export const MAX_AVATAR_CHARS = 8;

/** Trim and cap a text avatar by Unicode code points instead of UTF-16 code units. */
export function normalizeAvatar(value: string, limit = MAX_AVATAR_CHARS) {
  return Array.from(value.trim()).slice(0, limit).join("");
}
