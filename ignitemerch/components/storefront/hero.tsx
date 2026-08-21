import Link from "next/link";
import type { Product, Category } from "@prisma/client";
import { AddToCartButton } from "@/components/storefront/add-to-cart-button";
import { StampCover } from "@/components/storefront/stamp-cover";
import { formatPrice } from "@/lib/utils";

interface HeroProps {
  featured: (Product & { category: Category | null }) | null;
}

export function Hero({ featured }: HeroProps) {
  return (
    <section className="mx-auto grid max-w-[1440px] gap-10 px-4 pb-8 pt-10 md:grid-cols-[1.15fr_0.85fr] md:items-end md:px-8 md:pt-16">
      <div className="enter">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
          Drop 01 · Operator kits
        </p>
        <h1 className="mt-4 max-w-[14ch] font-display text-[clamp(3.4rem,9vw,8.2rem)] font-extrabold uppercase leading-[0.8] tracking-tight text-bone">
          Kits you can run tonight.
        </h1>
        <p className="mt-6 max-w-[42ch] text-lg leading-relaxed text-ash">
          ComfyUI photo desks, UGC ad floors, n8n lines, and Cursor skills.
          Priced out of the $10 prompt junk pile. Built as files you actually
          import.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link
            href="/shop"
            className="inline-flex h-12 items-center bg-ember px-6 font-mono text-[11px] uppercase tracking-[0.18em] text-soot transition-[transform,background-color] duration-200 ease-expo hover:bg-ember-hot active:scale-[0.96]"
          >
            Open the drop
          </Link>
          <Link
            href="#research"
            className="inline-flex h-12 items-center border border-bone/20 px-5 font-mono text-[11px] uppercase tracking-[0.18em] text-bone transition-colors duration-200 hover:border-bone/50"
          >
            Why these SKUs
          </Link>
        </div>
      </div>

      {featured ? (
        <article className="enter enter-3 border border-bone/10 bg-ink shadow-heat">
          <StampCover
            lotNumber={featured.lotNumber}
            name={featured.name}
            tone={featured.coverTone}
            className="aspect-[4/5] min-h-[320px]"
          />
          <div className="flex flex-wrap items-end justify-between gap-4 p-5">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ash">
                {featured.category?.name ?? "Lot"}
              </p>
              <p className="mt-2 font-mono text-xl text-bone">
                {formatPrice(featured.price)}
                {featured.compareAtPrice ? (
                  <span className="ml-2 text-sm text-steel line-through">
                    {formatPrice(featured.compareAtPrice)}
                  </span>
                ) : null}
              </p>
            </div>
            <div className="flex gap-2">
              <Link
                href={`/product/${featured.slug}`}
                className="inline-flex h-12 items-center border border-bone/20 px-4 font-mono text-[11px] uppercase tracking-[0.16em] text-bone"
              >
                Inspect
              </Link>
              <AddToCartButton productId={featured.id} />
            </div>
          </div>
        </article>
      ) : (
        <div className="enter enter-3 border border-dashed border-bone/20 p-10 text-ash">
          No lots on the floor yet.
        </div>
      )}
    </section>
  );
}
