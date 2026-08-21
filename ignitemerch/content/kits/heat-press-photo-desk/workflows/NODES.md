# Required nodes

Install from ComfyUI Manager or the Registry. Do not install random GitHub zips.

## Always
- ComfyUI 0.3+
- LoadImage, ImageScale, SaveImage (core)

## Cutout + lifestyle (recommended)
- BiRefNet background removal (ComfyUI core or the audited BiRefNet node)
- IC-Light V1 (`iclight_sd15_fc` or SDXL FC). V1 is commercial-ok. Skip V2.
- IPAdapter Plus (cubiq)
- ControlNet Auxiliary Preprocessors (Fannovel16)
- Ultimate SD Upscale (optional, 8GB+ VRAM)

## Models
- SD 1.5 or SDXL checkpoint you already use for product work
- `ip-adapter_sd15` or `ip-adapter_sdxl` matching the checkpoint
- CLIP-ViT-H-14 for IP-Adapter

If a node is missing, ComfyUI will mark it red. Install that node, then reload. Do not replace nodes with lookalikes.
