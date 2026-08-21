import Link from "next/link";
import { prisma } from "@/lib/prisma";

interface SuccessPageProps {
  searchParams: Promise<{ order?: string; dev?: string; session_id?: string }>;
}

export default async function CheckoutSuccessPage({
  searchParams,
}: SuccessPageProps) {
  const params = await searchParams;
  const order = params.order
    ? await prisma.order.findUnique({
        where: { id: params.order },
        include: { downloads: { include: { product: true } } },
      })
    : null;

  return (
    <main className="mx-auto max-w-[720px] px-4 py-16">
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
        Pressed
      </p>
      <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-none">
        Ticket paid
      </h1>
      {params.dev === "1" ? (
        <p className="mt-4 border border-ember/40 px-4 py-3 font-mono text-[11px] uppercase tracking-[0.14em] text-ember">
          Dev checkout. These links are real tokens in your local database.
        </p>
      ) : null}
      {order && order.downloads.length > 0 ? (
        <ul className="mt-8 divide-y divide-bone/10 border-y border-bone/10">
          {order.downloads.map((download) => (
            <li key={download.id} className="py-4">
              <p className="font-display text-2xl font-extrabold uppercase">
                {download.product.name}
              </p>
              <Link
                href={`/api/downloads/${download.token}`}
                className="mt-2 inline-block font-mono text-xs uppercase tracking-[0.16em] text-ember"
              >
                Download kit
              </Link>
              <p className="mt-1 font-mono text-[11px] text-steel">
                {download.downloadsRemaining} pulls left
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-6 text-ash">
          If you paid with Stripe, the webhook mints download tokens and emails
          this page. Locally, use the ticket links above.
        </p>
      )}
      <Link href="/shop" className="mt-10 inline-block font-mono text-xs uppercase tracking-[0.16em] text-ash">
        Back to the drop
      </Link>
    </main>
  );
}
