// Applies the picked theme before the first paint. A file, not an inline script: the CSP forbids those.
try {
  var theme = localStorage.getItem('kanso.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme
} catch {
  // storage blocked: follow the system
}
