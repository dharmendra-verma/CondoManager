import { defineConfig } from "vitest/config";

// Vite build → portal/dist (deployed to Azure Static Web Apps), plus the
// vitest config for the pure render/format helper tests (no DOM needed).
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
