import { getCommandSnapshot } from "@/lib/catalog";
import { formatPrice } from "@/lib/utils";
import Link from "next/link";

export default async function CommandPage() {
  const snap = await getCommandSnapshot();

  return (
    <main className="mx-auto max-w-[1440px] px-4 py-12 md:px-8">
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
        Command
      </p>
      <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-[0.82] tracking-tight md:text-8xl">
        Press board
      </h1>
      <p className="mt-4 max-w-[54ch] text-ash">
        Every number on this board is queried. If the floor has not sold or an
        agent has not run, you will see an empty row, not a decorative KPI.
      </p>

      <section className="mt-12 border border-bone/10">
        <header className="border-b border-bone/10 px-5 py-3 font-mono text-[11px] uppercase tracking-[0.18em] text-ash">
          Ledger
        </header>
        <dl>
          <LedgerRow
            label="Completed orders"
            value={
              snap.completedOrderCount === 0
                ? "None yet"
                : String(snap.completedOrderCount)
            }
            empty={snap.completedOrderCount === 0}
          />
          <LedgerRow
            label="Completed revenue"
            value={
              snap.completedOrderCount === 0
                ? "No sales"
                : formatPrice(snap.completedRevenue)
            }
            empty={snap.completedOrderCount === 0}
          />
          <LedgerRow
            label="Agent runs"
            value={
              snap.agentRunCount === 0 ? "No runs yet" : String(snap.agentRunCount)
            }
            empty={snap.agentRunCount === 0}
          />
          <LedgerRow
            label="Agent spend"
            value={
              snap.agentRunCount === 0 ? "No spend" : formatPrice(snap.agentSpend)
            }
            empty={snap.agentRunCount === 0}
          />
          <LedgerRow
            label="Pending approvals"
            value={
              snap.pendingApprovals === 0
                ? "Queue is clear"
                : String(snap.pendingApprovals)
            }
            empty={snap.pendingApprovals === 0}
          />
        </dl>
      </section>

      <section className="mt-10">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="font-display text-4xl font-extrabold uppercase">
            Agents
          </h2>
          <Link
            href="/command/agents"
            className="font-mono text-[11px] uppercase tracking-[0.16em] text-ash"
          >
            Manage
          </Link>
        </div>
        {snap.agents.length === 0 ? (
          <p className="border border-dashed border-bone/15 px-5 py-8 text-ash">
            No agents registered.
          </p>
        ) : (
          <table className="w-full text-left">
            <thead>
              <tr className="font-mono text-[10px] uppercase tracking-[0.16em] text-steel">
                <th className="py-2">Name</th>
                <th>Permission</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {snap.agents.map((agent) => {
                const last = agent.runs[0];
                return (
                  <tr key={agent.id} className="border-t border-bone/10">
                    <td className="py-4">
                      <p className="font-display text-xl font-extrabold uppercase">
                        {agent.name}
                      </p>
                      <p className="text-sm text-ash">{agent.purpose}</p>
                    </td>
                    <td className="font-mono text-xs">{agent.permissionLevel}</td>
                    <td className="font-mono text-xs text-ash">
                      {last
                        ? `${last.status} · ${last.startedAt.toISOString()}`
                        : "No runs yet"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}

function LedgerRow({
  label,
  value,
  empty,
}: {
  label: string;
  value: string;
  empty: boolean;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] gap-4 border-b border-bone/10 px-5 py-4 last:border-b-0">
      <dt className="font-mono text-[11px] uppercase tracking-[0.16em] text-ash">
        {label}
      </dt>
      <dd className={empty ? "font-mono text-sm text-steel" : "font-mono text-sm text-bone"}>
        {value}
      </dd>
    </div>
  );
}
