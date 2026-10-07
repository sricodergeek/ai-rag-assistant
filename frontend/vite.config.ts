import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const apiUrl = (env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/auth': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/documents': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/upload': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/ask': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/speak': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/voice': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/transcribe': {
          target: apiUrl,
          changeOrigin: true,
        },
      },
    },
  }
})
