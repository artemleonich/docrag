import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Бэкенд FastAPI слушает :8000. Проксируем запросы API, чтобы фронт ходил на тот же ориджин.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1", // форсируем IPv4 (иначе Vite биндит только ::1 и браузер не подключается)
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
