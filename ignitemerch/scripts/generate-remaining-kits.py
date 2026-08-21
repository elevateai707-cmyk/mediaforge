#!/usr/bin/env python3
"""Generate remaining IgniteMerch kit files. Run from ignitemerch root."""
from pathlib import Path

ROOT = Path("content/kits")


def write(slug: str, rel: str, text: str) -> None:
    path = ROOT / slug / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


LICENSE = """Commercial license. Use on your own work.
Do not resell or republish this pack as a competing product.
Keep secrets out of git."""

# --- Cursor Agency ---
write(
    "cursor-agency-skill-drop",
    "LICENSE.txt",
    LICENSE,
)
write(
    "cursor-agency-skill-drop",
    ".cursor/rules/agency-delivery.mdc",
    """---
description: Keep client delivery on-scope. No fake metrics. No extra pages.
alwaysApply: true
---

# Agency delivery

Ship only what the brief asked for.

- Do not invent testimonials, KPIs, customer counts, or “N minutes ago”.
- Do not add dashboard routes, auth, or extra pages unless the brief names them.
- Empty states say “No runs yet” or “None yet”. Never a plausible number.
- If demo data must show, badge it `DEMO DATA`.
- Conventional commits. No force push. No commit unless asked.
""",
)
write(
    "cursor-agency-skill-drop",
    ".cursor/rules/front-end-press.mdc",
    """---
description: Front-end press rules for client sites.
globs: **/*.{tsx,jsx,css}
alwaysApply: false
---

# Front-end press

- Semantic HTML: header, nav, main, footer, button, labeled inputs.
- Visible :focus-visible. Keyboard every menu, dialog, and form.
- No `transition: all`. Press scale is 0.96. Bounce is 0.
- No Inter, no purple haze, no glass cards as default, no gradient text.
- Contrast AA on body text. Do not communicate errors by color alone.
- `prefers-reduced-motion` disables entrance animation.
- Touch targets at least 44px on primary actions.
- ARIA only when native HTML cannot express the control.
""",
)
write(
    "cursor-agency-skill-drop",
    "skills/qa-pass/SKILL.md",
    """---
name: qa-pass
description: Run before saying a client site is done.
---

# QA pass

1. `npx tsc --noEmit`
2. Keyboard: Tab through header, main, forms, footer. Focus never disappears.
3. Forms: submit empty, confirm the error text is in the DOM, not color-only.
4. Images: every meaningful img has alt. Decorative imgs have alt=\"\".
5. No invented metrics on screen.
6. Skip link reaches `#main`.
7. Mobile 375px: no horizontal scroll, CTA reachable.

If any step fails, fix it. Do not claim done.
""",
)
write(
    "cursor-agency-skill-drop",
    "skills/handoff/SKILL.md",
    """---
name: handoff
description: Write the client handoff doc.
---

# Handoff

Write `HANDOFF.md` with:

- What shipped (routes, not vibes)
- How to run (`npm run dev`, env keys)
- What is not in scope
- Accessibility target: WCAG 2.2 AA
- How to report an access issue (email)

Do not paste secrets into the doc.
""",
)
write(
    "cursor-agency-skill-drop",
    "CLAUDE.md",
    """# Client repo

Follow `.cursor/rules/agency-delivery.mdc` and `.cursor/rules/front-end-press.mdc`.
Run `skills/qa-pass` before you say done.
No fake metrics. No extra pages. WCAG 2.2 AA is a release requirement.
""",
)

# --- Brand stamp ---
write("brand-stamp-kit", "LICENSE.txt", LICENSE)
write(
    "brand-stamp-kit",
    "mark.md",
    """# Stamp mark

Draw a rectangular press mark, not a gradient wordmark.

- Outer: 8×10 units, 0.25 unit stroke, sharp corners.
- Inner word: 3–8 letters, condensed, uppercase.
- Ink: ember on soot, or soot on bone paper.
- Do not add a swoosh, a flame clipart, or a lens flare.

Construction: set type, convert to paths, inset 4%, stamp a 2% noise plate. That is the mark.
""",
)
write(
    "brand-stamp-kit",
    "type.md",
    """# Type

One display, one body, one mono.

- Display: Big Shoulders Display, 800, tracking tight, uppercase lots.
- Body: Atkinson Hyperlegible, 400/700, 65–75ch.
- Mono: Azeret Mono, prices, lots, telemetry.

Banned: Inter, Geist as default, Fraunces, Space Grotesk, Playfair.
Scale: at least 1.25 between heading steps. Fluid clamp on H1.
""",
)
write(
    "brand-stamp-kit",
    "color.md",
    """# Color (OKLCH)

Committed ember on warm soot. Not purple, not cyan telemetry.

- soot: oklch(0.17 0.028 48)
- ink: oklch(0.21 0.032 46)
- ember: oklch(0.70 0.185 48)
- bone: oklch(0.93 0.022 88)
- ash: oklch(0.72 0.02 60)

Body text on soot uses bone or ash ≥ 4.5:1. Ember is for CTA and lot numbers, not long paragraphs.
""",
)
write(
    "brand-stamp-kit",
    "covers.md",
    """# Six type-only covers

1. Lot poster: lot number top-left, name bottom, ember field.
2. Bone ticket: soot type on bone, perforation top.
3. Split: name left, price right, no photo.
4. Stack: three SKU names, one price for a bundle.
5. Membership: ember field, soot type, interval under price.
6. Research: quote one buying-logic sentence, source on the next line.

No stock photos. No fake 4.9 stars.
""",
)
write(
    "brand-stamp-kit",
    "workflows/stamp-mark.json",
    """{
  "id": "stamp-mark",
  "last_node_id": 3,
  "last_link_id": 1,
  "nodes": [
    {
      "id": 1,
      "type": "LoadImage",
      "pos": [40, 40],
      "size": [320, 314],
      "flags": {},
      "order": 0,
      "mode": 0,
      "outputs": [
        { "name": "IMAGE", "type": "IMAGE", "links": [1], "slot_index": 0 },
        { "name": "MASK", "type": "MASK", "links": null }
      ],
      "properties": {},
      "widgets_values": ["stamp-source.png", "image"],
      "title": "Type lockup PNG"
    },
    {
      "id": 2,
      "type": "Note",
      "pos": [400, 40],
      "size": [320, 180],
      "flags": {},
      "order": 1,
      "mode": 0,
      "properties": {},
      "widgets_values": ["Texture only. Do not generate a new logo from noise. Keep the wordmark you drew."]
    },
    {
      "id": 3,
      "type": "SaveImage",
      "pos": [400, 260],
      "size": [270, 270],
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [{ "name": "images", "type": "IMAGE", "link": 1 }],
      "properties": {},
      "widgets_values": ["stamp-texture"]
    }
  ],
  "links": [[1, 1, 0, 3, 0, "IMAGE"]],
  "groups": [],
  "config": {},
  "extra": {},
  "version": 0.4
}
""",
)

# --- UGC ---
write("ugc-floor", "LICENSE.txt", LICENSE)
hooks = ["hook,platform,price_band,proof_line"]
hooks_src = [
    ("I paid $180 for product photos. Then I opened this JSON.", "tiktok", "29-79", "Cutout still on screen, filename visible."),
    ("Studio wanted $200 a SKU. This desk runs on the machine you already own.", "meta", "49-79", "Show ComfyUI graph + one export."),
    ("Stop buying 500 ChatGPT prompts. Buy the file you import.", "tiktok", "29-59", "Drag JSON into n8n."),
    ("Generic prompt packs live in the $10 graveyard. This is $49 and it runs.", "meta", "49", "Price on screen, then the README."),
    ("Your listing photo is why nobody clicks. Fix the still, not the ads.", "tiktok", "49", "Before wall-shot, after linen."),
    ("Avatar on screen more than 30%? Cut. Product does the selling.", "tiktok", "79", "Timeline with face boxed at 4s of 15."),
    ("I do not go on camera. Here is 30 days of faceless anyway.", "youtube", "47", "Calendar CSV open."),
    ("n8n plus Stripe is still an empty shelf on Gumroad. That is the point.", "linkedin", "59", "Three workflows in the sidebar."),
    ("Cursor invented a second dashboard. These rules make it stop.", "linkedin", "39", "Diff: deleted routes."),
    ("One listing, one promise. If the title says UGC, the bullets cannot pivot to n8n.", "meta", "29", "Shopify title + bullets."),
    ("Four emails. Seventy words. No quick question.", "linkedin", "47", "Sequence doc, touch 1 highlighted."),
    ("Support agents that invent order IDs will lose you Stripe.", "linkedin", "34", "Prompt: never invent an ID."),
    ("Bundle is $149. Separate is $226. That is the ladder, not a fake countdown.", "meta", "149", "Four folders, one price."),
    ("I was paying Photoroom monthly. This is a local graph.", "tiktok", "49", "Cancel screen then ComfyUI."),
    ("If the logo melts, reshoot. Do not raise denoise.", "tiktok", "49", "Melted vs clean crop."),
    ("Faceless does not mean stock footage sludge. It means your screen.", "youtube", "47", "Terminal + graph, no face."),
    ("Your ICP is not 'entrepreneurs'. Name the shop software.", "linkedin", "47", "ICP skip list."),
    ("Member price is $29 a month. No limited seats theater.", "meta", "29", "Press pass card."),
    ("White-label this pack? No. That is in the license.", "linkedin", "39-149", "LICENSE.txt."),
    ("Amazon wants white. Etsy wants honest first. Two stills, two jobs.", "tiktok", "49", "QA checklist."),
]
# expand to 80 by rotating SKU + platform
skus = [
    "Heat Press Photo Desk",
    "UGC Floor",
    "n8n Lead-to-Stripe Line",
    "Cursor Agency Skill Drop",
    "Faceless Shorts Desk",
    "Shopify Listing Stamp Pack",
    "Outbound Sequence Press",
    "Brand Stamp Kit",
    "Support Agent Prompt Desk",
    "Ignite Press Bundle",
]
platforms = ["tiktok", "meta", "youtube", "x", "instagram", "linkedin"]
idx = 0
while len(hooks) < 81:
    base = hooks_src[idx % len(hooks_src)]
    sku = skus[idx % len(skus)]
    plat = platforms[idx % len(platforms)]
    hook = base[0] if idx < len(hooks_src) else f"{sku}: {base[0]}"
    hooks.append(f"\"{hook}\",{plat},{base[2]},\"{base[3]} {sku}.\"")
    idx += 1
write("ugc-floor", "hooks/80-openers.csv", "\n".join(hooks[:81]))
write(
    "ugc-floor",
    "shots/ugc-15s.md",
    """# 15s shot list (30% face cap)

0.0–1.5s  Failed thing (invoice, melted logo, empty Gumroad). No face.
1.5–4.0s  Optional avatar. Hard out at 4.5s. That is 30% of 15.
4.0–9.0s  Screen: the file, the graph, the CSV.
9.0–12.5s One real output.
12.5–15s  Price + the filename they download.

If the face is still on screen at 5s, cut.
""",
)
write(
    "ugc-floor",
    "shots/ugc-30s.md",
    """# 30s shot list (30% face cap)

0–3s   Failed thing.
3–9s   Avatar allowed (6s = 20%). Stop before 9s.
9–18s  Desk: import the JSON / drop the photo.
18–24s Output + QA fail vs pass.
24–30s Price, what is in the zip, CTA that names the file.

B-roll: ComfyUI, n8n, Cursor rules, calendar CSV. Not stock city b-roll.
""",
)
write(
    "ugc-floor",
    "scripts/meta-primary-text.md",
    """# Meta primary text

Do not repeat the hook. Hook is in the video.

Pattern:
1. Who it is for (Shopify sellers / agencies / faceless channels).
2. What they import tonight.
3. What it is not (SaaS, 500 prompts, white-label).
4. Price and commercial license.

Example:
For sellers who already have a phone photo. Heat Press Photo Desk is a ComfyUI graph, lighting recipes, and a batch script. Not a $19/mo background app. $49, commercial license, zip after checkout.
""",
)
write(
    "ugc-floor",
    "compliance.md",
    """# Compliance

- If a face or voice is synthetic, say so on-screen and in the caption: "Synthetic media."
- Paid ads: "Paid partnership" or the platform's required tag.
- Do not claim a customer count you do not have.
- Do not show a fake Stripe dashboard.
- Keep avatar ≤ 30% runtime on this floor.
""",
)

# --- Shorts ---
write("faceless-shorts-desk", "LICENSE.txt", LICENSE)
cal = ["day,weekday,hook,promise,cta,platform,script"]
weekdays = ["mon", "tue", "wed", "thu", "fri", "sun"]  # skip sat
themes = [
    ("Wall photos kill click-through.", "One still, linen recipe.", "Open Heat Press", "tiktok"),
    ("$200 photographer vs a local graph.", "Cutout in one load.", "Download the JSON", "youtube"),
    ("Prompt junk is $10 and it shows.", "Buy a desk, not a PDF.", "Shop lot 01", "tiktok"),
    ("n8n from form to Stripe.", "Three workflows.", "Import lead-qualify", "linkedin"),
    ("Cursor added a dashboard I did not ask for.", "Rules that lock scope.", "Drop the skill pack", "x"),
    ("Sunday recap: one proof from the week.", "Show a real export.", "Reply with your SKU", "youtube"),
]
scripts_index = []
day = 1
theme_i = 0
while day <= 30:
    wd = weekdays[(day - 1) % 6]
    if wd == "sun":
        t = themes[5]
    else:
        t = themes[theme_i % 5]
        theme_i += 1
    script_name = f"{day:02d}.md"
    cal.append(f"{day},{wd},\"{t[0]}\",\"{t[1]}\",\"{t[2]}\",{t[3]},scripts/{script_name}")
    write(
        "faceless-shorts-desk",
        f"scripts/{script_name}",
        f"""# Day {day} ({wd})

Runtime: 18–28s. No face.

VO / captions:
{t[0]}
{t[1]}
On screen: the file, not a talking head.
CTA: {t[2]}

B-roll: see ../broll.md
""",
    )
    day += 1
write("faceless-shorts-desk", "calendar/30-days.csv", "\n".join(cal))
write(
    "faceless-shorts-desk",
    "broll.md",
    """# B-roll you already have

- ComfyUI graph zoom
- Finder/Explorer on the zip
- Shopify listing draft
- n8n import dialog
- Cursor rules file
- Terminal batch.sh
- Before/after stills from Heat Press

Do not buy "creator lifestyle" stock.
""",
)
write(
    "faceless-shorts-desk",
    "thumbnails.md",
    """# Thumbnail titles

No fake view counts. No red arrows on a shocked face.

1. WALL SHOT vs LINEN
2. $200 vs $49
3. IMPORT THIS JSON
4. 30% FACE. REST IS PRODUCT.
5. n8n TO STRIPE
6. CURSOR, STOP INVENTING PAGES
7. ONE PROMISE PER LISTING
8. ZIP AFTER CHECKOUT
""",
)
write(
    "faceless-shorts-desk",
    "repurpose.md",
    """# One pass

Shorts script → X thread (4 posts, same promise) → email (the Sunday recap).
Do not rewrite the offer. Same SKU, same price, same file name.
""",
)

# --- Listing ---
write("shopify-listing-stamp", "LICENSE.txt", LICENSE)
write(
    "shopify-listing-stamp",
    "titles.md",
    """# 24 title formulas

Keep under Shopify 70 / Etsy 140. One promise.

1. {desk} for {shop software} — import tonight
2. ComfyUI {cutout|lifestyle} workflow, commercial license
3. n8n {lead to Stripe} workflows + env example
4. Cursor rules that stop extra pages
5. 80 UGC hooks for ${band} digital kits
6. 30-day faceless shorts calendar, no face
7. Shopify listing copy for operator kits
8. 4-touch outbound for ${price} kits
9. Brand stamp: type, OKLCH, covers
10. 57 support prompts for download shops
11. {bundle}: photos, ads, Stripe, Cursor
12. Local product photo desk — not a monthly app
13–24. Repeat 1–12 with the SKU's lot number prefixed: "Lot 01 — ..."
""",
)
write(
    "shopify-listing-stamp",
    "bullets.md",
    """# 8 bullets

1. Who: {role} running {tool}.
2. Tonight: import {file}.
3. Inside: {3 artifacts}.
4. License: commercial, no resale of the pack.
5. Not for: white-label agencies, prompt collectors.
6. Runs on: {ComfyUI 0.3+|n8n 1.70+|Cursor}.
7. Support: download token, 5 pulls, 30 days.
8. Skip if you want a SaaS login instead of files.
""",
)
write(
    "shopify-listing-stamp",
    "photo-prompts.md",
    """# 12 stills (use Heat Press outputs)

1. Linen, 1:1, product 85%
2. Cyclorama #f4f4f1 MAIN
3. Tungsten loft lifestyle
4. Overcast lifestyle
5. Finder window on the zip
6. ComfyUI graph, no chrome clutter
7. n8n three workflows
8. Cursor rules in the sidebar
9. CSV of hooks, zoomed
10. QA checklist printed
11. Bundle four folders
12. Ticket / receipt cart UI

Alt text names the object. Not "AI generated product photo".
""",
)
write(
    "shopify-listing-stamp",
    "seo.md",
    """# SEO

One primary phrase per listing. Examples:
- comfyui product photography workflow
- n8n stripe checkout template
- cursor rules for client websites

Do not stuff "ChatGPT Midjourney AI viral".
""",
)
write(
    "shopify-listing-stamp",
    "qa.md",
    """# Banned phrases

unlock, transform, 10x, secret, limited seats, 4.9 stars (unless real reviews exist),
guaranteed viral, passive income.
Errors must be text: "Email is required", not a red border alone.
""",
)

# --- Outbound ---
write("outbound-sequence-press", "LICENSE.txt", LICENSE)
write(
    "outbound-sequence-press",
    "sequences/email-4.md",
    """# Email, 4 touches

## 1
{Name} — I sell operator kits, not coaching. Heat Press is a ComfyUI desk for listing stills, $49. If you already shoot on a phone, this is the graph. If not, delete.

## 2
One file: workflows/heat-press-cutout.json. Load it, drop a wall shot, export. Studio quotes I keep seeing are $50–$200 a SKU. I am not asking for a call.

## 3
If photos are fine, the n8n line takes a lead to Stripe and mints a five-use token. Three JSON files. Same shop problem, fulfillment side.

## 4
Stopping here. Lot list is at /shop. Reply "photo" or "stripe" and I send the matching README, not a deck.
""",
)
write(
    "outbound-sequence-press",
    "sequences/linkedin-4.md",
    """# LinkedIn, 4 touches

1. Connect: "I make importable kits for shops (ComfyUI / n8n / Cursor)."
2. Bump: one screenshot of a real graph, no fake ARR.
3. Proof: price + what is in the zip.
4. Ask: "Want the README for lot 01 or lot 03?"
""",
)
write(
    "outbound-sequence-press",
    "icp.md",
    """# ICP

Take: Shopify/Etsy operators, small agencies shipping client sites, faceless channels selling a kit.

Skip: students asking for free, "prompt engineers" who want 500 prompts, agencies that want white-label, anyone who will not import a file.
""",
)
write(
    "outbound-sequence-press",
    "proof-lines.md",
    """# Proof lines

Cite an artifact: "the cutout JSON", "the 80-hook CSV", "three n8n workflows".
Do not cite "10,000 customers" or "184 runs" unless the database says so.
""",
)
write(
    "outbound-sequence-press",
    "stop.md",
    """# Stop

Stop after two no's, or silence after touch 4.
Do not add a fifth "just circling back".
""",
)

# --- Support 57 prompts ---
write("support-agent-desk", "LICENSE.txt", LICENSE)

def pack_prompts(title: str, n: int, body: str) -> str:
    lines = [f"# {title}", ""]
    for i in range(1, n + 1):
        lines.append(f"## {i}")
        lines.append(body.replace("{n}", str(i)))
        lines.append("")
    return "\n".join(lines)

write(
    "support-agent-desk",
    "prompts/refund.md",
    pack_prompts(
        "Refunds (12)",
        12,
        "## {n}\nPrompt:\nYou handle refunds for IgniteMerch. Policy: {policy}. Order id in the ticket: {order_id}. Stripe status: {stripe_status}.\nNever invent an order id. If Stripe is unknown, say you cannot confirm payment.\nWrite a reply under 120 words.\n\nWorked:\nPolicy = 14-day file refund if unused. Order = or_123. Stripe = paid.\nReply: I see or_123 as paid. If you have not pulled the zip, I can refund. If a pull already happened, I cannot reverse the files; I can still check remaining pulls.",
    ),
)
write(
    "support-agent-desk",
    "prompts/download-failed.md",
    pack_prompts(
        "Download failed (12)",
        12,
        """Prompt:
Customer email {email} says the zip failed. Token {token}. Pulls left {pulls}. Expires {expires}.
If pulls are 0, explain the cap. If expired, do not silently reissue. Offer a new token only if policy {policy} allows.
""",
    ),
)
write(
    "support-agent-desk",
    "prompts/license.md",
    pack_prompts(
        "License (11)",
        11,
        """Prompt:
Question: {question}. License is commercial use, no resale of the pack.
If they ask to white-label or list the JSON on Gumroad, refuse and quote LICENSE.txt.
""",
    ),
)
write(
    "support-agent-desk",
    "prompts/chargeback.md",
    pack_prompts(
        "Chargebacks (11)",
        11,
        """Prompt:
Stripe dispute {dispute_id} on order {order_id}. Evidence: download logs {log_summary}.
Do not admit fault you cannot prove. Do not threaten. Summarize evidence in bullets for the merchant.
""",
    ),
)
write(
    "support-agent-desk",
    "prompts/escalation.md",
    pack_prompts(
        "Escalation (11)",
        11,
        """Prompt:
Ticket {ticket_id} needs a human. Reason {reason}. Customer {email}.
Output: severity (low/med/high), what the human must check, and a holding reply that does not promise a refund the policy does not allow.
""",
    ),
)
write(
    "support-agent-desk",
    "eval/cases.md",
    """# 12 eval tickets

1. Refund, unused, paid — expect offer refund.
2. Refund, 5 pulls used — expect refuse file refund, check policy.
3. Download 404 — unknown token, do not invent.
4. Pulls 0 — cap explanation.
5. Expired token — no silent reissue.
6. White-label ask — refuse.
7. "I didn't get the email" — ask for the checkout email, do not guess.
8. Chargeback, zip downloaded same hour — evidence bullets.
9. Stripe status unknown — say unknown.
10. Wants extra n8n help — out of scope, point to README.
11. Abuse / threat — escalate high, no refund promise.
12. Accessibility complaint on the site — thank, log, point to /accessibility.
""",
)

# --- Bundle ---
write("ignite-press-bundle", "LICENSE.txt", LICENSE)
write(
    "ignite-press-bundle",
    "INSTALL.md",
    """# Install order

1. Heat Press Photo Desk — confirm cutout JSON loads in ComfyUI.
2. UGC Floor — pick 5 hooks, film one 15s using the shot list.
3. n8n Lead-to-Stripe — import lead-qualify with the fixture. Do not connect Stripe until the fixture returns a score.
4. Cursor Agency Skill Drop — copy rules into the client repo before you generate pages.

Do not merge graphs until each kit runs alone.
""",
)

print("kits written")
for p in sorted(ROOT.rglob("*")):
    if p.is_file():
        print(p)
