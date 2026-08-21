"use server";

import { randomUUID } from "crypto";
import { redirect } from "next/navigation";
import { OrderStatus, ProductStatus } from "@prisma/client";
import { prisma } from "@/lib/prisma";
import { getSessionId } from "@/lib/session";
import { getStripe, isStripeConfigured } from "@/lib/stripe/client";

export async function startCheckout(formData: FormData) {
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  if (!email || !email.includes("@")) {
    throw new Error("Need a real email for the download ticket.");
  }

  const sessionId = await getSessionId();
  const items = await prisma.cartItem.findMany({
    where: { sessionId },
    include: { product: true },
  });
  if (items.length === 0) {
    throw new Error("Cart is empty.");
  }

  const unpublished = items.find(
    (item) => item.product.status !== ProductStatus.PUBLISHED,
  );
  if (unpublished) {
    throw new Error("A lot in the cart is no longer on the floor.");
  }

  const total = items.reduce(
    (sum, item) => sum + item.product.price * item.quantity,
    0,
  );
  const site = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3005";

  if (isStripeConfigured()) {
    const stripe = getStripe();
    if (!stripe) throw new Error("Stripe client missing.");
    const session = await stripe.checkout.sessions.create({
      mode: "payment",
      customer_email: email,
      success_url: `${site}/checkout/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${site}/cart`,
      line_items: items.map((item) => ({
        quantity: item.quantity,
        price_data: {
          currency: "usd",
          unit_amount: Math.round(item.product.price * 100),
          product_data: {
            name: `${item.product.lotNumber} ${item.product.name}`,
            description: item.product.subtitle ?? undefined,
          },
        },
      })),
      metadata: { cartSessionId: sessionId },
    });
    if (!session.url) throw new Error("Stripe did not return a checkout URL.");
    redirect(session.url);
  }

  const order = await prisma.order.create({
    data: {
      customerEmail: email,
      total,
      status: OrderStatus.COMPLETED,
      stripeId: `dev_${randomUUID()}`,
      items: {
        create: items.map((item) => ({
          productId: item.productId,
          price: item.product.price,
        })),
      },
      downloads: {
        create: items.map((item) => ({
          productId: item.productId,
          token: randomUUID(),
          downloadsRemaining: 5,
          expiresAt: new Date(Date.now() + 1000 * 60 * 60 * 24 * 30),
        })),
      },
    },
  });

  await prisma.$transaction(
    items.map((item) =>
      prisma.product.update({
        where: { id: item.productId },
        data: {
          purchases: { increment: item.quantity },
          revenue: { increment: item.product.price * item.quantity },
        },
      }),
    ),
  );

  await prisma.cartItem.deleteMany({ where: { sessionId } });
  redirect(`/checkout/success?order=${order.id}&dev=1`);
}
