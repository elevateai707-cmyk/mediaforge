import Link from "next/link";
import type { Metadata } from "next";
import { ProductType } from "@prisma/client";
import { getCategoriesWithCounts, getPublishedProducts } from "@/lib/catalog";
import { ProductSheet } from "@/components/storefront/product-sheet";

const TYPE_FILTERS: Array<{ label: string; value: ProductType | "ALL" }> = [
  { label: "All", value: "ALL" },
  { label: "Workflows", value: ProductType.WORKFLOW },
  { label: "Creator", value: ProductType.CREATOR_KIT },
  { label: "Automation", value: ProductType.AUTOMATION_TEMPLATE },
  { label: "Prompts", value: ProductType.PROMPT_PACK },
  { label: "Business", value: ProductType.BUSINESS_KIT },
  { label: "Bundles", value: ProductType.BUNDLE },
];

export const metadata: Metadata = {
  title: "The drop",
  description:
    "Operator kits on the floor: ComfyUI, UGC, n8n, Cursor, listings, outbound, brand, support.",
  alternates: { canonical: "/shop" },
};

interface ShopPageProps {
  searchParams: Promise<{ type?: string; category?: string }>;
}

export default async function ShopPage({ searchParams }: ShopPageProps) {
  const params = await searchParams;
  const [products, categories] = await Promise.all([
    getPublishedProducts(),
    getCategoriesWithCounts(),
  ]);

  const typeFilter = parseType(params.type);
  const categoryFilter = params.category ?? "all";

  const visible = products.filter((product) => {
    const typeOk = typeFilter === "ALL" || product.type === typeFilter;
    const categoryOk =
      categoryFilter === "all" || product.category?.slug === categoryFilter;
    return typeOk && categoryOk;
  });

  return (
    <main className="mx-auto max-w-[1440px] px-4 py-12 md:px-8">
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
        Catalog
      </p>
      <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-[0.82] tracking-tight md:text-8xl">
        The drop
      </h1>

      <div className="mt-8 flex flex-wrap gap-2">
        {TYPE_FILTERS.map((filter) => {
          const href =
            filter.value === "ALL"
              ? "/shop"
              : `/shop?type=${filter.value}${categoryFilter !== "all" ? `&category=${categoryFilter}` : ""}`;
          const active =
            (filter.value === "ALL" && typeFilter === "ALL") ||
            filter.value === typeFilter;
          return (
            <Link
              key={filter.label}
              href={href}
              className={
                active
                  ? "bg-ember px-3 py-2 font-mono text-[11px] uppercase tracking-[0.16em] text-soot"
                  : "border border-bone/15 px-3 py-2 font-mono text-[11px] uppercase tracking-[0.16em] text-ash hover:text-bone"
              }
            >
              {filter.label}
            </Link>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Link
          href={typeFilter === "ALL" ? "/shop" : `/shop?type=${typeFilter}`}
          className={
            categoryFilter === "all"
              ? "font-mono text-[11px] uppercase tracking-[0.16em] text-ember"
              : "font-mono text-[11px] uppercase tracking-[0.16em] text-steel"
          }
        >
          Every bay
        </Link>
        {categories.map((category) => (
          <Link
            key={category.id}
            href={
              typeFilter === "ALL"
                ? `/shop?category=${category.slug}`
                : `/shop?type=${typeFilter}&category=${category.slug}`
            }
            className={
              categoryFilter === category.slug
                ? "font-mono text-[11px] uppercase tracking-[0.16em] text-ember"
                : "font-mono text-[11px] uppercase tracking-[0.16em] text-steel hover:text-bone"
            }
          >
            {category.name}
            {category._count.products > 0
              ? ` ${category._count.products}`
              : ""}
          </Link>
        ))}
      </div>

      {visible.length === 0 ? (
        <p className="mt-16 border border-dashed border-bone/15 px-6 py-16 text-center text-ash">
          Nothing in this bay yet.
        </p>
      ) : (
        <div className="mt-12 grid gap-10 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((product, index) => (
            <ProductSheet
              key={product.id}
              product={product}
              tall={index % 3 === 0}
            />
          ))}
        </div>
      )}
    </main>
  );
}

function parseType(value: string | undefined): ProductType | "ALL" {
  if (!value) return "ALL";
  const match = (Object.values(ProductType) as string[]).find(
    (item) => item === value,
  );
  return (match as ProductType | undefined) ?? "ALL";
}
