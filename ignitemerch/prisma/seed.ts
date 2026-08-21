import {
  PrismaClient,
  ProductStatus,
  ProductType,
  AgentPermissionLevel,
} from "@prisma/client";
import { listKitFiles } from "../lib/kits";

const prisma = new PrismaClient();

async function main() {
  await prisma.download.deleteMany();
  await prisma.review.deleteMany();
  await prisma.orderItem.deleteMany();
  await prisma.order.deleteMany();
  await prisma.agentRun.deleteMany();
  await prisma.agentTask.deleteMany();
  await prisma.cartItem.deleteMany();
  await prisma.bundleItem.deleteMany();
  await prisma.bundle.deleteMany();
  await prisma.productAsset.deleteMany();
  await prisma.productVersion.deleteMany();
  await prisma.geoIndex.deleteMany();
  await prisma.product.deleteMany();
  await prisma.category.deleteMany();
  await prisma.membershipPlan.deleteMany();
  await prisma.agent.deleteMany();

  const categories = await Promise.all([
    prisma.category.create({
      data: {
        name: "Product Press",
        slug: "product-press",
        description: "ComfyUI desks that turn a phone photo into listing stills.",
      },
    }),
    prisma.category.create({
      data: {
        name: "Ad Floor",
        slug: "ad-floor",
        description: "UGC hooks, shot lists, and paid social scripts.",
      },
    }),
    prisma.category.create({
      data: {
        name: "Agent Line",
        slug: "agent-line",
        description: "n8n lines from lead to Stripe to download token.",
      },
    }),
    prisma.category.create({
      data: {
        name: "Desk Skills",
        slug: "desk-skills",
        description: "Cursor and Claude rules that keep client work on-scope.",
      },
    }),
    prisma.category.create({
      data: {
        name: "Short Form",
        slug: "short-form",
        description: "Faceless calendars and scripts for one niche.",
      },
    }),
    prisma.category.create({
      data: {
        name: "Listing Desk",
        slug: "listing-desk",
        description: "Shopify and Etsy copy that names one promise.",
      },
    }),
    prisma.category.create({
      data: {
        name: "Outbound",
        slug: "outbound",
        description: "Four-touch sequences for a named ICP.",
      },
    }),
    prisma.category.create({
      data: {
        name: "Brand Stamp",
        slug: "brand-stamp",
        description: "Identity recipes that survive a 20-SKU catalog.",
      },
    }),
  ]);

  const bySlug = Object.fromEntries(categories.map((c) => [c.slug, c]));

  const products = [
    {
      name: "Heat Press Photo Desk",
      slug: "heat-press-photo-desk",
      subtitle: "ComfyUI cutouts and lifestyle stills from a phone photo.",
      lotNumber: "01",
      coverTone: "ember",
      price: 49,
      compareAtPrice: 79,
      type: ProductType.WORKFLOW,
      featured: true,
      categoryId: bySlug["product-press"].id,
      researchNote:
        "Studio product photography still lists at $50–$200 per item, while AI listing variants land at cents when the real product stays in the frame. Etsy and Shopify sellers already batch this work. We sell the local ComfyUI desk, not another monthly photo app.",
      contents:
        "Cutout workflow JSON, lifestyle workflow JSON, four lighting recipes, batch shell script, marketplace QA checklist.",
      description:
        "Load the graph, drop a wall-shot photo, export a cutout and four lighting recipes. Built for sellers who are tired of lightbox apps and melted logos. Commercial license. Runs locally in ComfyUI 0.3+.",
      kit: "heat-press-photo-desk",
    },
    {
      name: "UGC Floor",
      slug: "ugc-floor",
      subtitle: "80 job-specific hooks and 15s/30s shot lists.",
      lotNumber: "02",
      coverTone: "rust",
      price: 79,
      compareAtPrice: 129,
      type: ProductType.CREATOR_KIT,
      featured: false,
      categoryId: bySlug["ad-floor"].id,
      researchNote:
        "Prompt packs that name a job outsell generic ChatGPT bundles by a wide margin. This floor is for UGC ads that sell a $29–$79 digital kit, with avatar screen time capped at 30%.",
      contents:
        "80-hook CSV, 15s and 30s shot lists, Meta primary text, synthetic-media compliance sheet.",
      description:
        "A floor, not a swipe file. Each hook has a platform, a price band, and the proof line that follows. Shot lists keep the face off-screen for 70% of the runtime.",
      kit: "ugc-floor",
    },
    {
      name: "n8n Lead-to-Stripe Line",
      slug: "n8n-lead-stripe",
      subtitle: "Qualify, checkout, mint a five-use download token.",
      lotNumber: "03",
      coverTone: "bone",
      price: 59,
      compareAtPrice: 99,
      type: ProductType.AUTOMATION_TEMPLATE,
      featured: false,
      categoryId: bySlug["agent-line"].id,
      researchNote:
        "A scrape of 3,000+ Gumroad prompt products found the $10–$19 band is a revenue dead zone, while n8n + multi-agent listings were still scarce versus 15,000+ ChatGPT prompt packs. Technical buyers import JSON and stay.",
      contents:
        "Three n8n workflows, qualifier prompt, test fixture, env example, Stripe error kill switch.",
      description:
        "Webhook in, ICP score, Stripe Checkout, download token out. Three graphs, one env file, a kill switch if Stripe fails three times in ten minutes.",
      kit: "n8n-lead-stripe",
    },
    {
      name: "Cursor Agency Skill Drop",
      slug: "cursor-agency-skill-drop",
      subtitle: "Rules that stop the agent inventing a second site.",
      lotNumber: "04",
      coverTone: "soot",
      price: 39,
      compareAtPrice: 59,
      type: ProductType.WORKFLOW,
      featured: false,
      categoryId: bySlug["desk-skills"].id,
      researchNote:
        "Generic prompt packs are crowded. Operator skill files (rules, skills, CLAUDE.md) are still an open shelf, and agencies already maintain them internally. This drop is that internal pack, cleaned for a client repo.",
      contents:
        "Always-on Cursor rules, QA skill, handoff skill, matching CLAUDE.md.",
      description:
        "Drop into a client repo. Locks scope, bans fake metrics, and makes the agent run a QA pass before it says done.",
      kit: "cursor-agency-skill-drop",
    },
    {
      name: "30-Day Faceless Shorts Desk",
      slug: "faceless-shorts-desk",
      subtitle: "A month of scripts for one niche: selling a kit.",
      lotNumber: "05",
      coverTone: "ember",
      price: 47,
      compareAtPrice: 67,
      type: ProductType.CREATOR_KIT,
      featured: false,
      categoryId: bySlug["short-form"].id,
      researchNote:
        "Creator packs sell when they are a desk (calendar, scripts, shots) rather than untitled prompt dumps. Faceless short-form remains a high-intent search for people who will not go on camera.",
      contents:
        "30-day CSV, 30 scripts, B-roll list, thumbnail patterns, one-pass repurpose sheet.",
      description:
        "Five posts a week. Each script is 18–28 seconds and points at one kit. No face. No fake view counts on the thumbnail.",
      kit: "faceless-shorts-desk",
    },
    {
      name: "Shopify Listing Stamp Pack",
      slug: "shopify-listing-stamp",
      subtitle: "Titles, bullets, and photo prompts for one promise.",
      lotNumber: "06",
      coverTone: "rust",
      price: 29,
      compareAtPrice: 45,
      type: ProductType.PROMPT_PACK,
      featured: false,
      categoryId: bySlug["listing-desk"].id,
      researchNote:
        "Healthy prompt revenue sits at $29–$49, not the $10–$19 dead zone. This pack is priced at the floor of that band and locked to one product type: downloadable operator kits.",
      contents:
        "24 title formulas, 8 bullet patterns, 12 photo prompts, SEO sheet, banned-phrase list.",
      description:
        "One listing, one promise. Titles respect marketplace character limits. Bullets say who it is for, what they run tonight, and who should skip it.",
      kit: "shopify-listing-stamp",
    },
    {
      name: "Outbound Sequence Press",
      slug: "outbound-sequence-press",
      subtitle: "Four touches. Email and LinkedIn. Named ICP.",
      lotNumber: "07",
      coverTone: "bone",
      price: 47,
      compareAtPrice: 79,
      type: ProductType.BUSINESS_KIT,
      featured: false,
      categoryId: bySlug["outbound"].id,
      researchNote:
        "Industry playbooks and sequences remain a top-selling digital category when they name the ICP. This press assumes you sell $47–$79 kits, not coaching retainers.",
      contents:
        "Four emails, four LinkedIn steps, ICP skip list, proof-line sheet, stop rules.",
      description:
        "70–110 words a touch. No “quick question.” Name the job and the file. Stop after two no’s or silence at touch four.",
      kit: "outbound-sequence-press",
    },
    {
      name: "Brand Stamp Kit",
      slug: "brand-stamp-kit",
      subtitle: "Mark, type, color, and cover recipes for one shop.",
      lotNumber: "08",
      coverTone: "soot",
      price: 59,
      compareAtPrice: 89,
      type: ProductType.WORKFLOW,
      featured: false,
      categoryId: bySlug["brand-stamp"].id,
      researchNote:
        "Midjourney image packs are a crowded, low-trust shelf. A locked identity system that survives 20 SKUs is what a shop needs next to product photography.",
      contents:
        "Stamp construction, type pairing, OKLCH tokens, six type-only covers, ComfyUI stamp texture graph.",
      description:
        "One display, one body, one mono. Ember is allowed to drench. Inter and glass cards are not.",
      kit: "brand-stamp-kit",
    },
    {
      name: "Support Agent Prompt Desk",
      slug: "support-agent-desk",
      subtitle: "57 prompts for refunds, licenses, and failed downloads.",
      lotNumber: "09",
      coverTone: "ember",
      price: 34,
      compareAtPrice: 49,
      type: ProductType.PROMPT_PACK,
      featured: false,
      categoryId: bySlug["desk-skills"].id,
      researchNote:
        "The same Gumroad study: “57 prompts for your customer support agent” beats “100 ChatGPT prompts.” $30–$49 is the band with better average sales than the $10 pile.",
      contents:
        "Refund, download, license, chargeback, and escalation prompts, plus 12 eval tickets.",
      description:
        "Each prompt has one placeholder and a worked reply. The agent may not invent an order ID or promise a refund the policy does not allow.",
      kit: "support-agent-desk",
    },
    {
      name: "Ignite Press Bundle",
      slug: "ignite-press-bundle",
      subtitle: "Photos, ads, fulfillment, and the agency skill drop.",
      lotNumber: "10",
      coverTone: "ember",
      price: 149,
      compareAtPrice: 226,
      type: ProductType.BUNDLE,
      featured: false,
      categoryId: bySlug["agent-line"].id,
      researchNote:
        "A $29 core with a higher bundle anchor converts better than a lone $19 pack. This bundle is the four desks that actually open a shop, priced under their separate total of $226.",
      contents:
        "Heat Press Photo Desk, UGC Floor, n8n Lead-to-Stripe Line, Cursor Agency Skill Drop.",
      description:
        "The shop-in-a-drop. Four kits, one license each, one install order. Run each graph alone before you wire them together.",
      kit: "ignite-press-bundle",
    },
  ];

  const created = [];
  for (const product of products) {
    const row = await prisma.product.create({
      data: {
        name: product.name,
        slug: product.slug,
        subtitle: product.subtitle,
        description: product.description,
        price: product.price,
        compareAtPrice: product.compareAtPrice,
        type: product.type,
        status: ProductStatus.PUBLISHED,
        version: "1.0.0",
        license: "commercial",
        commercialUse: true,
        aiCreated: true,
        featured: product.featured,
        lotNumber: product.lotNumber,
        coverTone: product.coverTone,
        researchNote: product.researchNote,
        contents: product.contents,
        categoryId: product.categoryId,
        views: 0,
        purchases: 0,
        revenue: 0,
        trendingScore: 0,
        conversionRate: 0,
        assets: {
          create: listKitFiles(product.kit).map((file) => ({
            name: file.name,
            fileType: file.fileType,
            sizeBytes: file.sizeBytes,
            url: file.relativePath,
          })),
        },
        versions: {
          create: {
            version: "1.0.0",
            changelog: "Full kit files, commercial license, zip after checkout.",
            assetUrl: product.kit,
          },
        },
      },
    });
    created.push(row);
  }

  const byProductSlug = Object.fromEntries(created.map((p) => [p.slug, p]));

  const bundle = await prisma.bundle.create({
    data: {
      name: "Ignite Press Bundle",
      slug: "ignite-press-bundle",
      description:
        "Heat Press Photo Desk, UGC Floor, n8n Lead-to-Stripe Line, and Cursor Agency Skill Drop.",
      price: 149,
      compareAtPrice: 226,
      featured: true,
    },
  });

  await prisma.bundleItem.createMany({
    data: [
      "heat-press-photo-desk",
      "ugc-floor",
      "n8n-lead-stripe",
      "cursor-agency-skill-drop",
    ].map((slug) => ({
      bundleId: bundle.id,
      productId: byProductSlug[slug].id,
    })),
  });

  await prisma.membershipPlan.create({
    data: {
      name: "Ignite Press Pass",
      stripePriceId: "price_press_pass_dev",
      price: 29,
      interval: "month",
      features:
        "One new lot each month, member price on the live drop, archive access to prior lots.",
    },
  });

  await prisma.agent.createMany({
    data: [
      {
        name: "Creative Director",
        model: "claude-sonnet-4-6",
        provider: "anthropic",
        permissionLevel: AgentPermissionLevel.NEEDS_APPROVAL,
        purpose: "Draft UGC scripts under the 30% avatar rule.",
      },
      {
        name: "Pricing Optimizer",
        model: "gpt-5.4",
        provider: "openai",
        permissionLevel: AgentPermissionLevel.NEEDS_APPROVAL,
        purpose: "Watch conversion vs views and propose price changes.",
      },
      {
        name: "GEO Indexer",
        model: "gpt-5.4-mini",
        provider: "openai",
        permissionLevel: AgentPermissionLevel.AUTONOMOUS,
        purpose: "Refresh llms.txt chunks when a product changes.",
      },
      {
        name: "Research Scout",
        model: "claude-sonnet-4-6",
        provider: "anthropic",
        permissionLevel: AgentPermissionLevel.READ_ONLY,
        purpose: "Flag demand signals. Cannot publish SKUs.",
      },
    ],
  });
}

main()
  .then(async () => {
    await prisma.$disconnect();
  })
  .catch(async (error: unknown) => {
    console.error(error);
    await prisma.$disconnect();
    process.exit(1);
  });
