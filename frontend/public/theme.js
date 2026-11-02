// Applies the theme picked in the account menu before the first paint (app/theme.ts keeps it in
// sync afterwards). Without a pick, the CSS follows the system's light or dark setting. A plain
// file rather than an inline script: the CSP allows scripts from kanso itself only.
try {
  var theme = localStorage.getItem('kanso.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme
} catch {
  // storage blocked: follow the system
}
