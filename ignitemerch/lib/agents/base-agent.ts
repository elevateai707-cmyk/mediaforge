import { prisma } from "@/lib/prisma";
import type { AgentDefinition, AgentRunStatus } from "@/types/agent";

export abstract class BaseAgent {
  constructor(protected readonly definition: AgentDefinition) {}

  protected async startRun() {
    const agent = await prisma.agent.upsert({
      where: { name: this.definition.name },
      update: {
        model: this.definition.model,
        provider: this.definition.provider,
        purpose: this.definition.purpose,
      },
      create: {
        ...this.definition,
        permissionLevel: "NEEDS_APPROVAL",
      },
    });
    return prisma.agentRun.create({
      data: { agentId: agent.id, status: "RUNNING" },
    });
  }

  protected async finishRun(
    runId: string,
    status: AgentRunStatus,
    extra: { costUsd?: number; errorMessage?: string },
  ) {
    const run = await prisma.agentRun.findUniqueOrThrow({ where: { id: runId } });
    await prisma.agentRun.update({
      where: { id: runId },
      data: {
        status,
        completedAt: new Date(),
        durationMs: Date.now() - run.startedAt.getTime(),
        costUsd: extra.costUsd ?? 0,
        errorMessage: extra.errorMessage,
      },
    });
  }
}
