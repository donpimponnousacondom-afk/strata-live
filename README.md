# pr-assets

Images for the pull request "Anti-aliased canvas shapes and smoother redraws". Nothing here is code; this branch
is never meant to be merged.

- `hero.png`: main page, elbow + warp core, before | after, 2x pixel zoom
- `detail-*.png`: before | after, 3x pixel zoom (elbow, warp core, gauges, duty ring, chart, pills, sliders)
- `pageNN.png`: every page, before over after, 1920x1009

All zooms are nearest-neighbour, so pixels are shown as rendered. Before = the repo at 8e4d94e; after = the PR
branch. Same mock data, Tk 8.6, 96 dpi.
