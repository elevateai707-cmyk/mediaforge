"use server";

import { revalidatePath } from "next/cache";
import { prisma } from "@/lib/prisma";
import { getSessionId, readSessionId } from "@/lib/session";
import { ProductStatus } from "@prisma/client";

export async function addToCart(productId: string) {
  const sessionId = await getSessionId();
  const product = await prisma.product.findFirst({
    where: { id: productId, status: ProductStatus.PUBLISHED },
    select: { id: true },
  });
  if (!product) {
    throw new Error("That lot is not on the floor.");
  }

  await prisma.cartItem.upsert({
    where: { sessionId_productId: { sessionId, productId } },
    update: { quantity: { increment: 1 } },
    create: { sessionId, productId, quantity: 1 },
  });

  revalidatePath("/cart");
  revalidatePath("/");
}

export async function setCartQuantity(productId: string, quantity: number) {
  const sessionId = await getSessionId();
  if (quantity <= 0) {
    await prisma.cartItem.deleteMany({ where: { sessionId, productId } });
  } else {
    await prisma.cartItem.updateMany({
      where: { sessionId, productId },
      data: { quantity },
    });
  }
  revalidatePath("/cart");
}

export async function getCart() {
  const sessionId = await readSessionId();
  if (!sessionId) {
    return { items: [], subtotal: 0, count: 0 };
  }
  const items = await prisma.cartItem.findMany({
    where: { sessionId },
    include: { product: true },
    orderBy: { createdAt: "asc" },
  });
  const subtotal = items.reduce(
    (sum, item) => sum + item.product.price * item.quantity,
    0,
  );
  const count = items.reduce((sum, item) => sum + item.quantity, 0);
  return { items, subtotal, count };
}
