import { prisma } from "@/lib/prisma";
import { BaseAgent } from "@/lib/agents/base-agent";

export class PricingOptimizerAgent extends BaseAgent {
  constructor() {
    super({
      name: "Pricing Optimizer",
      model: "gpt-5.4",
      provider: "openai",
      purpose: "Watch conversion vs views and propose price changes.",
    });
  }

  async inspect() {
    const run = await this.startRun();
    try {
      const products = await prisma.product.findMany({
        where: { status: "PUBLISHED" },
        select: { name: true, views: true, purchases: true, price: true },
      });
      const signals = products
        .filter((product) => product.views >= 20 && product.purchases === 0)
        .map((product) => ({
          name: product.name,
          suggestion:
            "High views, zero purchases. Consider a $10 drop or a tighter promise on the PDP.",
        }));
      await this.finishRun(run.id, "COMPLETED", { costUsd: 0 });
      return signals;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      await this.finishRun(run.id, "FAILED", { errorMessage: message });
      throw error;
    }
  }
}
