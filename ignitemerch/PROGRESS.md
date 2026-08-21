# IgniteMerch progress

Last saved: 2026-08-21. Branch: `master` at `ef9039a` plus this file. Dev: http://localhost:3005 (LAN http://192.168.0.219:3005). Postgres Docker host **5435**. Not pushed.

## Done

- Ten operator lots with real files in `content/kits/` (not `public/`). Checkout zips by token.
- Storefront, cart, checkout (Stripe or labeled Dev checkout), command (empty states, no fake KPIs).
- Desk chatbot + `/faq` (FAQPage JSON-LD). Answers from FAQ + Prisma only.
- SEO/GEO: canonicals, OG, Organization/WebSite/Product schema, robots (GPTBot/PerplexityBot on FAQ/shop/llms.txt), sitemap, `llms.txt`.
- WCAG 2.2 AA baseline: skip link, `/accessibility`, jsx-a11y, Playwright axe. Spec: `baseline/SITE-BASELINE.md`. Copy `.cursor/rules/*.mdc` onto other Elevate sites.
- Security: CSP/HSTS, command gated by `COMMAND_SECRET` in production, rate limits, Next **15.5.23**, Prisma **6.12.0**.
- Agents: visitor desk chat; `POST /api/agents/tick` merchandising signals only. Price/publish/refund stay on approval.

## Not done

- Playwright browsers (`npx playwright install`) then `npm run test:a11y`. Manual keyboard/screen-reader pass.
- Lighthouse CI. Replace `access@localhost` with a real intake email.
- Next 15 still bundles postcss/sharp advisories; do not force Next 16.
- Stripe live keys. Production `COMMAND_SECRET`. Domain + Search Console.
- Membership billing. Full Creative Director / video workers from the original 145-file spec.
- Portfolio retrofit (AlbertaWeb.Design and the rest) using `baseline/SITE-BASELINE.md`.

## Next session

1. Domain, Search Console, live Stripe, real access email.
2. Run the 90-day traffic plan in `MARKETING.md` (eat the UGC / shorts / outbound kits).
3. Copy the a11y rules onto the next client site.

Do not invent store metrics. Kits stay out of `public/`.
