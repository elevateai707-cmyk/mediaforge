import Link from "next/link";
import type { Category, Product } from "@prisma/client";
import { StampCover } from "@/components/storefront/stamp-cover";
import { formatPrice } from "@/lib/utils";

interface ProductSheetProps {
  product: Product & { category: Category | null };
  tall?: boolean;
}

export function ProductSheet({ product, tall = false }: ProductSheetProps) {
  return (
    <Link href={`/product/${product.slug}`} className="group block">
      <StampCover
        lotNumber={product.lotNumber}
        name={product.name}
        tone={product.coverTone}
        className={tall ? "aspect-[3/4]" : "aspect-[4/3]"}
      />
      <div className="mt-3 flex items-baseline justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ash">
            {product.category?.name}
          </p>
          <p className="mt-1 font-display text-2xl font-extrabold uppercase leading-none tracking-tight text-bone group-hover:text-ember">
            {product.name}
          </p>
        </div>
        <p className="font-mono text-sm text-bone">{formatPrice(product.price)}</p>
      </div>
    </Link>
  );
}
