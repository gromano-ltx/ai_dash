import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ["recharts"],
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/collector.py": "http://localhost:8000",
      "/install.sh": "http://localhost:8000",
    },
  },
})
