# Lighting recipes

Run lifestyle only after the cutout is clean. Same seed across a catalog.

## Camera
- Height: just above object center. Jewelry: 15° down. Apparel mock: eye level.
- Distance: fill 80% of frame. Leave crop room for 1:1 and 4:5.
- Source photo: 2000px on the long edge minimum. JPEG from a phone is fine. Screenshots of screenshots are not.

## Linen table
- Key 45°, fill 30%, no gels.
- Positive: `professional product photo, natural linen, 45 degree key, soft fill, catalog still, sharp label, even whites`
- Negative: see `negatives.md`
- Denoise: 0.28–0.35

## Tungsten loft
- 3200K. One practical lamp in frame. Shadow under the object, not across the logo.
- Positive: `tungsten loft, 3200K practical lamp, warm wood desk, product sharp, premium still, no neon`
- Denoise: 0.32

## Overcast
- High diffusion, almost no specular.
- Positive: `overcast daylight, high diffusion, soft shadow, outdoor table, true color, no bloom`
- Denoise: 0.30

## Cyclorama
- Hex `#f4f4f1`. Centered. Export 1:1 and 4:5.
- Positive: `seamless cyclorama, hex f4f4f1, centered product, ecommerce cut-in, no floor seam, no prop`
- Denoise: 0.25

## IC-Light
- Model: V1 FC (commercial). Not V2.
- Steps 20–25, CFG 7–8, sampler `dpmpp_2m`, scheduler `karras`.
- IP-Adapter weight 0.6–0.8. Above 1.0 distorts the SKU.

## Consistency
Lock seed + checkpoint + recipe name in the filename: `sku-linen-seed441.png`.
