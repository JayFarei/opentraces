import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const args = process.argv.slice(2);
const localConfigPath = resolve("wrangler.local.jsonc");

const wranglerArgs = [
  "wrangler",
  ...(existsSync(localConfigPath) ? ["--config", localConfigPath] : []),
  ...args,
];

const command = process.platform === "win32" ? "npx.cmd" : "npx";
const child = spawn(command, wranglerArgs, {
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }

  process.exit(code ?? 1);
});
