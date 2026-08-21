import { NextResponse } from "next/server";
import { getPublishedProducts } from "@/lib/catalog";

export async function GET() {
  const products = await getPublishedProducts();
  const lines = [
    "# IgniteMerch full catalog",
    "",
    ...products.flatMap((product) => [
      `## ${product.name}`,
      product.contents,
      product.researchNote,
      "",
    ]),
  ];
  return new NextResponse(lines.join("\n"), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
