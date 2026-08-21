import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { zipKit } from "@/lib/kits";
import { allowRequest } from "@/lib/security/rate-limit";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ token: string }> },
) {
  const { token } = await context.params;
  const ip = _request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "local";
  if (!allowRequest(`dl:${ip}`, 30, 60_000)) {
    return NextResponse.json({ error: "Too many downloads." }, { status: 429 });
  }
  const download = await prisma.download.findUnique({
    where: { token },
    include: { product: true },
  });

  if (!download) {
    return NextResponse.json({ error: "Unknown ticket." }, { status: 404 });
  }
  if (download.expiresAt.getTime() < Date.now()) {
    return NextResponse.json({ error: "Ticket expired." }, { status: 410 });
  }
  if (download.downloadsRemaining <= 0) {
    return NextResponse.json({ error: "No pulls left." }, { status: 429 });
  }

  let archive: Uint8Array;
  try {
    archive = zipKit(download.product.slug);
  } catch {
    return NextResponse.json({ error: "Kit files missing on disk." }, { status: 404 });
  }

  await prisma.download.update({
    where: { id: download.id },
    data: { downloadsRemaining: { decrement: 1 } },
  });

  return new NextResponse(Buffer.from(archive), {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="${download.product.slug}.zip"`,
      "Cache-Control": "no-store",
    },
  });
}
