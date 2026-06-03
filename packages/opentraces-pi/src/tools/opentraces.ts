import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

export type JsonObject = Record<string, unknown>;

export const INTERNAL_ENV = "OPENTRACES_PI_EXTENSION_INTERNAL";
export const MAX_TEXT_CHARS = 50_000;

export function truncateText(value: string, limit = MAX_TEXT_CHARS): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n...[truncated ${value.length - limit} chars by opentraces-pi]`;
}

export function asDetails(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : { value };
}

export async function runOpenTraces(
  pi: ExtensionAPI,
  args: string[],
  ctx?: ExtensionContext,
  options: { timeout?: number; parseJson?: boolean } = {},
): Promise<{ ok: boolean; code: number; text: string; details: JsonObject }> {
  const timeout = options.timeout ?? 20_000;
  const parseJson = options.parseJson ?? true;
  const env = { ...(process.env ?? {}), [INTERNAL_ENV]: "1" };
  const candidates = ["opentraces", "ot"];
  let last: any = undefined;
  for (const command of candidates) {
    try {
      const result: any = await (pi as any).exec(command, args, {
        signal: ctx?.signal,
        timeout,
        cwd: ctx?.cwd,
        env,
      });
      last = result;
      const code = Number(result?.code ?? result?.exitCode ?? 0);
      const stdout = String(result?.stdout ?? "");
      const stderr = String(result?.stderr ?? "");
      const text = stdout || stderr;
      if (code !== 0 && /not found|ENOENT/i.test(stderr)) continue;
      let details: JsonObject = { stdout: truncateText(stdout), stderr: truncateText(stderr), argv: args, code };
      if (parseJson && stdout.trim()) {
        try {
          details = asDetails(JSON.parse(extractJson(stdout)));
        } catch (error) {
          details = { ...details, parse_error: String(error) };
        }
      }
      return { ok: code === 0, code, text: truncateText(text), details };
    } catch (error) {
      last = error;
    }
  }
  return {
    ok: false,
    code: 127,
    text: "OpenTraces CLI was not found. Run /ot-setup or install with `pipx install opentraces`.",
    details: {
      status: "missing_cli",
      install_hints: ["pipx install opentraces", "uv tool install opentraces", "pip install opentraces"],
      last_error: String(last ?? "not found"),
    },
  };
}

export function extractJson(stdout: string): string {
  const sentinel = "---OPENTRACES_JSON---";
  const idx = stdout.lastIndexOf(sentinel);
  if (idx >= 0) return stdout.slice(idx + sentinel.length).trim();
  return stdout.trim();
}

export function textResult(title: string, result: { ok: boolean; code: number; text: string; details: JsonObject }) {
  const prefix = result.ok ? title : `${title} failed (exit ${result.code})`;
  const body = result.text || JSON.stringify(result.details, null, 2);
  return {
    content: [{ type: "text", text: truncateText(`${prefix}\n\n${body}`) }],
    details: result.details,
    isError: !result.ok,
  };
}
