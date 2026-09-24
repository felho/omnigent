import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The Databricks embed shares its host's React Router 6.4.1 instance. Keep
// route and navigation tests on that version as well as the standalone version.
export default defineConfig({
  resolve: {
    alias: [
      {
        find: /^react-router-dom$/,
        replacement: fileURLToPath(
          new URL("./node_modules/react-router-dom-host/dist/index.js", import.meta.url),
        ),
      },
      { find: "@", replacement: fileURLToPath(new URL("./src", import.meta.url)) },
    ],
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: [
      "src/App.test.tsx",
      "src/lib/routing.test.tsx",
      "src/managedEmbedRouter.test.tsx",
      "src/managedRouterRoutes.test.ts",
    ],
  },
});
