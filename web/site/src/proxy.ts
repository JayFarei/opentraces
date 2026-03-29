import { NextRequest, NextResponse } from "next/server";

const SUBDOMAIN_ROUTES: Record<string, string> = {
  docs: "/docs",
  explorer: "/explorer",
  schema: "/schema",
};

// Domains where we should check for subdomains
const PRODUCTION_DOMAINS = ["opentraces.ai"];

export function proxy(request: NextRequest) {
  const hostname = request.headers.get("host") || "";

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
  matcher: ["/((?!_next|favicon.ico|icon.svg|llms.txt|.*\\.png$).*)"],
};
