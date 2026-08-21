import { cookies } from "next/headers";
import { randomUUID } from "crypto";

const SESSION_COOKIE = "ignite_session";

export async function readSessionId() {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

export async function getSessionId() {
  const existing = await readSessionId();
  if (existing) return existing;

  const sessionId = randomUUID();
  const store = await cookies();
  store.set(SESSION_COOKIE, sessionId, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: process.env.NODE_ENV === "production",
    maxAge: 60 * 60 * 24 * 30,
  });
  return sessionId;
}
