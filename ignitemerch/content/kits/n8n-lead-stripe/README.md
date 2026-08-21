# n8n Lead-to-Stripe Agent Line
Version 1.0.0 · Commercial license

A production-shaped n8n line: form or DM in, qualified lead, Stripe Checkout out, download token issued.

## Why this SKU
Gumroad is packed with generic ChatGPT prompts and thin on n8n + agent orchestration. Technical buyers pay more and actually import workflows.

## You get
1. `n8n/lead-qualify.json` — webhook, spam checks, ICP score.
2. `n8n/checkout-dispatch.json` — Stripe Checkout Session for one SKU.
3. `n8n/fulfill-download.json` — on `checkout.session.completed`, mint a 5-use download token.
4. `prompts/qualifier.md` — the single qualifier prompt with `{placeholders}`.
5. `env.example` — Stripe, Redis, and mail keys.

## Import
1. n8n 1.70 or later.
2. Import the three JSON files as separate workflows.
3. Set credentials. Do not commit them.
4. Run the test payload in `fixtures/lead.json`.
5. Confirm a Checkout URL returns before you connect a public form.

## Kill switch
If Stripe errors 3 times in 10 minutes, the qualify workflow stops enqueueing. Fix keys, then click Active.

## License
Run inside your company. Do not list these graphs on a template marketplace.
