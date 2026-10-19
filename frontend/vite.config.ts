import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Where the Django API is. In docker compose the frontend container reaches it
// by service name (http://web:8000); running `npm run dev` on your own machine,
// it's http://localhost:8000.
const apiTarget = process.env.API_TARGET ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The browser only ever talks to Vite (localhost:5173). Requests to /api
    // are forwarded to Django — so to the browser it's all one site, and no
    // CORS setup is needed.
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
    // In docker the code is mounted from Windows, and file-change events
    // don't cross into the Linux container: poll for changes instead (the
    // same reason the worker uses WATCHFILES_FORCE_POLLING).
    watch: process.env.VITE_USE_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
  },
})
