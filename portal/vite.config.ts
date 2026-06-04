import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

// Vite build → portal/dist (deployed to Azure Static Web Apps), plus the
// vitest config for the pure render/format helper tests (no DOM needed).
// Multi-page: the public ticket portal (index.html), the gated tenant admin
// page (admin.html, CM-56), and the TEST-ONLY web chat harness (test-chat.html,
// CM-55) build as separate entry points.
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        admin: resolve(__dirname, "admin.html"),
        testChat: resolve(__dirname, "test-chat.html"),
      },
    },
  },
  // Dev only: proxy the TEST-ONLY web-chat API (the agents.webchat FastAPI app,
  // run on :8000) so the SPA can call same-origin /web/* during `npm run dev`.
  server: {
    proxy: {
      "/web": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
