import { NextResponse, NextRequest } from "next/server";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow login page, static assets, and favicon
  if (
    pathname === "/login" ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon")
  ) {
    return NextResponse.next();
  }

  // Check for auth cookie
  const authCookie = request.cookies.get("alphamolt_auth")?.value;
  const sitePassword = process.env.SITE_PASSWORD;

  // If no password is configured, allow access (dev mode)
  if (!sitePassword) {
    return NextResponse.next();
  }

  // Validate cookie
  if (authCookie !== sitePassword) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
