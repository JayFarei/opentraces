import { NextRequest, NextResponse } from "next/server";

const VIEWER = "https://datasets-server.huggingface.co";
const README_BASE = "https://huggingface.co/datasets";
const REVALIDATE_SECONDS = 300;

const DATASET_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*$/;

function unavailable(status: number | null) {
  return NextResponse.json(
    { ok: false, status, info: null, text: null },
    { status: 200 },
  );
}

export async function GET(req: NextRequest) {
  const kind = req.nextUrl.searchParams.get("kind");
  const dataset = req.nextUrl.searchParams.get("dataset")?.trim() ?? "";

  if ((kind !== "info" && kind !== "readme") || !DATASET_ID_RE.test(dataset)) {
    return NextResponse.json({ ok: false, error: "invalid request" }, { status: 400 });
  }

  try {
    if (kind === "info") {
      const response = await fetch(`${VIEWER}/info?dataset=${encodeURIComponent(dataset)}`, {
        headers: { accept: "application/json" },
        next: { revalidate: REVALIDATE_SECONDS },
      });
      if (!response.ok) return unavailable(response.status);
      return NextResponse.json({ ok: true, status: response.status, info: await response.json() });
    }

    const response = await fetch(`${README_BASE}/${dataset}/resolve/main/README.md`, {
      next: { revalidate: REVALIDATE_SECONDS },
    });
    if (!response.ok) return unavailable(response.status);
    return NextResponse.json({ ok: true, status: response.status, text: await response.text() });
  } catch {
    return unavailable(null);
  }
}
