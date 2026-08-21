import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { answerDeskChat } from "@/lib/chat/desk";
import { allowRequest } from "@/lib/security/rate-limit";

const bodySchema = z.object({
  message: z.string().min(1).max(500),
});

export async function POST(request: NextRequest) {
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "local";
  if (!allowRequest(`chat:${ip}`, 20, 60_000)) {
    return NextResponse.json({ error: "Too many questions. Wait a minute." }, { status: 429 });
  }

  let json: unknown;
  try {
    json = await request.json();
  } catch {
    return NextResponse.json({ error: "Send JSON { message }." }, { status: 400 });
  }

  const parsed = bodySchema.safeParse(json);
  if (!parsed.success) {
    return NextResponse.json({ error: "Message must be 1–500 characters." }, { status: 400 });
  }

  const answer = await answerDeskChat(parsed.data.message);
  return NextResponse.json(answer);
}
