import type { Product } from "@prisma/client";
import { absUrl } from "@/lib/seo";

interface ProductJsonLdProps {
  product: Product;
}

export function ProductJsonLd({ product }: ProductJsonLdProps) {
  const payload = {
    "@context": "https://schema.org",
    "@type": ["Product", "SoftwareApplication"],
    name: product.name,
    description: product.description,
    sku: product.slug,
    category: product.type,
    offers: {
      "@type": "Offer",
      price: product.price,
      priceCurrency: "USD",
      availability: "https://schema.org/InStock",
      url: absUrl(`/product/${product.slug}`),
    },
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  );
}
