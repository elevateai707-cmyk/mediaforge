import { prisma } from "@/lib/prisma";

export default async function IntelligencePage() {
  const products = await prisma.product.findMany({
    where: { status: "PUBLISHED" },
    select: {
      name: true,
      lotNumber: true,
      views: true,
      purchases: true,
      conversionRate: true,
      trendingScore: true,
    },
    orderBy: { lotNumber: "asc" },
  });

  const anyActivity = products.some(
    (product) => product.views > 0 || product.purchases > 0,
  );

  return (
    <main className="mx-auto max-w-[1440px] px-4 py-12 md:px-8">
      <h1 className="font-display text-6xl font-extrabold uppercase leading-none">
        Intelligence
      </h1>
      <p className="mt-4 max-w-[54ch] text-ash">
        Views increment when a product page loads. Purchases increment when an
        order completes. Empty cells stay empty.
      </p>
      {!anyActivity ? (
        <p className="mt-8 border border-dashed border-bone/15 px-5 py-12 text-ash">
          No merchandising signal yet. Open a product page to record a view.
        </p>
      ) : null}
      <table className="mt-10 w-full text-left">
        <thead>
          <tr className="font-mono text-[10px] uppercase tracking-[0.16em] text-steel">
            <th className="py-2">Lot</th>
            <th>Name</th>
            <th>Views</th>
            <th>Purchases</th>
          </tr>
        </thead>
        <tbody>
          {products.map((product) => (
            <tr key={product.lotNumber} className="border-t border-bone/10">
              <td className="py-3 font-mono text-ember">{product.lotNumber}</td>
              <td className="font-display text-xl font-extrabold uppercase">
                {product.name}
              </td>
              <td className="font-mono text-sm tabular">
                {product.views === 0 ? "—" : product.views}
              </td>
              <td className="font-mono text-sm tabular">
                {product.purchases === 0 ? "—" : product.purchases}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
