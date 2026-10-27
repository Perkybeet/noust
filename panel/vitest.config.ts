import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.mjs"],
    // Stylesheets are not applied in jsdom, but tokens.css, fonts.css and app.css are read as
    // text by the tokens test (contrast, font stacks and their fallback faces, the utilities
    // app.css exposes for each token).
    css: { include: [/tokens\.css/, /fonts\.css/, /app\.css/] },
    restoreMocks: true,
    // A test's timeout catches a hang; it is not what a test may cost. The ones that render a
    // whole page and walk it with a user (a wizard, a settings page, axe over it) take one to two
    // seconds on a CI runner and three times that on a loaded machine, and 685 of them already
    // asked for twenty seconds one by one. Under a CPU three times slower than CI's the only two
    // tests of 5,864 that failed were two that had not asked (the environment tab removing six
    // rows, at five seconds). What a test waits for stays as short as it was: this does not touch
    // findBy, waitFor or any wait inside a test.
    testTimeout: 20_000,
  },
});
