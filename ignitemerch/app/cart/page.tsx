import { getCart, setCartQuantity } from "@/app/actions/cart-actions";
import { formatPrice } from "@/lib/utils";
import Link from "next/link";

export default async function CartPage() {
  const cart = await getCart();

  return (
    <main className="mx-auto max-w-[720px] px-4 py-12 md:px-0">
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
        Ticket
      </p>
      <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-none tracking-tight">
        Cart
      </h1>

      <div className="relative mt-10 bg-bone text-soot">
        <div className="ticket-perf absolute inset-x-0 top-0 h-3" />
        <div className="px-6 pb-8 pt-8">
          {cart.items.length === 0 ? (
            <p className="py-10 text-center text-lg">
              Ticket is blank.{" "}
              <Link href="/shop" className="underline">
                Open the drop
              </Link>
              .
            </p>
          ) : (
            <ul>
              {cart.items.map((item) => (
                <li
                  key={item.id}
                  className="grid grid-cols-[1fr_auto] gap-3 border-b border-soot/15 py-5"
                >
                  <div>
                    <p className="font-mono text-[10px] uppercase tracking-[0.16em]">
                      Lot {item.product.lotNumber}
                    </p>
                    <p className="font-display text-3xl font-extrabold uppercase leading-none">
                      {item.product.name}
                    </p>
                    <div className="mt-3 flex items-center gap-2">
                      <form
                        action={setCartQuantity.bind(
                          null,
                          item.productId,
                          item.quantity - 1,
                        )}
                      >
                        <button
                          type="submit"
                          className="h-8 w-8 border border-soot/20 font-mono text-sm active:scale-[0.96]"
                          aria-label="Decrease quantity"
                        >
                          -
                        </button>
                      </form>
                      <span className="font-mono text-sm tabular">
                        {item.quantity}
                      </span>
                      <form
                        action={setCartQuantity.bind(
                          null,
                          item.productId,
                          item.quantity + 1,
                        )}
                      >
                        <button
                          type="submit"
                          className="h-8 w-8 border border-soot/20 font-mono text-sm active:scale-[0.96]"
                          aria-label="Increase quantity"
                        >
                          +
                        </button>
                      </form>
                    </div>
                  </div>
                  <p className="font-mono text-lg">
                    {formatPrice(item.product.price * item.quantity)}
                  </p>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-6 flex items-baseline justify-between">
            <span className="font-mono text-xs uppercase tracking-[0.16em]">
              Subtotal
            </span>
            <span className="font-mono text-2xl">{formatPrice(cart.subtotal)}</span>
          </div>
          {cart.items.length > 0 ? (
            <Link
              href="/checkout"
              className="mt-6 inline-flex h-12 w-full items-center justify-center bg-ember font-mono text-[11px] uppercase tracking-[0.18em] text-soot active:scale-[0.96]"
            >
              Checkout
            </Link>
          ) : null}
        </div>
        <div className="ticket-perf absolute inset-x-0 bottom-0 h-3" />
      </div>
    </main>
  );
}
