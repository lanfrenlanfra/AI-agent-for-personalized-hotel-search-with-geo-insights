# Architecture

**English** | [Русский](architecture.ru.md)

```
 ostrovok.ru ──► collect_ostrovok.py ──► data/raw/ostrovok/*.json ─┐
 Overpass/OSM ─► collect_osm.py ───────► data/raw/osm/tiles/*.gz ──┤  (in the repository)
                                                                    ▼
                 ingest ─► hotels, reviews ─► collectors ─► observations ─► reconcile ─► facts ─► search / API / UI
                                                  ▲                              │
                          hotelier answers ───────┘            questions ◄───────┘ (disputed, outdated, unknown)
```

## Data model (SQLite)

* `observations` — what we learned: `feature, scope, value{v,summary,detail}, source, confidence, observed_at, collected_at, evidence[], method, simulated, superseded`.
* `facts` — the result per `(hotel, feature, scope)`: value, status, confidence, expiry, explanation, conflict.
* `questions`, `events` (value changes, re-checks), `features` (registry), `hotels`, `reviews`.

Scope (`scope`): `hotel`, `room:north…northwest` (windows facing a given compass direction), `room:yard` (the quietest side), `room:street` (the noisiest side), `room:water`. Thanks to scopes, a hotelier's statement ("the sunset is visible from west-facing windows on the 5th floor") is compared with the calculation for the same side of the building.

## Reliability (`reconcile.py`)

1. Observation weight = confidence × freshness. Freshness: 1.0 up to 0.5·TTL, falling linearly to 0.5 at TTL, then decaying to 0.15.
2. For each source the best observation is taken; between independent sources a "noisy-OR" is used (two weak pieces of evidence reinforce each other).
3. The leader is the value with the highest weight. If the opposite value has weight ≥ 0.25 and ≥ half of the leader's weight, the fact is **disputed**; from 0.04 it is a minor discrepancy (shown, but not blocking).
4. The hotelier (`hotelier`) overrides the self-reported data in the Ostrovok listing and ratings; it can only be overridden by independent evidence with weight ≥ 0.75. Physics (`osm_geo`, `astronomy`) contradicting the hotelier's words → "disputed".
5. A simulated hotelier: weight ≤ 0.5, status no higher than "likely". A calculation based only on OSM: no higher than "likely".

## Geo models (`app/geo`)

* **Sunset**: for the 15th of each month, the sun azimuth at an altitude of 2° (setting); from the window of a facade (field of view ±55° from the normal), on each floor, the model checks that along the ray there is no building whose angle exceeds 2°. Building height = OSM `height` → floors × 3.1 + 1.5 → 18 m by default; the share of explicit heights lowers confidence.
* **View of a landmark**: a slope test of the "window → peak" line against buildings on the line of sight, giving the minimum floor and the side.
* **Noise**: sum of sources (roads by class, trams, railways, bars, construction sites) with attenuation of −20·lg(d) and 12 dB shielding by the building itself, for each of the 8 sides → a score from 0 to 100.
* **Walking routes**: Dijkstra over the OSM street graph (80 m/min, penalties for steps and arterial roads), plus the number of busy-street crossings.
* **White nights**: by latitude (the sun no deeper than 9° below the horizon).

### Terrain, forest, climbs (`app/dem.py`, `app/geo/terrain.py`, `app/osmextra.py`)

* **Elevation model**: Terrarium tiles (Terrain Tiles on AWS, no key; elevation = R·256 + G + B/256 − 32768) in three layers: z13 up to 1.8 km (≈12 m per pixel), z11 up to 9 km (≈47 m), z9 up to 75 km (≈190 m). The cache is compressed int16 in `data/raw/dem/`, bilinear sampling is vectorized, NaN outside a layer.
* **Horizon**: as in r.horizon (GRASS GIS): for each azimuth (0.25° step) the maximum elevation angle above the terrain along the ray path is taken, corrected for Earth's curvature and refraction (k = 0.13). The resulting obstruction for the sunset ray is the maximum of the building/forest angle (OSM scene) and the terrain angle.
* **Sun**: position by hour angle; the disc counts as visible if the altitude of its center > −0.833° + terrain angle. From this come "light lost at sunset" and "sunrise delay" in minutes, and hours of direct sun in winter.
* **Buildings on a slope**: each building has a `base` — ground elevation relative to the hotel (from the DEM); obstruction = base + height. The hotel's elevation is the median over nine points within 120 m, so that a single "bad" cell (shoreline, bathymetry) doesn't shift the calculation.
* **Forest**: OSM polygons (`natural=wood`, `landuse=forest`) are an obstruction of typical height; if the window is inside a forest, 25 m to the edge (`FOREST_EDGE_M`).
* **Mountains**: a ridge more than 2° above the buildings within the window's field of view; named peaks (OSM `natural=peak`, `ele`) within 70 km are checked for visibility along the terrain profile.
* **On foot**: graph edges get their travel time from Tobler's function (speed drops on climbs and descents steeper than ≈5%); the response includes the elevation gain.
* **Without terrain** nothing breaks: the calculation runs on flat ground, confidence ×0.85, the text says "(terrain not accounted for)", and characteristics that depend only on terrain (`mountain_view`, `sun_hours_winter`) are not created.

## External sources (`app/probes/`, `app/extcache.py`)

Each probe (`climate`, `air`, `wiki`, `commons`, `masts`) receives a hotel record and returns observations with a source, method, a link to the primary source (`evidence[].url`) and confidence, plus a run status (the `probe_runs` table). API requests go through `extcache.fetch_json`: fresh cache → network (timeout `PROBE_TIMEOUT`; a circuit breaker disables a host for 2 minutes after two consecutive failures) → stale cache with its age → an honest "no data". Responses are reduced to the needed aggregates before being written (`reduce`), so that the cache in the repository stays small; API keys never end up in the cache or in links. In reconciliation, the sources `open_meteo`, `open_meteo_aq`, `osm_masts` are treated as model-based (`MODEL_ONLY` in `reconcile.py`) — a single such record is not enough for "confirmed".

## Region index and on-demand enrichment (`catalog.py`, `enrich.py`)

Two-stage search over a large inventory. The `catalog` table stores cheap features of all hotels in the region (from Ostrovok catalog pages) and the deep-check status (`''/queued/running/done/failed`). `catalog.stratified_sample` picks a diverse starting sample (district × price × type), `catalog.coarse_score` scores candidates for a query using the cheap features, `enrich.augment` queues the best ones, and a background worker runs the whole pipeline (listing → OSM tiles → observations → fact reconciliation → questions for the hotelier). Search answers immediately from the already-checked hotels and honestly shows the coverage (`coverage`), while the UI polls `/api/search` with `passive` until the queue is empty.

## Re-checking (`freshness.py`)

Statuses are recomputed by age; "Re-check" re-runs the collectors (online — re-downloading the listing, reviews and OSM tiles) and writes an event whenever a value changes. Demo mode adds a building to the map to show how a fact changes and how that turns into a question for the hotelier.
