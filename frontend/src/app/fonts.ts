import fullFonts from '../styles/fonts-full.css?url'

let isLinked = false

// Link the full font subsets after the first paint (rAF runs before it, the timeout after).
// A script-inserted stylesheet never blocks rendering.
const linked = new Promise<void>((resolve) =>
  requestAnimationFrame(() =>
    setTimeout(() => {
      const link = Object.assign(document.createElement('link'), { rel: 'stylesheet', href: fullFonts })
      link.addEventListener('load', () => {
        isLinked = true
        resolve()
      })
      document.head.append(link)
    }),
  ),
)

type Run ={ text: string; lang?: string | null; bold?: boolean }

// The family a language draws with first, as tokens.css orders them. Only that one: loading the
// whole stack would also fetch the Chinese subsets for every Japanese or Latin character.
function family(lang: string | null | undefined) {
  if (!lang?.toLowerCase().startsWith('zh')) return 'Zen Maru Gothic'
  return /^zh-(hant|tw|hk)\b/i.test(lang) ? 'Noto Sans TC' : 'Noto Sans SC'
}

/** Loads every font subset the text needs, so it's in before the text shows: text drawn while its
    subset is still loading blanks, in Firefox, all text set in the same font for a frame, so new
    rows flickered the whole page. Resolves when they're in, after a second at most, or at once
    while the full fonts aren't linked yet (the first page doesn't wait for those). */
export function preloadGlyphs(runs: Run[]): Promise<unknown> {
  const chars = new Map<string, Set<string>>() // font → the characters it draws
  for (const { text, lang, bold } of runs) {
    const font = `${bold ? 700 : 400} 1em '${family(lang)}'`
    const set = chars.get(font) ?? new Set()
    for (const char of text) set.add(char)
    chars.set(font, set)
  }
  const loaded = linked
    .then(() => Promise.all([...chars].map(([font, set]) => document.fonts.load(font, [...set].join('')))))
    .catch(() => undefined)
  if (!isLinked) return Promise.resolve()
  return Promise.race([loaded, new Promise((resolve) => setTimeout(resolve, 1000))])
}
