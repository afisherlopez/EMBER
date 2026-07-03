# ADR 0002: Dynamic tiling over pre-rendered tiles

## Status
Accepted

## Context
Raster metrics may grow over time and users need pan/zoom at arbitrary extents without storing many tile pyramids.

## Decision
Use TiTiler (`/cog` endpoints) to dynamically serve XYZ tiles from COG URIs.

## Consequences
- Small storage footprint and no tile build pipeline in v1.
- Styling (rescale/colormap) remains runtime-configurable per asset.
- Requires careful COG writing and GDAL tuning for performance.
