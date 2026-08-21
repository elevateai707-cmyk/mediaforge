import { NextResponse } from "next/server";
import { refreshTrendingScores } from "@/lib/catalog";
import { indexProduct } from "@/lib/geo/indexer";
import { prisma } from "@/lib/prisma";
import { ProductStatus } from "@prisma/client";
import { PricingOptimizerAgent } from "@/lib/agents/pricing-optimizer-agent";
import { assertUnderCostCap } from "@/lib/guards/cost-killswitch";
import { allowRequest } from "@/lib/security/rate-limit";

export async function POST() {
  if (!allowRequest("agent-tick", 6, 60_000)) {
    return NextResponse.json({ error: "Tick rate limited." }, { status: 429 });
  }

  try {
    await assertUnderCostCap();
    await refreshTrendingScores();
    const products = await prisma.product.findMany({
      where: { status: ProductStatus.PUBLISHED },
      select: { id: true },
    });
    for (const product of products) {
      await indexProduct(product.id);
    }
    const signals = await new PricingOptimizerAgent().inspect();
    return NextResponse.json({
      ok: true,
      indexed: products.length,
      pricingSignals: signals,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Tick failed.";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
