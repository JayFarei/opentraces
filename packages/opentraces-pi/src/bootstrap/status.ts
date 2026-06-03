import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { runOpenTraces } from "../tools/opentraces";

export async function captureStatus(pi: ExtensionAPI, ctx?: ExtensionContext, repair = false) {
  const args = ["setup", "pi", "--dry-run", "--json"];
  const result = await runOpenTraces(pi, args, ctx, { timeout: 15_000, parseJson: true });
  if (!result.ok) return result;
  const details: any = result.details;
  if (repair) {
    details.repair_requested = true;
    details.repair_guidance = [
      "Run `opentraces setup pi` to add the package to Pi settings.",
      "Run `opentraces init --agent pi` in the project to enable capture.",
      "Run `opentraces setup git` in a terminal for commit correlation.",
    ];
  }
  return { ...result, details, text: JSON.stringify(details, null, 2) };
}

export async function maybeShowStartupStatus(pi: ExtensionAPI, ctx: ExtensionContext) {
  const result = await captureStatus(pi, ctx, false);
  if (!result.ok || !ctx.hasUI) return;
  const details: any = result.details;
  const steps = Array.isArray(details.steps) ? details.steps : [];
  const missing = steps.filter((step: any) => step && step.optional !== true && step.state !== "ok");
  if (missing.length > 0) {
    ctx.ui.setStatus("opentraces", `OpenTraces: ${missing.length} setup step(s) missing`);
  } else {
    ctx.ui.setStatus("opentraces", "OpenTraces: capture ready");
  }
}
