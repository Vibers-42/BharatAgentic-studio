// Vite config. In local dev (npm run dev) /api is forwarded to the FastAPI server,
// the same way nginx forwards it inside Docker, so the frontend code never changes.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
