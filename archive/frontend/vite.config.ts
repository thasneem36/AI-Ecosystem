import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server runs on :3000 and proxies API calls to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
