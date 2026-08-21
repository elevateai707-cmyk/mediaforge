import type { Bundle, BundleItem, Product } from "@prisma/client";
import { AddToCartButton } from "@/components/storefront/add-to-cart-button";
import { formatPrice } from "@/lib/utils";

type BundleWithItems = Bundle & {
  items: Array<BundleItem & { product: Product }>;
};

interface BundleTicketProps {
  bundle: BundleWithItems;
  productId?: string;
}

export function BundleTicket({ bundle, productId }: BundleTicketProps) {
  return (
    <section className="border border-bone/10 bg-ink">
      <div className="grid md:grid-cols-[1.1fr_0.9fr]">
        <div className="p-8">
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-ember">
            Bundle
          </p>
          <h2 className="mt-3 font-display text-5xl font-extrabold uppercase leading-[0.85] tracking-tight">
            {bundle.name}
          </h2>
          <p className="mt-4 max-w-[46ch] text-ash">{bundle.description}</p>
          <p className="mt-6 font-mono text-3xl">
            {formatPrice(bundle.price)}
            <span className="ml-3 text-base text-steel line-through">
              {formatPrice(bundle.compareAtPrice)}
            </span>
          </p>
          {productId ? (
            <div className="mt-6">
              <AddToCartButton productId={productId} label="Add bundle to ticket" />
            </div>
          ) : null}
        </div>
        <ul className="border-t border-bone/10 md:border-l md:border-t-0">
          {bundle.items.map((item, index) => (
            <li
              key={item.id}
              className="flex items-baseline justify-between gap-4 border-b border-bone/10 px-6 py-4 last:border-b-0"
            >
              <span className="font-mono text-[11px] text-ember">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="flex-1 font-display text-xl font-extrabold uppercase leading-none">
                {item.product.name}
              </span>
              <span className="font-mono text-sm text-ash">
                {formatPrice(item.product.price)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
