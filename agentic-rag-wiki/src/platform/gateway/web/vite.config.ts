import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// dev 时把 /api 代理到本地 HTTP 门面（python -m src.platform.gateway.http，默认 127.0.0.1:8000）。
// build 产物落 dist/，由门面同源挂载（http.py: web/dist），届时无需代理、直接同源 /api。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
