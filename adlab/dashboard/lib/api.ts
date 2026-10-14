// Talks to `adlab serve` (default http://127.0.0.1:8430). Local only: nothing here uploads
// to an ad account. If you set ADLAB_API_TOKEN on the server, set NEXT_PUBLIC_ADLAB_TOKEN
// here too (it ends up in the browser bundle, so only do that on your own machine/LAN).
export const API = process.env.NEXT_PUBLIC_ADLAB_API ?? "http://127.0.0.1:8430";
const TOKEN = process.env.NEXT_PUBLIC_ADLAB_TOKEN ?? "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export type Verdict = "PASS" | "FIX" | "REJECT";
export type Perf = {
  key: string; label: string; impressions: number; clicks: number; spend: number;
  leads: number; conversions: number; creatives: number; ctr: number | null; cpc: number | null; cpl: number | null;
};
export type Hook = { type: string; visual: string; why: string; advertiser: string; site: string; score: number };
export type QueueRow = {
  id: string; status: string; ratio: string; site: string | null; hook: string | null; script: string;
  pattern: string | null; verdict: Verdict | null; rank: number;
};
export type Home = {
  tldr: string[];
  to_review: { count: number; top: { id: string; ratio: string; site: string; hook: string; script: string; pattern: string; compliance: string }[] };
  hooks: Hook[];
  converting: Perf[];
};
export type Check = { name: string; passed: boolean; reasons: string[] };
export type Teardown = {
  hook: { type: string; seconds: number; visual: string; on_screen_text: string };
  angles: string[]; format: string; beats: string[]; why_it_works: string;
  pacing: { length_s: number; cuts_per_10s: number; music_energy: string };
};
export type Detail = {
  creative: { id: string; brief_id: string; aspect_ratio: string; path: string; ai_disclosure: string; facts_used: string[] };
  status: string;
  brief: {
    id: string; site: string; hook: { type: string }; angle: string; script: string; cta: string;
    claims: { text: string; fact_key: string }[]; missing_facts: string[];
    shots: { index: number; seconds: number; visual: string; on_screen_text: string; vo_line: string }[];
  } | null;
  pattern: { name: string; own_performance_score: number | null } | null;
  compliance: { verdict: Verdict; checks: Check[] } | null;
  learned_from: { ad_id: string; advertiser: string; platform: string; url: string | null; score: number | null; teardown: Teardown | null }[];
  decision: { decision: string; notes: string } | null;
  manual_checks: string[];
};
export type Report = { total: Perf; by_pattern: Perf[]; by_hook: Perf[]; by_creative: Perf[] };
export type ExportResult = { folder: string | null; files: Record<string, string[]>; sheets: Record<string, string>; skipped: string[] };

export const api = {
  home: () => req<Home>("/api/home"),
  queue: (status = "pending") => req<QueueRow[]>(`/api/queue?status=${encodeURIComponent(status)}`),
  detail: (id: string) => req<Detail>(`/api/creatives/${id}`),
  videoUrl: (id: string) => `${API}/api/creatives/${id}/video${TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : ""}`,
  decide: (id: string, decision: "approve" | "edit" | "kill", notes = "") =>
    req(`/api/creatives/${id}/decision`, { method: "POST", body: JSON.stringify({ decision, notes }) }),
  report: (days = 28) => req<Report>(`/api/report?days=${days}`),
  export: () => req<ExportResult>("/api/export", { method: "POST", body: JSON.stringify({}) }),
};
