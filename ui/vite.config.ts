import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Base path is "/" by default for local dev and custom deploys. GitHub Pages
// builds set VITE_BASE (e.g. "/gingugu/") via the workflow.
const base = process.env.VITE_BASE ?? '/'

export default defineConfig({
  base,
  plugins: [react()],
  server: {
    port: 5173,
    open: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5174',
        changeOrigin: true,
      },
    },
  },
})
