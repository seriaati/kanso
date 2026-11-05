import fullFonts from '../styles/fonts-full.css?url'

let isLinked = false

// Link the full font subsets after the first paint (rAF runs before it, the timeout after).
// A script-inserted stylesheet never blocks rendering.
requestAnimationFrame(() =>
  setTimeout(() => {
    const link = Object.assign(document.createElement('link'), { rel: 'stylesheet', href: fullFonts })
    link.addEventListener('load', () => (isLinked = true))
    document.head.append(link)
  }),
)

type Run ={ text: string; lang?: string | null; bold?: boolean }

// The UI font stack for a language, as tokens.css orders it.
function stack(lang: string | null | undefined) {
  if (!lang?.toLowerCase().startsWith('zh')) return ['Zen Maru Gothic', 'Noto Sans SC', 'Noto Sans TC']
  return [/^zh-(hant|tw|hk)\b/i.test(lang) ? 'Noto Sans TC' : 'Noto Sans SC', 'Zen Maru Gothic']
}

type Face = { face: FontFace; ranges: [number, number][] }
let faces: Map<string, Face[]> | undefined // "family weight" → its subsets

function facesOf(family: string, weight: number) {
  if (!faces) {
    const map = new Map<string, Face[]>()
    document.fonts.forEach((face) => {
      const ranges = face.unicodeRange.split(',').map((range): [number, number] => {
        const [start, end = start] = range.trim().slice(2).split('-')
        return [parseInt(start, 16), parseInt(end, 16)]
      })
      const key = `${face.family.replace(/["']/g, '')} ${face.weight}`
      map.set(key, [...(map.get(key) ?? []), { face, ranges }])
    })
    faces = map
  }
  return faces.get(`${family} ${weight}`) ?? []
}

/** Loads every font subset the text needs, so it's in before the text shows: text drawn while its
    subset is still loading blanks, in Firefox, all text set in the same font for a frame, so new
    rows flickered the whole page. Resolves when they're in, after a second at most, or at once
    while the full fonts aren't linked yet (the first page doesn't wait for those). */
export function preloadGlyphs(runs: Run[]): Promise<unknown> {
  if (!isLinked) return Promise.resolve()
  const chars = new Map<string, Set<string>>() // "lang weight" → the characters set in it
  for (const { text, lang, bold } of runs) {
    const key = `${lang ?? ''} ${bold ? 700 : 400}`
    // The space too: the subset covering it sets the line's height, so it loads even for text without Latin.
    const set = chars.get(key) ?? new Set(' ')
    for (const char of text) set.add(char)
    chars.set(key, set)
  }
  // Each character from the first family in the stack that has it, as the browser picks; the last
  // matching subset wins, as in CSS.
  const needed = new Set<FontFace>()
  for (const [key, set] of chars) {
    const [lang, weight] = key.split(' ')
    const families = stack(lang).map((family) => facesOf(family, Number(weight)))
    for (const char of set) {
      const code = char.codePointAt(0)!
      for (const subsets of families) {
        const match = subsets.findLast(({ ranges }) => ranges.some(([start, end]) => code >= start && code <= end))
        if (match) {
          needed.add(match.face)
          break
        }
      }
    }
  }
  const loaded = Promise.all([...needed].map((face) => face.load())).catch(() => undefined)
  return Promise.race([loaded, new Promise((resolve) => setTimeout(resolve, 1000))])
}
