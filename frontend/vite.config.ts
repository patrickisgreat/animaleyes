import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built bundle is served by FastAPI; API + MJPEG are same-origin in production.
// For local dev, proxy them to the running container on :8081.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8081",
      "/stream.mjpg": "http://127.0.0.1:8081",
      "/frame.jpg": "http://127.0.0.1:8081",
      "/photos": "http://127.0.0.1:8081",
      "/events": "http://127.0.0.1:8081",
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
