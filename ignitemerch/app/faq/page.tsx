import type { Metadata } from "next";
import { FAQS } from "@/lib/faq";
import { FaqJsonLd } from "@/components/seo/faq-json-ld";
import { absUrl } from "@/lib/seo";

export const metadata: Metadata = {
  title: "FAQ",
  description:
    "Downloads, licenses, Stripe, ComfyUI, n8n, and accessibility for IgniteMerch operator kits.",
  alternates: { canonical: absUrl("/faq") },
};

export default function FaqPage() {
  return (
    <article className="mx-auto max-w-[800px] px-4 py-12 md:px-0">
      <FaqJsonLd />
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
        AEO
      </p>
      <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-none">
        FAQ
      </h1>
      <p className="mt-4 max-w-[62ch] text-ash">
        Short answers written to match the page. The desk chatbot uses this same
        list. We do not invent store metrics here.
      </p>
      <dl className="mt-10 space-y-8">
        {FAQS.map((faq) => (
          <div key={faq.id} className="border-t border-bone/10 pt-6">
            <dt className="font-display text-2xl font-extrabold uppercase leading-tight">
              {faq.question}
            </dt>
            <dd className="mt-3 max-w-[62ch] leading-relaxed text-ash">
              {faq.answer}
            </dd>
          </div>
        ))}
      </dl>
    </article>
  );
}
