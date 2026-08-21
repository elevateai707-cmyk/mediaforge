export interface FaqItem {
  id: string;
  question: string;
  answer: string;
  href?: string;
}

export const FAQS: FaqItem[] = [
  {
    id: "what-is",
    question: "What is IgniteMerch?",
    answer:
      "A merch press for operator kits: ComfyUI photo desks, UGC ad floors, n8n Stripe lines, and Cursor rules. You buy files you import tonight, not a monthly photo app.",
    href: "/shop",
  },
  {
    id: "prices",
    question: "Why are kits $29 to $149 instead of $9?",
    answer:
      "Generic $10–$19 prompt packs are a revenue dead zone. These lots are job-specific desks in the $29–$79 band, with a $149 bundle under the $226 separate total.",
    href: "/#research",
  },
  {
    id: "download",
    question: "How do downloads work?",
    answer:
      "After checkout you get a ticket token. Each lot zips as one archive, five pulls, 30 days. Kits are not sitting in a public folder.",
    href: "/checkout",
  },
  {
    id: "license",
    question: "Can I resell the pack or white-label it?",
    answer:
      "No. Commercial use on your own listings, ads, support, and client work is allowed. Relisting the JSON or markdown as a competing product is not.",
  },
  {
    id: "stripe",
    question: "Is Stripe required locally?",
    answer:
      "No. If Stripe keys are empty, checkout records the order locally and is labeled Dev checkout. Add STRIPE_SECRET_KEY for live payment.",
    href: "/checkout",
  },
  {
    id: "comfy",
    question: "Which ComfyUI version does Heat Press need?",
    answer:
      "ComfyUI 0.3 or later. Load heat-press-cutout.json first. Install BiRefNet and IC-Light V1 for the full lifestyle path. IC-Light V2 is not for commercial listings.",
    href: "/product/heat-press-photo-desk",
  },
  {
    id: "n8n",
    question: "How do I import the n8n line?",
    answer:
      "n8n 1.70 or later. Import the three JSON files as separate workflows. Run fixtures/lead.json before you connect Stripe. If Stripe errors three times in ten minutes, stop the qualify workflow.",
    href: "/product/n8n-lead-stripe",
  },
  {
    id: "access",
    question: "How do I report an accessibility issue?",
    answer:
      "Email the contact on the accessibility statement. We target WCAG 2.2 Level AA in source code. We do not use an overlay widget as a substitute.",
    href: "/accessibility",
  },
  {
    id: "agents",
    question: "Do AI agents run the storefront by themselves?",
    answer:
      "A desk chatbot answers from this FAQ and the live catalog. Command agents can tick merchandising when COMMAND_SECRET is set. Publish, price, and campaign dispatch stay on approval. Command is not public.",
    href: "/faq",
  },
  {
    id: "refund",
    question: "What is the refund policy?",
    answer:
      "If you have not pulled the zip, ask for a refund on the ticket email. If pulls already happened, the files cannot be reversed. Do not invent order IDs in chat; use the email from checkout.",
  },
];

export function findFaqMatches(query: string, limit = 3) {
  const tokens = tokenize(query);
  if (tokens.length === 0) return FAQS.slice(0, limit);
  return FAQS.map((item) => ({
    item,
    score: scoreText(`${item.question} ${item.answer}`, tokens),
  }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((row) => row.item);
}

export function tokenize(value: string) {
  return value
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((token) => token.length > 2);
}

export function scoreText(haystack: string, tokens: string[]) {
  const hay = haystack.toLowerCase();
  return tokens.reduce((sum, token) => sum + (hay.includes(token) ? 1 : 0), 0);
}
