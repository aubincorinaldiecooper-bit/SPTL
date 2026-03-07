import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/generated': 'http://127.0.0.1:8000',
    },
  },
});
