import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

// Tests run inside the real Cloudflare Workers runtime (workerd via miniflare),
// so the projection code is exercised under the same runtime it deploys to — but
// every test injects a fixture `fetcher`, so the suite is green in CI with NO
// deployed infrastructure and NO network access.
export default defineWorkersConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.jsonc" },
      },
    },
  },
});
