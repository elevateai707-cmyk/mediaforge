import { FAQS } from "@/lib/faq";
import { formatFaqForSchema } from "@/lib/chat/desk";
import { absUrl } from "@/lib/seo";

export function FaqJsonLd() {
  const payload = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "@id": absUrl("/faq#faq"),
    mainEntity: formatFaqForSchema(FAQS),
    inLanguage: "en-US",
  };
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  );
}
