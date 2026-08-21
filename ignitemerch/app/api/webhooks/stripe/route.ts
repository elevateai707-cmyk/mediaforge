import { NextRequest, NextResponse } from "next/server";
import Stripe from "stripe";
import { randomUUID } from "crypto";
import { OrderStatus } from "@prisma/client";
import { prisma } from "@/lib/prisma";
import { getStripe } from "@/lib/stripe/client";

export async function POST(request: NextRequest) {
  const stripe = getStripe();
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!stripe || !secret) {
    return NextResponse.json(
      { error: "Stripe webhook is not configured." },
      { status: 503 },
    );
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return NextResponse.json({ error: "Missing signature." }, { status: 400 });
  }

  const payload = await request.text();
  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(payload, signature, secret);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid payload";
    return NextResponse.json({ error: message }, { status: 400 });
  }

  if (event.type === "checkout.session.completed") {
    const session = event.data.object as Stripe.Checkout.Session;
    const email = session.customer_email ?? session.customer_details?.email;
    if (!email) {
      return NextResponse.json({ received: true, skipped: "no-email" });
    }

    const existing = await prisma.order.findUnique({
      where: { stripeId: session.id },
    });
    if (existing) {
      return NextResponse.json({ received: true, duplicate: true });
    }

    const cartSessionId = session.metadata?.cartSessionId;
    const cartItems = cartSessionId
      ? await prisma.cartItem.findMany({
          where: { sessionId: cartSessionId },
          include: { product: true },
        })
      : [];

    const total = (session.amount_total ?? 0) / 100;
    await prisma.order.create({
      data: {
        stripeId: session.id,
        customerEmail: email,
        total,
        status: OrderStatus.COMPLETED,
        items: {
          create: cartItems.map((item) => ({
            productId: item.productId,
            price: item.product.price,
          })),
        },
        downloads: {
          create: cartItems.map((item) => ({
            productId: item.productId,
            token: randomUUID(),
            downloadsRemaining: 5,
            expiresAt: new Date(Date.now() + 1000 * 60 * 60 * 24 * 30),
          })),
        },
      },
    });

    await prisma.$transaction(
      cartItems.map((item) =>
        prisma.product.update({
          where: { id: item.productId },
          data: {
            purchases: { increment: item.quantity },
            revenue: { increment: item.product.price * item.quantity },
          },
        }),
      ),
    );

    if (cartSessionId) {
      await prisma.cartItem.deleteMany({ where: { sessionId: cartSessionId } });
    }
  }

  return NextResponse.json({ received: true });
}
