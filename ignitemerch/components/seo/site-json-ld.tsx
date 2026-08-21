import { SITE_NAME, absUrl } from "@/lib/seo";

export function SiteJsonLd() {
  const payload = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": absUrl("/#org"),
        name: SITE_NAME,
        url: absUrl("/"),
      },
      {
        "@type": "WebSite",
        "@id": absUrl("/#site"),
        name: SITE_NAME,
        url: absUrl("/"),
        publisher: { "@id": absUrl("/#org") },
        inLanguage: "en-US",
      },
    ],
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  );
}
