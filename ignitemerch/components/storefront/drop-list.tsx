import Link from "next/link";
import type { Category, Product } from "@prisma/client";
import { formatPrice } from "@/lib/utils";

type ProductWithCategory = Product & { category: Category | null };

interface DropListProps {
  products: ProductWithCategory[];
}

export function DropList({ products }: DropListProps) {
  if (products.length === 0) {
    return (
      <p className="border border-dashed border-bone/15 px-6 py-16 text-center text-ash">
        No runs yet. The floor is empty until lots are published.
      </p>
    );
  }

  const [lead, ...rest] = products;

  return (
    <div className="space-y-3">
      {lead ? <DropLead product={lead} /> : null}
      <ul className="divide-y divide-bone/10 border-y border-bone/10">
        {rest.map((product, index) => (
          <li key={product.id} className={index === 0 ? "enter enter-4" : ""}>
            <Link
              href={`/product/${product.slug}`}
              className="group grid grid-cols-[4.5rem_1fr_auto] items-baseline gap-4 py-5 md:grid-cols-[5.5rem_minmax(0,1.4fr)_minmax(0,1fr)_auto]"
            >
              <span className="font-mono text-sm text-ember">
                {product.lotNumber}
              </span>
              <span className="font-display text-3xl font-extrabold uppercase leading-none tracking-tight text-bone transition-colors duration-200 group-hover:text-ember md:text-4xl">
                {product.name}
              </span>
              <span className="hidden max-w-[36ch] text-sm text-ash md:block">
                {product.subtitle}
              </span>
              <span className="font-mono text-sm text-bone">
                {formatPrice(product.price)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DropLead({ product }: { product: ProductWithCategory }) {
  return (
    <Link
      href={`/product/${product.slug}`}
      className="group grid gap-4 border border-bone/10 bg-ink p-5 md:grid-cols-[auto_1fr_auto] md:items-end"
    >
      <span className="font-mono text-xs uppercase tracking-[0.2em] text-ember">
        Lot {product.lotNumber} · {product.category?.name}
      </span>
      <div>
        <h3 className="font-display text-5xl font-extrabold uppercase leading-[0.85] tracking-tight text-bone group-hover:text-ember md:text-6xl">
          {product.name}
        </h3>
        <p className="mt-3 max-w-[52ch] text-ash">{product.subtitle}</p>
      </div>
      <p className="font-mono text-2xl text-bone">{formatPrice(product.price)}</p>
    </Link>
  );
}
