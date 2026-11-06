# Segmentation service protocol (`segmentation_backend: http`)

Use this to plug in your own segmentation model, such as SAM 2, a depth model or a
custom U-Net, instead of the built-in method. PaperCraft sends the image and the
layer plan. Your service returns one label map. Cleanup, hidden-area fill, assets,
masks and `layers.json` are then produced exactly as with the built-in backend.

## Configuration

```yaml
plugins:
  entries:
    papercraft-3d:
      settings:
        segmentation_backend: http
        segmentation_url: http://127.0.0.1:8765/segment   # or https://your-host/segment
        segmentation_timeout_s: 300
```

Only `https://` URLs are accepted, plus `http://` on `localhost`, `127.0.0.1` or
`::1` for a service running on your own machine. The image is sent to that
URL and nowhere else.

## Request

`POST <segmentation_url>` with `Content-Type: application/json`:

```json
{
  "version": "1",
  "image": { "mime": "image/png", "data": "<base64 of the original file>", "width": 1672, "height": 941 },
  "layers": [
    { "id": "sunset_sky", "name": "...", "role": "sky", "order": 0,
      "description": "what belongs to this layer", "bbox": [0, 0, 1, 0.42] }
  ]
}
```

`layers` comes from `analysis.json`, sorted by `order` (0 = farthest). `bbox` is
normalised `[x0, y0, x1, y1]` with the origin at the top left.

## Response

HTTP 200, JSON:

```json
{
  "labelMap": { "mime": "image/png", "data": "<base64 PNG>" },
  "confidence": { "sunset_sky": 0.93 },
  "method": "sam2-hiera-l"
}
```

- `labelMap` is an 8-bit greyscale (`L`) or palette (`P`) PNG. A pixel value of
  `order + 1` assigns the pixel to that layer, and `0` means unassigned. Unassigned
  pixels take the nearest assigned layer. A map of a different size is resized
  with nearest-neighbour sampling.
- `confidence` is optional, per layer id, in the range 0–1. Missing values are recorded as 0.5.
- `method` is optional and is recorded in `layers.json` as `http:<method>`.

## Errors

| Situation | PaperCraft error |
|---|---|
| Connection refused, DNS failure | `model_unavailable` (retryable) |
| Timeout | `timeout` (retryable) |
| HTTP 5xx | `segmentation_failed` (retryable) |
| HTTP 4xx, malformed JSON or PNG, label value > number of layers | `segmentation_failed` |
| Response larger than 64 MB | `segmentation_failed` |

With `allow_preview_fallback: true`, any of these produces the labelled flat
preview instead of an error.
