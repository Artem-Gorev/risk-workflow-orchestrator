import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'BACKEND_');
  const backend = {
    target: env.BACKEND_URL || 'http://127.0.0.1:8000',
    changeOrigin: true,
  };
  const proxy = { '/api/v1': backend, '/health/ready': backend };
  return {
    base: './',
    esbuild: { jsx: 'automatic' },
    server: { host: 'localhost', port: 5173, strictPort: true, proxy },
    preview: { host: 'localhost', port: 4173, strictPort: true, proxy },
  };
});
