import { prisma } from "@/lib/prisma";

export default async function CampaignsPage() {
  const campaigns = await prisma.campaign.findMany({
    include: { creatives: true },
    orderBy: { createdAt: "desc" },
  });

  return (
    <main className="mx-auto max-w-[1440px] px-4 py-12 md:px-8">
      <h1 className="font-display text-6xl font-extrabold uppercase leading-none">
        Campaigns
      </h1>
      {campaigns.length === 0 ? (
        <p className="mt-8 border border-dashed border-bone/15 px-5 py-12 text-ash">
          No campaigns yet. Creatives appear after the video worker runs with
          real platform keys.
        </p>
      ) : (
        <ul className="mt-10 divide-y divide-bone/10">
          {campaigns.map((campaign) => (
            <li key={campaign.id} className="py-6">
              <p className="font-display text-3xl font-extrabold uppercase">
                {campaign.name}
              </p>
              <p className="mt-2 font-mono text-xs text-ash">
                Budget {campaign.budget} · Spend {campaign.spend} ·{" "}
                {campaign.creatives.length} creatives
              </p>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
