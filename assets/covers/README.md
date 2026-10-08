# Prosvet cover assets

- `backgrounds/` and `logo/` are extracted from the owner-supplied
  `input/Просвет 16-9.pptx`; they are project brand assets and remain subject
  to the rights of the presentation owner.
- `Forum-Regular.ttf` is by Denis Masharov and is distributed under the SIL
  Open Font License 1.1. The license text is in `assets/fonts/OFL.txt`.
- `Bellota-Bold.ttf` replaces the original Templegarten display face with a
  heavier weight suitable for small YouTube thumbnails. It is by the Bellota
  Project Authors and is distributed under the SIL Open Font License 1.1; see
  `assets/fonts/Bellota-OFL.txt`. Poiret One remains bundled as an alternative.
- Brand palette: yellow `#F9B913`, sand `#CCB999`, orange `#EE7227`, mint
  `#B6DCCD`, teal `#1B8E88`, brown `#726858`.
- Each template variant reproduces one slide's colour scheme from the deck
  (`sand` is the hand-made reference cover); `decor_alt` is the second leaf
  colour on slides that alternate two.
- `decor/leaf_<hash>.path` are the deck's own leaf outlines and the
  `decor_sets` in `prosvet_16x9.json` its per-slide leaf arrangements,
  regenerated with
  `python tools/pptx_to_template.py "input/Просвет 16-9.pptx" --decor-sets 1,2,6,9,10,13,14,16,17 --out <template>`.
