import {
  getBundles,
  getFeaturedProduct,
  getMembershipPlan,
  getPublishedProducts,
} from "@/lib/catalog";
import { BundleTicket } from "@/components/storefront/bundle-ticket";
import { DropList } from "@/components/storefront/drop-list";
import { Hero } from "@/components/storefront/hero";
import { MembershipCta } from "@/components/storefront/membership-cta";
import { ResearchStrip } from "@/components/storefront/research-strip";

export default async function HomePage() {
  const [featured, products, bundles, plan] = await Promise.all([
    getFeaturedProduct(),
    getPublishedProducts(),
    getBundles(),
    getMembershipPlan(),
  ]);

  const catalog = featured
    ? products.filter((product) => product.id !== featured.id)
    : products;
  const featuredBundle = bundles.find((bundle) => bundle.featured) ?? bundles[0];

  return (
    <main>
      <Hero featured={featured} />
      <section className="mx-auto max-w-[1440px] px-4 py-8 md:px-8 md:py-14">
        <div className="mb-8 flex items-end justify-between gap-4">
          <h2 className="font-display text-5xl font-extrabold uppercase leading-none tracking-tight md:text-6xl">
            Live drop
          </h2>
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-ash">
            {products.length} lots
          </p>
        </div>
        <DropList products={catalog} />
      </section>
      {featuredBundle ? (
        <section className="mx-auto max-w-[1440px] px-4 py-8 md:px-8">
          <BundleTicket
            bundle={featuredBundle}
            productId={products.find((p) => p.slug === featuredBundle.slug)?.id}
          />
        </section>
      ) : null}
      <ResearchStrip
        notes={products.slice(0, 6).map((product) => ({
          lotNumber: product.lotNumber,
          name: product.name,
          researchNote: product.researchNote,
        }))}
      />
      <MembershipCta plan={plan} />
    </main>
  );
}
