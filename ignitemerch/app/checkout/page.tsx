import { getCart } from "@/app/actions/cart-actions";
import { startCheckout } from "@/app/actions/checkout-actions";
import { isStripeConfigured } from "@/lib/stripe/client";
import { formatPrice } from "@/lib/utils";
import Link from "next/link";

export default async function CheckoutPage() {
  const cart = await getCart();
  const stripeReady = isStripeConfigured();

  if (cart.items.length === 0) {
    return (
      <main className="mx-auto max-w-[640px] px-4 py-16">
        <h1 className="font-display text-5xl font-extrabold uppercase">
          Checkout
        </h1>
        <p className="mt-4 text-ash">
          Ticket is blank.{" "}
          <Link href="/shop" className="text-ember">
            Open the drop
          </Link>
          .
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-[640px] px-4 py-12">
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
        One page
      </p>
      <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-none">
        Checkout
      </h1>
      <ul className="mt-8 divide-y divide-bone/10 border-y border-bone/10">
        {cart.items.map((item) => (
          <li key={item.id} className="flex justify-between py-4">
            <span>
              {item.product.name} × {item.quantity}
            </span>
            <span className="font-mono">
              {formatPrice(item.product.price * item.quantity)}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-4 flex justify-between font-mono text-xl">
        <span>Total</span>
        <span>{formatPrice(cart.subtotal)}</span>
      </p>
      {!stripeReady ? (
        <p className="mt-6 border border-ember/40 bg-ember/10 px-4 py-3 font-mono text-[11px] uppercase tracking-[0.14em] text-ember">
          Dev checkout · Stripe keys are empty, so the order is recorded locally
          and download tokens are minted in the database.
        </p>
      ) : null}
      <form action={startCheckout} className="mt-8 space-y-4">
        <label className="block">
          <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-ash">
            Email for the download ticket
          </span>
          <input
            required
            type="email"
            name="email"
            className="mt-2 h-12 w-full border border-bone/15 bg-ink px-3 text-bone"
            placeholder="you@studio.com"
          />
        </label>
        <button
          type="submit"
          className="inline-flex h-12 w-full items-center justify-center bg-ember font-mono text-[11px] uppercase tracking-[0.18em] text-soot active:scale-[0.96]"
        >
          {stripeReady ? "Pay with Stripe" : "Record order locally"}
        </button>
      </form>
    </main>
  );
}
