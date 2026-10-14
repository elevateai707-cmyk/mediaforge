"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Fish, Inbox, TrendingUp } from "lucide-react";
import { api, type Home } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Loading, TLDR } from "@/components/tldr";
import { money, pct } from "@/lib/utils";

// Home is exactly three things: what to review, what's new, what's converting.
export default function HomePage() {
  const [data, setData] = useState<Home | null>(null);
  const [error, setError] = useState<string>();
  useEffect(() => {
    api.home().then(setData).catch((e: Error) => setError(e.message));
  }, []);
  if (!data) return <Loading error={error} />;
  return (
    <>
      <TLDR lines={data.tldr} />
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Inbox className="h-5 w-5" /> To review</CardTitle>
            <CardDescription>{data.to_review.count} ads waiting on you</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.to_review.top.map((a) => (
              <Link key={a.id} href={`/review?id=${a.id}`} className="block rounded-md border p-3 hover:bg-secondary">
                <div className="mb-1 flex items-center gap-2 text-xs">
                  <Badge variant={a.compliance === "PASS" ? "PASS" : a.compliance === "FIX" ? "FIX" : "default"}>{a.compliance}</Badge>
                  <span className="text-muted-foreground">{a.site} · {a.hook} · {a.ratio}</span>
                </div>
                <p className="line-clamp-2 text-sm">{a.script}</p>
              </Link>
            ))}
            {data.to_review.count === 0 && <p className="text-sm text-muted-foreground">Queue is empty. Run <code>adlab produce</code>.</p>}
            <Link href="/review" className="inline-flex items-center gap-1 text-sm font-medium">
              Open queue <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Fish className="h-5 w-5" /> New hooks</CardTitle>
            <CardDescription>From this week&apos;s winning ads (structure only)</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.hooks.map((h, i) => (
              <div key={i} className="text-sm">
                <div className="flex items-center gap-2">
                  <Badge>{h.type}</Badge>
                  <span className="text-xs text-muted-foreground">{h.advertiser} · {h.score}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-muted-foreground">{h.why}</p>
              </div>
            ))}
            {data.hooks.length === 0 && <p className="text-sm text-muted-foreground">No new teardowns this week.</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" /> What&apos;s converting</CardTitle>
            <CardDescription>Your results, by pattern</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.converting.map((p) => (
              <div key={p.key} className="text-sm">
                <p className="font-medium">{p.label}</p>
                <p className="text-muted-foreground">CPL {money(p.cpl)} · CTR {pct(p.ctr)} · {p.leads} leads</p>
              </div>
            ))}
            {data.converting.length === 0 && (
              <p className="text-sm text-muted-foreground">No results yet. Import them with <code>adlab report --import</code>.</p>
            )}
            <Link href="/results" className="inline-flex items-center gap-1 text-sm font-medium">
              All results <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
