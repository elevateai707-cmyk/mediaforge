import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AddToCartButton } from "@/components/storefront/add-to-cart-button";
import { StampCover } from "@/components/storefront/stamp-cover";
import { ProductJsonLd } from "@/components/seo/product-json-ld";
import {
  getProductBySlug,
  getRelatedProducts,
  recordProductView,
} from "@/lib/catalog";
import { bytesLabel, formatPrice } from "@/lib/utils";

interface ProductPageProps {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({
  params,
}: ProductPageProps): Promise<Metadata> {
  const { slug } = await params;
  const product = await getProductBySlug(slug);
  if (!product) return { title: "Lot missing" };
  return {
    title: product.name,
    description: product.subtitle ?? product.description,
  };
}

export default async function ProductPage({ params }: ProductPageProps) {
  const { slug } = await params;
  const product = await getProductBySlug(slug);
  if (!product) notFound();

  await recordProductView(product.id);
  const related = await getRelatedProducts(product.id, product.categoryId);

  return (
    <main className="mx-auto max-w-[1440px] px-4 py-10 md:px-8">
      <ProductJsonLd product={product} />
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-ash">
        <Link href="/shop" className="hover:text-bone">
          Drop
        </Link>
        <span className="mx-2">/</span>
        {product.category?.name}
      </p>

      <div className="mt-6 grid gap-10 lg:grid-cols-[0.9fr_1.1fr]">
        <StampCover
          lotNumber={product.lotNumber}
          name={product.name}
          tone={product.coverTone}
          className="min-h-[420px] lg:min-h-[560px]"
        />
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-ember">
            Lot {product.lotNumber} · v{product.version}
          </p>
          <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-[0.82] tracking-tight md:text-7xl">
            {product.name}
          </h1>
          <p className="mt-4 max-w-[46ch] text-lg text-ash">{product.subtitle}</p>
          <div className="mt-6 flex items-baseline gap-3">
            <p className="font-mono text-3xl">{formatPrice(product.price)}</p>
            {product.compareAtPrice ? (
              <p className="font-mono text-lg text-steel line-through">
                {formatPrice(product.compareAtPrice)}
              </p>
            ) : null}
          </div>
          <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.16em] text-ash">
            {product.license} license · commercial use{" "}
            {product.commercialUse ? "on" : "off"}
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <AddToCartButton productId={product.id} label="Add this lot" />
            <Link
              href="/cart"
              className="inline-flex h-12 items-center border border-bone/20 px-5 font-mono text-[11px] uppercase tracking-[0.16em]"
            >
              View ticket
            </Link>
          </div>
          <p className="mt-8 max-w-[58ch] leading-relaxed text-ash">
            {product.description}
          </p>
          <div className="mt-8 border-t border-bone/10 pt-8">
            <h2 className="font-mono text-[11px] uppercase tracking-[0.18em] text-ember">
              In the crate
            </h2>
            <p className="mt-3 max-w-[58ch] text-bone">{product.contents}</p>
          </div>
          <div className="mt-8">
            <h2 className="font-mono text-[11px] uppercase tracking-[0.18em] text-ember">
              Why we stock it
            </h2>
            <p className="mt-3 max-w-[58ch] text-sm leading-relaxed text-ash">
              {product.researchNote}
            </p>
          </div>
        </div>
      </div>

      <section className="mt-16">
        <h2 className="font-display text-4xl font-extrabold uppercase tracking-tight">
          Files
        </h2>
        {product.assets.length === 0 ? (
          <p className="mt-4 text-ash">No files attached yet.</p>
        ) : (
          <ul className="mt-4 divide-y divide-bone/10 border-y border-bone/10">
            {product.assets.map((asset) => (
              <li
                key={asset.id}
                className="flex flex-wrap items-baseline justify-between gap-3 py-4"
              >
                <span className="font-display text-xl font-extrabold uppercase">
                  {asset.name}
                </span>
                <span className="font-mono text-xs text-ash">
                  {asset.fileType} · {bytesLabel(asset.sizeBytes)} · unlocks after
                  purchase
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-16">
        <h2 className="font-display text-4xl font-extrabold uppercase tracking-tight">
          Reviews
        </h2>
        {product.reviews.length === 0 ? (
          <p className="mt-4 border border-dashed border-bone/15 px-5 py-8 text-ash">
            No verified reviews yet. They show up after a completed order.
          </p>
        ) : (
          <ul className="mt-6 space-y-6">
            {product.reviews.map((review) => (
              <li key={review.id} className="border-b border-bone/10 pb-6">
                <p className="font-mono text-xs text-ember">
                  {review.rating}/5 · {review.verified ? "Verified" : "Unverified"}
                </p>
                <p className="mt-2 max-w-[62ch] text-ash">{review.comment}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {related.length > 0 ? (
        <section className="mt-16">
          <h2 className="font-display text-4xl font-extrabold uppercase tracking-tight">
            Same bay
          </h2>
          <ul className="mt-6 divide-y divide-bone/10 border-y border-bone/10">
            {related.map((item) => (
              <li key={item.id}>
                <Link
                  href={`/product/${item.slug}`}
                  className="flex items-baseline justify-between gap-4 py-4"
                >
                  <span className="font-display text-2xl font-extrabold uppercase">
                    {item.name}
                  </span>
                  <span className="font-mono text-sm">
                    {formatPrice(item.price)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}
