import { ProductStatus } from "@prisma/client";
import { prisma } from "@/lib/prisma";
import { calculateTrendingScore } from "@/lib/utils";

export async function getPublishedProducts() {
  return prisma.product.findMany({
    where: { status: ProductStatus.PUBLISHED },
    include: { category: true, assets: true },
    orderBy: [{ featured: "desc" }, { lotNumber: "asc" }],
  });
}

export async function getFeaturedProduct() {
  return prisma.product.findFirst({
    where: { status: ProductStatus.PUBLISHED, featured: true },
    include: { category: true, assets: true },
    orderBy: { lotNumber: "asc" },
  });
}

export async function getProductBySlug(slug: string) {
  return prisma.product.findFirst({
    where: { slug, status: ProductStatus.PUBLISHED },
    include: {
      category: true,
      assets: true,
      versions: { orderBy: { createdAt: "desc" } },
      reviews: { include: { customer: { include: { user: true } } } },
    },
  });
}

export async function recordProductView(productId: string) {
  await prisma.product.update({
    where: { id: productId },
    data: { views: { increment: 1 } },
  });
}

export async function getCategoriesWithCounts() {
  return prisma.category.findMany({
    include: {
      _count: {
        select: { products: { where: { status: ProductStatus.PUBLISHED } } },
      },
    },
    orderBy: { name: "asc" },
  });
}

export async function getRelatedProducts(productId: string, categoryId: string | null) {
  return prisma.product.findMany({
    where: {
      status: ProductStatus.PUBLISHED,
      id: { not: productId },
      ...(categoryId ? { categoryId } : {}),
    },
    include: { category: true },
    take: 3,
    orderBy: { lotNumber: "asc" },
  });
}

export async function getBundles() {
  return prisma.bundle.findMany({
    include: {
      items: { include: { product: true } },
    },
    orderBy: { featured: "desc" },
  });
}

export async function getMembershipPlan() {
  return prisma.membershipPlan.findFirst({
    orderBy: { price: "asc" },
  });
}

export async function refreshTrendingScores() {
  const products = await prisma.product.findMany({
    where: { status: ProductStatus.PUBLISHED },
    select: { id: true, purchases: true, views: true, createdAt: true },
  });

  await Promise.all(
    products.map((product) =>
      prisma.product.update({
        where: { id: product.id },
        data: { trendingScore: calculateTrendingScore(product) },
      }),
    ),
  );
}

export async function getCommandSnapshot() {
  const [orderAgg, runAgg, pendingApprovals, agents, campaigns] =
    await Promise.all([
      prisma.order.aggregate({
        _sum: { total: true },
        _count: { _all: true },
        where: { status: "COMPLETED" },
      }),
      prisma.agentRun.aggregate({
        _sum: { costUsd: true },
        _count: { _all: true },
      }),
      prisma.approvalRequest.count({ where: { status: "PENDING" } }),
      prisma.agent.findMany({
        include: {
          runs: { orderBy: { startedAt: "desc" }, take: 1 },
        },
        orderBy: { name: "asc" },
      }),
      prisma.campaign.findMany({
        include: { creatives: true },
        orderBy: { createdAt: "desc" },
      }),
    ]);

  return {
    completedOrderCount: orderAgg._count._all,
    completedRevenue: orderAgg._sum.total ?? 0,
    agentRunCount: runAgg._count._all,
    agentSpend: runAgg._sum.costUsd ?? 0,
    pendingApprovals,
    agents,
    campaigns,
  };
}
