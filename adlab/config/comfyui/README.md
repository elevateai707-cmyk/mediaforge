# Your ComfyUI workflows

Export each from ComfyUI with **Workflow → Export (API)** / "Save (API Format)":

| File | Used for |
|---|---|
| `image.json` | `generated_image` shots (and `generated_video` when there's no `video.json`, with a slow push-in) |
| `video.json` | `generated_video` shots |

Then replace the values AdLab should fill with these placeholders (a string that is exactly
one placeholder takes that value's type, so `"{{width}}"` becomes the number 768):

| Placeholder | Value |
|---|---|
| `{{prompt}}` | the shot's visual, from the brief |
| `{{negative}}` | no people/faces/text/logos/brand names (see `producer.NEGATIVE`) |
| `{{width}}` `{{height}}` | 768x1344 (9:16), 896x1120 (4:5), 1024x1024 (1:1) |
| `{{seed}}` | stable per brief + shot, so re-runs match |
| `{{frames}}` `{{fps}}` `{{seconds}}` | video length (`ADLAB_GEN_FPS`, default 16) |
| `{{filename_prefix}}` | `adlab_<brief>_s<shot>_<ratio>` |

Unknown placeholders fail loudly. Check the filled payloads with `adlab produce --dry-run`.
