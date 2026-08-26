import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// One origin in production: FastAPI serves the built bundle. In development the
// two toolchains run separately, so /api is proxied to keep the browser believing
// there is still only one origin -- which keeps the session cookie working without
// any CORS or SameSite special-casing.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
