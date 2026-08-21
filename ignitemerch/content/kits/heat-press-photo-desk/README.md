# Heat Press Photo Desk
Version 1.0.0 · Commercial license

A ComfyUI desk for ecommerce product shots. Built for sellers who currently pay studio rates or lose listings to weak photos.

## Why this SKU
Professional product photography still runs $50–$200 per item. AI background and lifestyle variants land at cents per image when you keep the real product in the frame. This kit is the local workflow, not a SaaS subscription.

## You get
1. `workflows/heat-press-cutout.json` — load in ComfyUI, drop a phone photo, get a marketplace cutout.
2. `workflows/heat-press-lifestyle.json` — same product, four lighting recipes (linen, tungsten loft, outdoor overcast, white cyclorama).
3. `recipes/lighting.md` — camera height, key/fill ratios, and negative prompts that stop melted logos.
4. `scripts/batch.sh` — folder-in, folder-out for 20+ SKUs.
5. `qa-checklist.md` — Amazon / Shopify / Etsy crop and background rules.

## How to run tonight
1. Open ComfyUI 0.3 or later.
2. Load `workflows/heat-press-cutout.json`.
3. Drop a photo shot against a plain wall. Do not use a busy room.
4. Render. If the product warps, lower denoise to 0.35 and lock the mask.
5. Run the lifestyle workflow only after the cutout is clean.

## Lighting recipes
- **Linen table:** key 45°, fill at 30%, no colored gels.
- **Tungsten loft:** 3200K, practical lamp in frame, shadow kept under the object.
- **Overcast:** high diffusion, almost no specular.
- **Cyclorama:** pure #f4f4f1, centered, 1:1 and 4:5 crops.

## What this is not
It will not invent a product you did not photograph. Ghost jewelry and melted type mean your source photo is too small. Reshoot.

## License
Commercial use on your own listings. Do not resell the JSON graphs as a competing pack.
