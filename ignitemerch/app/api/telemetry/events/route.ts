import { NextRequest, NextResponse } from "next/server";
import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/prisma";
import { readSessionId } from "@/lib/session";

export async function POST(request: NextRequest) {
  const body = (await request.json()) as {
    eventType?: string;
    metadata?: Prisma.InputJsonValue;
  };
  if (!body.eventType) {
    return NextResponse.json({ error: "eventType required" }, { status: 400 });
  }
  const sessionId = (await readSessionId()) ?? "anon";
  await prisma.analyticsEvent.create({
    data: {
      eventType: body.eventType,
      sessionId,
      metadata: body.metadata ?? {},
    },
  });
  return NextResponse.json({ ok: true });
}
