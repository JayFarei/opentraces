import type { NextConfig } from "next";
import { readFileSync, writeFileSync } from "fs";
import { resolve } from "path";

// Read version from Python package at config-load time (before bundling)
let version = "0.0.0";
try {
  const initPy = readFileSync(resolve(__dirname, "../../src/opentraces/__init__.py"), "utf-8");
  const match = initPy.match(/__version__\s*=\s*"([^"]+)"/);
  if (match) version = match[1];
} catch {}
writeFileSync(resolve(__dirname, "src/lib/version.json"), JSON.stringify({ version }));

const nextConfig: NextConfig = {
  transpilePackages: ["@opentraces/ui"],
  allowedDevOrigins: ["gabrieles-mac-mini-1.taila1b059.ts.net"],
};

export default nextConfig;
