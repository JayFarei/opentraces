import { NextResponse } from "next/server";
import { getGithubStars } from "@/lib/homepage-metrics";

// Same-origin GitHub-star count for the nav badge. The homepage gets the count
// server-side and passes it to <Nav stars=…>; every other page (including the
// client-rendered ones) reads it from here so the badge — and therefore the
// menu bar width — is identical across the whole site.
export const runtime = "nodejs";
// Cache for an hour; star counts move slowly and this keeps it off the hot path.
export const revalidate = 3600;

export async function GET() {
  let stars = "—";
  try {
    stars = await getGithubStars();
  } catch {
    /* fall back to the placeholder; never fail the nav */
  }
  return NextResponse.json(
    { stars },
    { headers: { "Cache-Control": "public, max-age=300, s-maxage=3600" } },
  );
}
