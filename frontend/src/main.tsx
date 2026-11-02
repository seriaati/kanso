import './styles/fonts.css'
import './styles/tokens.css'
import './styles/base.css'

import fullFonts from './styles/fonts-full.css?url'

import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { queryClient } from './app/api'
import { App } from './app/App'
import './app/update'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)

// Link the full font subsets once the first frame is painted (rAF runs before the paint, the
// timeout after it). A script-inserted stylesheet never blocks rendering.
requestAnimationFrame(() =>
  setTimeout(() => document.head.append(Object.assign(document.createElement('link'), { rel: 'stylesheet', href: fullFonts }))),
)
