"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Check, Pencil, X } from "lucide-react";
import { api, type Detail, type QueueRow } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Verdict } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Loading, TLDR } from "@/components/tldr";
import { cn } from "@/lib/utils";

const STATUSES = ["pending", "edit", "approved", "killed", "rejected"] as const;
const ASPECT: Record<string, string> = { "9:16": "aspect-[9/16]", "4:5": "aspect-[4/5]", "1:1": "aspect-square" };

function Queue() {
  const params = useSearchParams();
  const [status, setStatus] = useState<string>("pending");
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [sel, setSel] = useState<string | null>(params.get("id"));
  const [detail, setDetail] = useState<Detail | null>(null);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const notesRef = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(() => {
    api.queue(status).then((r) => {
      setRows(r);
      setSel((cur) => (cur && r.some((x) => x.id === cur) ? cur : r[0]?.id ?? null));
    }).catch((e: Error) => setError(e.message));
  }, [status]);
  useEffect(load, [load]);
  useEffect(() => {
    setDetail(null);
    setNotes("");
    if (sel) api.detail(sel).then(setDetail).catch((e: Error) => setError(e.message));
  }, [sel]);

  const decide = useCallback(async (d: "approve" | "edit" | "kill") => {
    if (!sel || busy) return;
    if (d === "edit" && !notes.trim()) {
      notesRef.current?.focus();
      setError("Write what to change, then press Edit again.");
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await api.decide(sel, d, notes);
      const i = rows?.findIndex((r) => r.id === sel) ?? -1;
      const next = rows?.[i + 1]?.id ?? rows?.[i - 1]?.id ?? null;
      setRows((r) => r?.filter((x) => x.id !== sel) ?? null);
      setSel(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [sel, busy, notes, rows]);

  // keyboard: A approve, E edit, K kill, J/↓ next, ↑ previous (not while typing notes)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "TEXTAREA") return;
      const i = rows?.findIndex((r) => r.id === sel) ?? -1;
      if (e.key === "a") decide("approve");
      else if (e.key === "e") decide("edit");
      else if (e.key === "k") decide("kill");
      else if ((e.key === "j" || e.key === "ArrowDown") && rows?.[i + 1]) setSel(rows[i + 1].id);
      else if (e.key === "ArrowUp" && rows?.[i - 1]) setSel(rows[i - 1].id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, sel, decide]);

  if (!rows) return <Loading error={error} />;
  const d = detail;
  const reasons = d?.compliance?.checks.flatMap((c) => c.reasons) ?? [];
  return (
    <>
      <TLDR
        lines={[
          `${rows.length} ads ${status}`,
          "A approve · E edit (with notes) · K kill · J next",
          "approved ads go to `adlab export`: you upload them, with the AI label on",
        ]}
      />
      <div className="mb-4 flex flex-wrap gap-2">
        {STATUSES.map((s) => (
          <Button key={s} size="sm" variant={s === status ? "default" : "outline"} onClick={() => setStatus(s)}>
            {s}
          </Button>
        ))}
      </div>
      {error && <p className="mb-3 text-sm text-reject">{error}</p>}
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.id}>
              <button
                onClick={() => setSel(r.id)}
                className={cn("w-full rounded-md border p-3 text-left text-sm hover:bg-secondary", r.id === sel && "border-foreground")}
              >
                <div className="mb-1 flex items-center gap-2 text-xs">
                  <Verdict v={r.verdict} />
                  <span className="text-muted-foreground">{r.site} · {r.ratio}</span>
                </div>
                <p className="line-clamp-2">{r.script}</p>
              </button>
            </li>
          ))}
          {rows.length === 0 && <li className="text-sm text-muted-foreground">Nothing {status}.</li>}
        </ul>

        {sel && !d && <Loading />}
        {d && (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,360px)_1fr]">
            <div className="space-y-3">
              <video
                key={d.creative.id}
                src={api.videoUrl(d.creative.id)}
                controls
                playsInline
                className={cn("w-full rounded-lg bg-black", ASPECT[d.creative.aspect_ratio])}
              />
              <p className="text-xs text-muted-foreground">{d.creative.ai_disclosure}</p>
              {d.status === "pending" || d.status === "edit" ? (
                <div className="space-y-2">
                  <Textarea ref={notesRef} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes (required for Edit)" />
                  <div className="grid grid-cols-3 gap-2">
                    <Button variant="pass" onClick={() => decide("approve")} disabled={busy || d.compliance?.verdict === "REJECT"}>
                      <Check className="h-4 w-4" /> Approve
                    </Button>
                    <Button variant="outline" onClick={() => decide("edit")} disabled={busy}>
                      <Pencil className="h-4 w-4" /> Edit
                    </Button>
                    <Button variant="destructive" onClick={() => decide("kill")} disabled={busy}>
                      <X className="h-4 w-4" /> Kill
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="text-sm">
                  Decision: <b>{d.status}</b> {d.decision?.notes && `· ${d.decision.notes}`}
                </p>
              )}
            </div>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    Compliance <Verdict v={d.compliance?.verdict} />
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 text-sm">
                  {reasons.length === 0 ? (
                    <p className="text-muted-foreground">All 5 checks passed: fact trace, similarity, banned content, platform policy, visuals.</p>
                  ) : (
                    reasons.map((r) => (
                      <p key={r} className={r.startsWith("[REJECT]") ? "text-reject" : "text-fix"}>{r}</p>
                    ))
                  )}
                </CardContent>
              </Card>

              {d.brief && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">The ad</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <p className="text-muted-foreground">
                      {d.brief.hook.type} · {d.brief.angle} · pattern: {d.pattern?.name ?? "?"}
                      {d.pattern?.own_performance_score != null && ` (yours: ${d.pattern.own_performance_score})`}
                    </p>
                    <p className="font-medium">&ldquo;{d.brief.script}&rdquo;</p>
                    <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                      {d.brief.shots.map((s) => (
                        <li key={s.index}>
                          {s.seconds}s · {s.visual}
                          {s.on_screen_text && <> · <span className="text-foreground">{s.on_screen_text}</span></>}
                          {s.vo_line && <> · VO: {s.vo_line}</>}
                        </li>
                      ))}
                    </ol>
                    <p>CTA: {d.brief.cta}</p>
                    <div className="flex flex-wrap gap-2">
                      {d.brief.claims.map((c) => (
                        <Badge key={c.fact_key} variant="outline" title={c.text}>fact: {c.fact_key}</Badge>
                      ))}
                    </div>
                    {d.brief.missing_facts.length > 0 && (
                      <p className="text-fix">Wanted but missing from facts.yaml: {d.brief.missing_facts.join(", ")}</p>
                    )}
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Learned from (structure only)</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  {d.learned_from.map((l) => (
                    <div key={l.ad_id} className="rounded-md border p-3">
                      <p className="font-medium">
                        {l.advertiser} <span className="text-xs text-muted-foreground">· {l.platform} · score {l.score ?? "?"}</span>
                      </p>
                      {l.teardown && (
                        <>
                          <p className="text-muted-foreground">
                            hook: {l.teardown.hook.type} · beats: {l.teardown.beats.join(" → ")} · {l.teardown.format}
                            {l.teardown.pacing.cuts_per_10s > 0 && ` · ${l.teardown.pacing.cuts_per_10s} cuts/10s`}
                          </p>
                          <p className="mt-1">{l.teardown.why_it_works}</p>
                        </>
                      )}
                    </div>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Before you upload</CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                    {d.manual_checks.map((m) => <li key={m}>{m}</li>)}
                  </ul>
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Queue />
    </Suspense>
  );
}
