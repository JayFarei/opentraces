import { NextRequest } from "next/server";
import { getDocContent, DOC_NAV } from "@/lib/docs";

export async function generateStaticParams() {
  return DOC_NAV.map((entry) => ({
    slug: entry.slug ? entry.slug.split("/") : [],
  }));
}

export const dynamic = "force-static";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ slug?: string[] }> },
) {
  const { slug } = await params;
  const slugStr = slug?.join("/") || "";
  const content = await getDocContent(slugStr);
  if (!content) {
    return new Response("Not found", { status: 404 });
  }
  return new Response(content, {
    status: 200,
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "public, max-age=300",
      Vary: "Accept",
    },
  });
}
