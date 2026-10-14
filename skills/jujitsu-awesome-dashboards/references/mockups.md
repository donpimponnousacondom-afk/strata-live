# Mockups with an image model: targets, not truth

An image model's **edit** endpoint can take your own screenshot and return the same screen, remastered: crisper
curves, consistent rounding, a heavier condensed font, a tasteful glow. That gives you a concrete visual target in
about a minute per page, without redesigning.

## How

1. Capture the page at its real size with good data. Never use a blurry or downscaled capture: the model copies the
   blur.
2. Call the edit endpoint with that image and the prompt below:
   `python scripts\image_edit.py shot.png mock.png --prompt @prompt.txt`, with `IMAGE_API_BASE`, `IMAGE_API_KEY`
   and `IMAGE_MODEL` set in the environment. Any OpenAI-compatible `/v1/images/edits` works.
3. Put the mockup side by side with the screenshot (`imgtool.py stack`) and list the concrete differences: edge
   quality, stroke weight, rounding, glow, spacing.
4. Implement those differences in code. Then capture again and compare against both.

## Prompt template

```
Remaster this exact screenshot (<W>x<H>) of a Star Trek LCARS-style engineering console desktop app into a crisp,
high-fidelity version of the SAME screen. Wide landscape, aspect ratio <W/H rounded, e.g. 1.9:1>.

Keep the layout identical: every panel, bar, pill, button, label, number, chart and the <main instrument> stay in
the same position, size, order and colour. Do not add, remove or rearrange any element. No window frame, no photo,
no perspective: a flat, straight-on screen capture.

What to improve: razor-sharp vector edges and perfectly round pill caps and elbow curves (true LCARS geometry),
clean consistent spacing, crisp anti-aliased text in a tall condensed DIN-style sans (like Bahnschrift SemiBold
Condensed / Antonio), all labels uppercase, the classic LCARS palette on pure black (orange #FF9900, peach #FF9966,
tan #FFCC99, gold #FFCC66, lilac #CC99CC, violet #9977DD, periwinkle #9999FF, sky #99CCFF, red #CC4444,
green #66CC99). Subtle, tasteful glow on the <main instrument> only. Thin precise chart lines, evenly spaced ticks.

Text fidelity matters most: copy every word and number from the input letter for letter; never invent words. Keep
the small text small; just make it sharp.
This is <page name>: <one line on what's on it>.
```

## Caveats (measured)

- **Aspect ratio comes from words, not the `size` parameter.** "aspect ratio 1.9:1" returned 1729×910; the size
  field was ignored. Output is about 1.5–1.6 MP whatever you ask, then you scale.
- **Small text gets garbled** ("TOP P" became "TC9 P", invented model names). Never copy text from a mockup; the
  app's text is the truth.
- **Layout holds well with a strong prompt**, but check element by element. Models like to "improve" by adding
  panels; forbid it explicitly.
- **Run 2–4 variants per page** in parallel, then keep the one that changes only finish, not structure.
- **Generating from a description alone** (no input image) makes a pretty but different screen. Use that only for a
  brand-new design, never for polishing an existing one.
