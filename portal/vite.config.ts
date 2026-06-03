import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

// Vite build → portal/dist (deployed to Azure Static Web Apps), plus the
// vitest config for the pure render/format helper tests (no DOM needed).
// Multi-page: the public ticket portal (index.html) and the gated tenant admin
// page (admin.html, CM-56) build as separate entry points.
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        admin: resolve(__dirname, "admin.html"),
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
