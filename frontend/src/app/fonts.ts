import fullFonts from '../styles/fonts-full.css?url'

// Link the full font subsets after the first paint (rAF runs before it, the timeout after).
// A script-inserted stylesheet never blocks rendering.
const linked = new Promise<void>((resolve) =>
  requestAnimationFrame(() =>
    setTimeout(() => {
      const link = Object.assign(document.createElement('link'), { rel: 'stylesheet', href: fullFonts })
      link.addEventListener('load', () => resolve())
      document.head.append(link)
    }),
  ),
)

export type Run = { text: string; lang?: string | null; bold?: boolean }

// The family a language draws with first, as tokens.css orders them. Only that one: loading the
// whole stack would also fetch the Chinese subsets for every Japanese or Latin character.
function family(lang: string | null | undefined) {
  if (!lang?.toLowerCase().startsWith('zh')) return 'Zen Maru Gothic'
  return /^zh-(hant|tw|hk)\b/i.test(lang) ? 'Noto Sans TC' : 'Noto Sans SC'
}

/** Loads every font subset the text needs in one go. Left to the browser, a subset loads only when
    text needing it is drawn, e.g. as a row scrolls into view, and each one that arrives re-lays out
    the whole page, which shows as text flickering everywhere. */
export function preloadGlyphs(runs: Run[]) {
  const chars = new Map<string, Set<string>>() // font → the characters it draws
  for (const { text, lang, bold } of runs) {
    const font = `${bold ? 700 : 400} 1em '${family(lang)}'`
    const set = chars.get(font) ?? new Set()
    for (const char of text) set.add(char)
    chars.set(font, set)
  }
  void linked.then(() =>
    Promise.all([...chars].map(([font, set]) => document.fonts.load(font, [...set].join('')))).catch(() => undefined),
  )
}
