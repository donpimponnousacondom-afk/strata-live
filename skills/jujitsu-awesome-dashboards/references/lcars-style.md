# LCARS (and friends): what makes a starship panel read as real

LCARS is the Star Trek: The Next Generation computer interface: flat colour blocks on black, joined by thick curved
"elbows", with condensed uppercase labels. It works because it is **disciplined**: few shapes, consistent radii,
generous black, and colour that means something.

## Palette (on pure black)

| Role | Hex | Use |
|---|---|---|
| orange | `#FF9900` | the frame: elbows, main bars, primary headers |
| peach | `#FF9966` | secondary frame blocks, warnings that aren't alarms |
| tan / ink | `#FFCC99` | body text and values |
| gold | `#FFCC66` | highlights, "caution" |
| lilac | `#CC99CC` | secondary headers, metadata lines |
| violet | `#9977DD` | tertiary blocks |
| periwinkle | `#9999FF` | data blocks, links |
| sky | `#99CCFF` | sensors, cool states |
| red | `#CC4444` / `#FF5544` | alert only; flashing red is red alert |
| green | `#66CC99` | nominal / OK |
| muted | `#8f8576` | captions, units, "no data" |
| grid | `#1c1c24` | unlit bar segments, chart grid |

Don't add more hues. Vary brightness with `mix(colour, black or white, t)`.

## Geometry

- **Elbow (sweep):** a sidebar column turns into a top or bottom bar with a big outer radius (about 2× the bar
  thickness) and a small inner fillet (about 1×). The sidebar is about 150 px wide and the bar 20–24 px tall.
- **Pills:** bars with semicircle ends, used as buttons, status lamps and header caps. Height 16–30 px.
- **Header bar:** a small pill cap, then the label, then a thin line to a round end dot, all in one colour.
- **Spacing:** 5–6 px gaps between blocks, and the same gap everywhere. Inconsistent gaps make it look homemade.
- **Blocks:** sidebar buttons are rectangles with the label bottom-right and a small number top-left.
- **Alignment:** labels left, numbers right-aligned in a column, and everything snapped to whole pixels.

## Type

- **Bahnschrift SemiBold Condensed** (Windows 10/11) for labels and headers; Bahnschrift Condensed for small text.
  **Antonio** (OFL) is the closest free LCARS face if you can ship it. Barlow Condensed is a DIN-like fallback.
- ALL CAPS for labels and headers. Lowercase only for long explanatory text.
- Three or four sizes per screen: title, header, label, small. Big numerals for key readouts.
- Text on a coloured block is black (`BG`), never white.

## States and motion

- **Global status:** GREEN, YELLOW or RED as a lit pill in the header. Red alert flashes the frame red at about 1 Hz
  (on and off at 2 Hz) for about 30 s.
- **Instrument animation:** pulses travelling through a column (a warp core), with speed tied to real load. Use
  `phase += speed * dt`. Calm at idle, fast when busy, frozen and dark when offline.
- **Sidebar alerts:** a department with an unread warning blinks a small red dot.
- **Glow:** one focal glow (the core chamber, the main reactor) that follows load. Glow everywhere is cheap sci-fi.

## Make it read as real, not as a parody

- **Restraint.** Lots of black, few shapes, no gradients on blocks, no bevels, no drop shadows.
- **Consistent radii and gaps.** That's 80% of the effect.
- **Real data in every block.** Captions that explain ("FREE VRAM 456 MiB"), not placeholder numbers.
- **Collect high-resolution references.** Screen-accurate LCARS fan recreations and the TNG technical manual style
  pages are good. Product photos at 300 px are not: you can't see radii, gaps or type in them.
- **Hand-tuned weights.** An anti-aliased outline needs about 1.2× the width to read like a hard one.

## Other starship looks (same toolkit, different rules)

- **Federation 2370s+ (Voyager/DS9 era):** similar to LCARS, with more blue and violet and thinner bars.
- **Tactical / CRT green:** monochrome `#66FF99` on near-black, a thin 1 px grid, scanline glow, a monospace
  condensed face.
- **Industrial (The Expanse style):** amber on charcoal, hairline frames, small caps, numeric emphasis, sharp 45°
  chamfers instead of round elbows. Draw the chamfers with `aa.polygon`.
