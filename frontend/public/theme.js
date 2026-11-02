// Applies the picked theme and accent before the first paint. A file, not an inline script: the CSP forbids those.
try {
  var theme = localStorage.getItem('kanso.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme
  var accent = localStorage.getItem('kanso.accent')
  if (accent) document.documentElement.dataset.accent = accent // an unknown one matches no palette: indigo
} catch {
  // storage blocked: follow the system
}
