# Site baseline (copy onto every Elevate / AlbertaWeb.Design build)

This is the default layer for new sites, then a retrofit checklist for existing ones (AlbertaWeb.Design, Elevate Vision Digital, DB Enterprises / EV1Charger, Build with DB, Retrofit Network, BC Heat Pump Pros, ElectrifyHQ, Velvet Throne).

Do not treat an accessibility overlay as protection. Build WCAG 2.2 Level AA into source.

## Research notes (2026)

- W3C WCAG 2.2 is the current recommendation. Level AA is the usual organizational target.
- DOJ Title II (state/local government) currently requires WCAG **2.1** AA, with compliance dates extended into 2027/2028 depending on population. See [ADA.gov first steps](https://www.ada.gov/resources/web-rule-first-steps/).
- Title III (private businesses) still does not name one statute-level WCAG version. Courts and DOJ point to WCAG in practice. Meeting **2.2 AA** also meets 2.1 AA.
- Overlays do not replace semantic HTML, keyboard support, or contrast. Automated scans miss reading order, names, and modal focus.

## Accessibility (WCAG 2.2 AA)

Semantic HTML, skip link, keyboard, visible focus, labels, text errors, alt text, contrast, reduced motion, 44px primary targets, zoom allowed, captions/transcripts when video exists, `/accessibility` statement with a contact.

CI: `eslint-plugin-jsx-a11y` → axe-core / Playwright → Lighthouse accessibility. Fail the build on serious violations. Manual keyboard + screen reader before launch.

AI-builder prompt (paste into every handoff):

> Accessibility is a release requirement. Target WCAG 2.2 Level AA. Use semantic HTML, complete keyboard accessibility, visible focus states, appropriate ARIA, compliant contrast, descriptive form labels/errors, alt text, reduced-motion support, accessible dialogs/navigation, minimum 44px primary targets, and screen-reader-compatible dynamic content. Run automated axe and Lighthouse accessibility tests before production. Do not use an accessibility overlay as a substitute for accessible source code.

## Security

HSTS, CSP tailored to real origins, nosniff, frame deny, referrer, permissions-policy. No secrets in git. Paid files not in `/public`. Admin/command gated. Rate-limit chat and downloads. Stripe webhooks verified.

## SEO / GEO / AEO

- `robots.ts` + `sitemap.ts`. Canonical + description on every page. OG image from brand, not a fake photo.
- JSON-LD: Organization, WebSite, Product+Offer (true prices), FAQPage, BreadcrumbList. Never invent AggregateRating.
- GEO: `llms.txt` / `llms-full.txt`, FAQ answers that can be quoted, allow retrieval bots on those URLs.
- AEO: FAQ page plus a chatbot that **only** answers from FAQ + live catalog.

## Agents

Customer chatbot: retrieval over FAQ and products. No invented metrics.
Ops tick: trending + GEO index + pricing **signals**, kill switch on spend. Publish/price/refund stay on approval.

## Retrofit order

1. Copy this folder’s rules into `.cursor/rules/`.
2. Add skip link, statement page, FAQ, robots/sitemap, CSP.
3. Move downloads behind tokens.
4. Gate admin.
5. Turn on jsx-a11y + axe.
6. Manual keyboard pass.
