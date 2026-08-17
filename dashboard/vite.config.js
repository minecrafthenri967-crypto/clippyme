import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// Content-Security-Policy injected into the PRODUCTION build only. It blocks
// inline/eval'd scripts and foreign script origins, which is the practical
// mitigation for XSS — the main threat to the Gemini key held in localStorage
// (see RedesignApp.jsx). Build-only via `apply: 'build'` because the Vite dev
// server / HMR needs 'unsafe-eval', which we never want shipped. 'unsafe-inline'
// is allowed for styles only (Tailwind v4 / React inline styles), not scripts.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
].join('; ')

const cspPlugin = () => ({
  name: 'clippyme-inject-csp',
  apply: 'build',
  transformIndexHtml() {
    return [{
      tag: 'meta',
      attrs: { 'http-equiv': 'Content-Security-Policy', content: CSP },
      injectTo: 'head-prepend',
    }]
  },
})

export default defineConfig({
  plugins: [tailwindcss(), react(), cspPlugin()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5175,
    strictPort: true,
    hmr: {
      clientPort: 5175,
    },
    // File-watching across the Docker bind mount. On Windows/macOS Docker
    // (WSL2 / gRPC-FUSE) inotify events do NOT propagate from the host into the
    // container, so Vite's default native watcher never sees host edits and HMR
    // silently stops working ("Docker is serving an old version"). Polling is
    // the portable fix. Costs a little CPU; dev-only, never affects `build`.
    watch: {
      usePolling: true,
      interval: 150,
    },
    // Dev-server Host allow-list. Only local names — the unrelated upstream
    // 'openshorts.app' was removed (DNS-rebinding hardening).
    //
    // Reaching the dev dashboard under any OTHER hostname (a Tailscale/VPN
    // name, a reverse-proxy domain) needs that name added here, or Vite
    // answers every request with a bare "This host is not allowed" and the
    // page never loads — which reads as "the server is broken", not as a
    // config choice. CLIPPYME_DEV_ALLOWED_HOSTS (comma-separated) appends to
    // the list from the environment so that no longer means editing this file
    // on the server and carrying a local diff forever. Deliberately additive
    // rather than Vite's `allowedHosts: true`: an explicit extra name keeps
    // the DNS-rebinding guard for every host you did NOT name.
    allowedHosts: [
      'localhost',
      '127.0.0.1',
      ...(process.env.CLIPPYME_DEV_ALLOWED_HOSTS || '')
        .split(',').map((h) => h.trim()).filter(Boolean),
    ],
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
      '/videos': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
      '/thumbnails': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
      '/fonts': {
        target: 'http://backend:8000',
        changeOrigin: true,
      }
    }
  }
})
