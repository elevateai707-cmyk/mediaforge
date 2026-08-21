import { NextRequest, NextResponse } from "next/server";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (!pathname.startsWith("/command") && !pathname.startsWith("/api/agents")) {
    return NextResponse.next();
  }

  const secret = process.env.COMMAND_SECRET;
  if (!secret) {
    if (process.env.NODE_ENV !== "production") {
      return NextResponse.next();
    }
    return new NextResponse("Command is closed.", { status: 404 });
  }

  const offered =
    request.headers.get("x-command-secret") ??
    request.nextUrl.searchParams.get("k") ??
    request.cookies.get("command_secret")?.value;

  if (offered !== secret) {
    return new NextResponse("Command requires COMMAND_SECRET.", { status: 401 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/command/:path*", "/api/agents/:path*"],
};
