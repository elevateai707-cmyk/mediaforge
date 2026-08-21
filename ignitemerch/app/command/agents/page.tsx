import { prisma } from "@/lib/prisma";

export default async function AgentsPage() {
  const agents = await prisma.agent.findMany({
    include: { runs: { orderBy: { startedAt: "desc" }, take: 5 } },
    orderBy: { name: "asc" },
  });

  return (
    <main className="mx-auto max-w-[1440px] px-4 py-12 md:px-8">
      <h1 className="font-display text-6xl font-extrabold uppercase leading-none">
        Agents
      </h1>
      <p className="mt-4 max-w-[54ch] text-ash">
        Permission changes need a connected model key. Runs appear here after a
        worker executes. This list is live from Postgres.
      </p>
      <ul className="mt-10 divide-y divide-bone/10 border-y border-bone/10">
        {agents.map((agent) => (
          <li key={agent.id} className="py-6">
            <p className="font-display text-3xl font-extrabold uppercase">
              {agent.name}
            </p>
            <p className="mt-1 font-mono text-xs text-ash">
              {agent.provider} · {agent.model} · {agent.permissionLevel}
            </p>
            <p className="mt-2 text-ash">{agent.purpose}</p>
            {agent.runs.length === 0 ? (
              <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.14em] text-steel">
                No runs yet
              </p>
            ) : (
              <ul className="mt-3 space-y-1 font-mono text-xs text-ash">
                {agent.runs.map((run) => (
                  <li key={run.id}>
                    {run.status} · {run.startedAt.toISOString()}
                    {run.costUsd ? ` · $${run.costUsd.toFixed(4)}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
