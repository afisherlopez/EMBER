# ADR 0002: Dynamic tiling over pre-rendered tiles

## Status
Accepted, narrowed to burn severity

## Context
Annual burn-severity rasters need pan/zoom at arbitrary extents without storing a
separate XYZ tile pyramid.

## Decision
Use a custom FastAPI/rio-tiler endpoint to mosaic the published annual
burn-severity COGs and dynamically serve PNG map tiles. The service does not expose
generic COG URLs or the former TiTiler `/cog` routes.

## Consequences
- Small storage footprint and no tile build pipeline in v1.
- Burn-severity categories use one fixed application colormap.
- Newest selected years win where annual rasters overlap.
- Requires careful COG writing and GDAL tuning for performance.
