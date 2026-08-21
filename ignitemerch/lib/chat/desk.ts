import { findFaqMatches, scoreText, tokenize, type FaqItem } from "@/lib/faq";
import { prisma } from "@/lib/prisma";
import { ProductStatus } from "@prisma/client";

export interface ChatAnswer {
  text: string;
  sources: Array<{ label: string; href: string }>;
}

export async function answerDeskChat(message: string): Promise<ChatAnswer> {
  const trimmed = message.trim();
  if (!trimmed) {
    return {
      text: "Ask about a lot, downloads, license, Stripe, or accessibility.",
      sources: [{ label: "FAQ", href: "/faq" }],
    };
  }

  const faqs = findFaqMatches(trimmed, 2);
  const products = await prisma.product.findMany({
    where: { status: ProductStatus.PUBLISHED },
    select: { name: true, slug: true, subtitle: true, price: true, lotNumber: true },
  });
  const tokens = tokenize(trimmed);
  const productHits = products
    .map((product) => ({
      product,
      score: scoreText(
        `${product.name} ${product.subtitle ?? ""} lot ${product.lotNumber}`,
        tokens,
      ),
    }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 2);

  const lines: string[] = [];
  const sources: Array<{ label: string; href: string }> = [];

  if (productHits.length > 0) {
    for (const hit of productHits) {
      lines.push(
        `Lot ${hit.product.lotNumber} ${hit.product.name} is $${hit.product.price}. ${hit.product.subtitle ?? ""}`,
      );
      sources.push({
        label: hit.product.name,
        href: `/product/${hit.product.slug}`,
      });
    }
  }

  if (faqs.length > 0) {
    for (const faq of faqs) {
      lines.push(faq.answer);
      sources.push({ label: faq.question, href: faq.href ?? "/faq" });
    }
  }

  if (lines.length === 0) {
    return {
      text: "I only answer from the catalog and FAQ. Open /faq or name a lot (photo, n8n, Cursor, UGC).",
      sources: [{ label: "FAQ", href: "/faq" }],
    };
  }

  return {
    text: lines.join("\n\n"),
    sources: uniqueSources(sources),
  };
}

function uniqueSources(sources: Array<{ label: string; href: string }>) {
  const seen = new Set<string>();
  const out: Array<{ label: string; href: string }> = [];
  for (const source of sources) {
    if (seen.has(source.href)) continue;
    seen.add(source.href);
    out.push(source);
  }
  return out;
}

export function formatFaqForSchema(faqs: FaqItem[]) {
  return faqs.map((faq) => ({
    "@type": "Question",
    name: faq.question,
    acceptedAnswer: { "@type": "Answer", text: faq.answer },
  }));
}
