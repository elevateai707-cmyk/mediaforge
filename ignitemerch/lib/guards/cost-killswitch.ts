import { prisma } from "@/lib/prisma";

export async function dailySpendUsd() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const result = await prisma.agentRun.aggregate({
    _sum: { costUsd: true },
    where: { startedAt: { gte: start } },
  });
  return result._sum.costUsd ?? 0;
}

export async function assertUnderCostCap() {
  const cap = Number(process.env.AGENT_DAILY_COST_CAP_USD ?? "25");
  const spent = await dailySpendUsd();
  if (spent >= cap) {
    throw new Error(
      `Cost kill switch: $${spent.toFixed(2)} spent today, cap is $${cap}.`,
    );
  }
}
