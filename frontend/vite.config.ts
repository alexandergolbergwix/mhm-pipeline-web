import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, Vite proxies /api → http://localhost:8000 so the cookie-based
// session works against the FastAPI backend without CORS friction. In
// production, FastAPI serves both the API and the built frontend from
// the same origin, so no proxy needed.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
