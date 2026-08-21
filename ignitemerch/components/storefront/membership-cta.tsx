import type { MembershipPlan } from "@prisma/client";
import { formatPrice } from "@/lib/utils";

interface MembershipCtaProps {
  plan: MembershipPlan | null;
}

export function MembershipCta({ plan }: MembershipCtaProps) {
  if (!plan) {
    return null;
  }

  const features = plan.features.split(",").map((item) => item.trim());

  return (
    <section className="mx-auto max-w-[1440px] px-4 py-20 md:px-8">
      <div className="grid gap-8 bg-ember px-6 py-10 text-soot md:grid-cols-[1.2fr_0.8fr] md:px-12 md:py-14">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.22em]">
            Membership
          </p>
          <h2 className="mt-3 font-display text-6xl font-extrabold uppercase leading-[0.82] tracking-tight md:text-8xl">
            {plan.name}
          </h2>
          <p className="mt-5 max-w-[42ch] text-lg">
            A monthly lot, member price on the live drop, and the archive. Cancel
            any time. No fake “limited seats.”
          </p>
        </div>
        <div className="flex flex-col justify-end gap-6">
          <p className="font-mono text-4xl">
            {formatPrice(plan.price)}
            <span className="text-base"> / {plan.interval}</span>
          </p>
          <ul className="space-y-2 text-sm">
            {features.map((feature) => (
              <li key={feature}>{feature}</li>
            ))}
          </ul>
          <a
            href="/checkout?plan=press-pass"
            className="inline-flex h-12 w-fit items-center bg-soot px-5 font-mono text-[11px] uppercase tracking-[0.18em] text-bone transition-transform duration-200 ease-expo active:scale-[0.96]"
          >
            Join the press
          </a>
        </div>
      </div>
    </section>
  );
}
