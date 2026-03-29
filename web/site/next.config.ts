import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  distDir: ".next",
  outputFileTracingRoot: projectRoot,
  transpilePackages: ["@opentraces/ui"],
  turbopack: {
    root: projectRoot,
  },
};

export default nextConfig;
