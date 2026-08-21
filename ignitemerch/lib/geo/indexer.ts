import { prisma } from "@/lib/prisma";
import { productSchema } from "@/lib/geo/schema";

export async function indexProduct(productId: string) {
  const product = await prisma.product.findUnique({ where: { id: productId } });
  if (!product) return;
  await prisma.geoIndex.upsert({
    where: { productId },
    update: {
      structuredSchema: productSchema(product),
      llmSummaryText: `${product.name}. ${product.description}`,
      lastIndexedAt: new Date(),
    },
    create: {
      productId,
      structuredSchema: productSchema(product),
      llmSummaryText: `${product.name}. ${product.description}`,
    },
  });
}
