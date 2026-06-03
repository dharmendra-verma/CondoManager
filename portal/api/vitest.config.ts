import { defineConfig } from "vitest/config";

// Self-contained config so `vitest` uses portal/api as its root rather than
// climbing to the SPA's portal/vite.config.ts. Pure handler/repo/model tests
// run in node (no DOM needed) — mirrors the test block in portal/vite.config.ts.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
