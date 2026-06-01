/**
 * Hebrew script detection helpers — WCAG 3.1.2 Language of Parts.
 *
 * Mixed Hebrew/English content in the curator UI needs a ``lang``
 * attribute on every Hebrew-bearing text node so screen readers and
 * other assistive tech can switch pronunciation/voice accordingly.
 *
 * Both functions are pure and side-effect free; safe to call inline
 * during render.
 */

// Unicode Hebrew block: U+0590..U+05FF. Covers the consonantal
// letters, niqqud (vowel points), cantillation marks, and the
// punctuation specific to Hebrew (geresh, gershayim, etc.). Built
// from explicit \u escapes so the source stays pure-ASCII regardless
// of editor/transport encoding.
const HEBREW_REGEX = /[֐-׿]/;

/**
 * Returns true when ``text`` contains at least one character in the
 * Unicode Hebrew block. ``null`` / ``undefined`` / empty strings
 * return false.
 */
export function hasHebrew(text: string | null | undefined): boolean {
  if (text === null || text === undefined) return false;
  if (text.length === 0) return false;
  return HEBREW_REGEX.test(text);
}

/**
 * Returns ``"he"`` when ``text`` contains any Hebrew character,
 * otherwise ``undefined``. Intended for inline use as
 * ``<span lang={langOf(value)}>`` so the attribute is simply omitted
 * for non-Hebrew content.
 */
export function langOf(text: string | null | undefined): "he" | undefined {
  return hasHebrew(text) ? "he" : undefined;
}
