import type { MetadataRoute } from "next";
import { absUrl } from "@/lib/seo";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/command", "/api/", "/checkout/success"],
      },
      {
        userAgent: "GPTBot",
        allow: ["/", "/faq", "/shop", "/llms.txt", "/product/"],
        disallow: ["/command", "/api/", "/cart", "/checkout"],
      },
      {
        userAgent: "PerplexityBot",
        allow: ["/", "/faq", "/shop", "/llms.txt", "/product/"],
        disallow: ["/command", "/api/"],
      },
    ],
    sitemap: absUrl("/sitemap.xml"),
  };
}
