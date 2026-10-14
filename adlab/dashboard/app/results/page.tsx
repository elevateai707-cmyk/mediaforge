"use client";

import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api, type ExportResult, type Perf, type Report } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loading, TLDR } from "@/components/tldr";
import { money, pct } from "@/lib/utils";

function Table({ title, rows }: { title: string; rows: Perf[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-3">Name</th><th className="pr-3">CTR</th><th className="pr-3">CPC</th>
              <th className="pr-3">CPL</th><th className="pr-3">Leads</th><th>Spend</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t">
                <td className="py-1.5 pr-3">{r.label}</td><td className="pr-3">{pct(r.ctr)}</td>
                <td className="pr-3">{money(r.cpc)}</td><td className="pr-3 font-medium">{money(r.cpl)}</td>
                <td className="pr-3">{r.leads}</td><td>{money(r.spend)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={6} className="py-2 text-muted-foreground">No results yet.</td></tr>
            )}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

export default function ResultsPage() {
  const [rep, setRep] = useState<Report | null>(null);
  const [error, setError] = useState<string>();
  const [exp, setExp] = useState<ExportResult | null>(null);
  useEffect(() => {
    api.report(28).then(setRep).catch((e: Error) => setError(e.message));
  }, []);
  if (!rep) return <Loading error={error} />;
  const t = rep.total;
  const best = rep.by_pattern[0];
  return (
    <>
      <TLDR
        lines={[
          `Last 28 days: ${money(t.spend)} spent · ${t.leads} leads · CPL ${money(t.cpl)} · CTR ${pct(t.ctr)}`,
          best ? `Best pattern: ${best.label} (CPL ${money(best.cpl)})` : "Import results: adlab report --import <csv>",
          "Patterns are re-ranked on these numbers, so new briefs favour what converts for you",
        ]}
      />
      <div className="mb-4 flex items-center gap-3">
        <Button onClick={() => api.export().then(setExp).catch((e: Error) => setError(e.message))}>
          <Download className="h-4 w-4" /> Export approved ads
        </Button>
        {exp && (
          <span className="text-sm text-muted-foreground">
            {Object.values(exp.files).flat().length} files → {exp.folder ?? "nothing approved yet"}
          </span>
        )}
      </div>
      {exp?.skipped.map((s) => <p key={s} className="text-sm text-fix">{s}</p>)}
      <div className="grid gap-4">
        <Table title="By pattern" rows={rep.by_pattern} />
        <Table title="By hook type" rows={rep.by_hook} />
        <Table title="By ad" rows={rep.by_creative} />
      </div>
    </>
  );
}
