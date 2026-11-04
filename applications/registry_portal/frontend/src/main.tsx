import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import { startup } from './startup'

const startupPromise = startup(import.meta.env, {
  location: window.location,
  history: window.history,
  sessionStorage: window.sessionStorage,
  fetch: window.fetch.bind(window),
  crypto: window.crypto,
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App startup={startupPromise} />
  </StrictMode>,
)
