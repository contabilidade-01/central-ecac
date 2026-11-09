import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build vai para app/static/novo e é servido pelo Flask em /app (desvio 28).
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: { outDir: "../app/static/novo", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:5847", "/logout": "http://127.0.0.1:5847" } },
});
