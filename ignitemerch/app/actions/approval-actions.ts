"use server";

import { revalidatePath } from "next/cache";
import { ApprovalStatus } from "@prisma/client";
import { prisma } from "@/lib/prisma";

export async function setApprovalStatus(
  id: string,
  status: "APPROVED" | "REJECTED",
) {
  const nextStatus =
    status === "APPROVED" ? ApprovalStatus.APPROVED : ApprovalStatus.REJECTED;
  await prisma.approvalRequest.update({
    where: { id },
    data: { status: nextStatus },
  });
  revalidatePath("/command");
  revalidatePath("/command/campaigns");
}
