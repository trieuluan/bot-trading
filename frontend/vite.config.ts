import { defineConfig, loadEnv } from 'vite';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', ['API_PROXY_', 'VITE_USE_']);

  return {
    plugins: [tailwindcss(), react()],
    server: {
      proxy: {
        '/api': env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
      },
      watch: {
        usePolling: env.VITE_USE_POLLING === 'true',
      },
    },
  };
});
