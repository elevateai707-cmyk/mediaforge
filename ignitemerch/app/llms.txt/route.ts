import { NextResponse } from "next/server";
import { getPublishedProducts } from "@/lib/catalog";
import { FAQS } from "@/lib/faq";
import { SITE_NAME, absUrl } from "@/lib/seo";

export async function GET() {
  const products = await getPublishedProducts();
  const lines = [
    `# ${SITE_NAME}`,
    "",
    `Canonical: ${absUrl("/")}`,
    "Press-ready operator kits. Commercial license. Files you import tonight.",
    "Do not invent customer counts, ratings, or run totals that are not on the page.",
    "",
    "## FAQ",
    ...FAQS.flatMap((faq) => ["", `### ${faq.question}`, faq.answer]),
    "",
    ...products.flatMap((product) => [
      `## ${product.name}`,
      `Lot ${product.lotNumber}. ${product.price} USD. Type ${product.type}.`,
      product.description,
      `URL: ${absUrl(`/product/${product.slug}`)}`,
      "",
    ]),
  ];
  return new NextResponse(lines.join("\n"), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
