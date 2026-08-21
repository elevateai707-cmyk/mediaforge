import type { MetadataRoute } from "next";
import { getPublishedProducts } from "@/lib/catalog";
import { absUrl } from "@/lib/seo";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const products = await getPublishedProducts();
  const staticRoutes = ["/", "/shop", "/faq", "/accessibility", "/cart", "/checkout"].map(
    (path) => ({
      url: absUrl(path),
      lastModified: new Date(),
    }),
  );
  const productRoutes = products.map((product) => ({
    url: absUrl(`/product/${product.slug}`),
    lastModified: product.updatedAt,
  }));
  return [...staticRoutes, ...productRoutes];
}
