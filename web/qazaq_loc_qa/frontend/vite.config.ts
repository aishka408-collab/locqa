import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/upload': 'http://localhost:8000',
      '/files': 'http://localhost:8000',
      '/analyze': 'http://localhost:8000',
      '/analysis': 'http://localhost:8000',
      '/glossary': 'http://localhost:8000',
      '/compare': 'http://localhost:8000'
    }
  }
})
