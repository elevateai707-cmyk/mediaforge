export type AgentRunStatus = "RUNNING" | "COMPLETED" | "FAILED";

export interface AgentDefinition {
  name: string;
  model: string;
  provider: string;
  purpose: string;
}

export interface AgentTelemetry {
  runId: string;
  status: AgentRunStatus;
  durationMs: number | null;
  costUsd: number | null;
}
