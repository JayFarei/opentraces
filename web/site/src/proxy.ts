import { NextRequest, NextResponse } from "next/server";

const SUBDOMAIN_ROUTES: Record<string, string> = {
  docs: "/docs",
  explorer: "/explorer",
  schema: "/schema",
};

// Domains where we should check for subdomains
const PRODUCTION_DOMAINS = ["opentraces.ai"];

function prefersMarkdown(accept: string): boolean {
  if (!accept) return false;
  const items = accept.split(",").map((s) => s.trim().toLowerCase());
  let mdQ = -1;
  let htmlQ = -1;
  for (const item of items) {
    const [type, ...params] = item.split(";").map((s) => s.trim());
    const q = Number(
      params.find((p) => p.startsWith("q="))?.slice(2) ?? "1",
    );
    if (type === "text/markdown" && q > mdQ) mdQ = q;
    if ((type === "text/html" || type === "*/*") && q > htmlQ) htmlQ = q;
  }
  return mdQ >= 0 && mdQ >= htmlQ;
}

function markdownRewrite(request: NextRequest): NextResponse | null {
  const accept = request.headers.get("accept") || "";
  if (!prefersMarkdown(accept)) return null;

  const pathname = request.nextUrl.pathname;
  const url = request.nextUrl.clone();

  if (pathname === "/") {
    url.pathname = "/llms.txt";
  } else if (pathname === "/docs" || pathname.startsWith("/docs/")) {
    url.pathname = pathname.replace(/^\/docs/, "/raw-docs");
  } else {
    return null;
  }

  const response = NextResponse.rewrite(url);
  response.headers.set("Content-Type", "text/markdown; charset=utf-8");
  response.headers.set("Vary", "Accept");
  return response;
}

export function proxy(request: NextRequest) {
  const hostname = request.headers.get("host") || "";

  const md = markdownRewrite(request);
  if (md) return md;

  // Only apply subdomain routing on production domains
  const isProductionDomain = PRODUCTION_DOMAINS.some((d) => hostname.endsWith(d));
  if (!isProductionDomain) return NextResponse.next();

  const parts = hostname.split(".");
  // e.g. "docs.opentraces.ai" -> subdomain = "docs"
  if (parts.length === 3) {
    const subdomain = parts[0];
    const targetPath = SUBDOMAIN_ROUTES[subdomain];
    const url = request.nextUrl.clone();

    if (targetPath && !url.pathname.startsWith(targetPath)) {
      url.pathname = `${targetPath}${url.pathname === "/" ? "" : url.pathname}`;
      return NextResponse.rewrite(url);
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next|favicon.ico|icon.svg|llms.txt|robots.txt|\\.well-known|.*\\.png$).*)"],
};
