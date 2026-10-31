# covers/ — cover templates and renderer

Applies also to `assets/covers/` (templates, decor paths), `assets/fonts/` and
`tools/pptx_to_template.py` (the PPTX converter).

- `frames.py` (FFmpeg still extraction) serves the cover frame picker.
  `providers.py` and `restore.py` (Zoom/ONNX/ComfyUI) are experimental and not
  wired into the workspace.
- In the source PPTX the 16:9 slide scale makes the numeric point size equal
  the pixel size on the 1280×720 canvas: never add a point-to-pixel conversion
  in the renderer.
- Import `onnxruntime` only inside restoration/face-detection calls. Rendering
  and manual photo selection must work without model weights or a usable
  execution provider.
- The PPTX converter drops shapes fully outside the slide. Decorative
  `custGeom` paths accept only `moveTo`, `cubicBezTo` and `close`; an unknown
  geometry command is an error, never silently degraded output.
- Decor `.path` files are in the unit square of the source shape's frame; the
  renderer maps that square onto the layer box, not the curve's bounding rect.
- Leaf arrangements live in a template's `decor_sets` and enter a layout
  through a `{"type": "decor_set"}` layer.
- `"auto"` styling (`style.py`) is seeded by the title (the source name when
  untitled): resolve it once and pass the concrete variant/decor set on, so
  preview, export and the recipe's `cover.png` match.
- The YouTube publish wizard renders its approved cover through
  `STEP_REGISTRY["cover"]`, not a separate save path, so its PNG and manifest
  match what the recipe's cover step cache-checks.
- Bellota Bold is the default OFL replacement for Templegarten; Poiret One is
  an optional lighter alternative. Keep each font's OFL file next to its TTF
  and keep the generic fallback path.
