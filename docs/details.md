# Kva Agent — detailed description

**English** | [Русский](details.ru.md)

The short version and quick start are in the [README](../README.md).

> The web interface is in Russian. Button labels, statuses and example queries are quoted here in English translation (with the Russian original where it matters).

## What it looks like for a guest

The home page is a single field: the guest describes a trip to Baikal in their own words and, if they want, attaches a reference photo. The model turns the request and the picture into criteria; Kva (the frog) searches the Ostrovok catalog (912 hotels in the region, the most suitable ones are additionally studied on the fly), verifies every fact by coordinates and map, sources and freshness, and shows the **three best options**: how well the hotel fits, what is good about it, what is worth asking the hotelier, and how it was verified. Details ("How we know this") are behind a button. "Compare these hotels" opens a short table: columns are hotels with a main photo, rows are **only what the guest asked for** (in text and in the photo), each cell holds one phrase from Kva, the best value is highlighted, and Kva's conclusion is at the top; the hotelier dashboard is in the top menu. Photos are analyzed by a multimodal model (`gemma3:4b` by default); if the model can't see images, Kva honestly says so and searches by text.

The conversation doesn't break off: the whole history (the guest's messages and Kva's replies) is shown as a feed on the home page and stored in the browser session. The guest can refine: "oh, I forgot, also with a pool and a banya" adds to the earlier wishes (if the model is alive, it also replaces what the guest changed their mind about; "let's start over" starts from scratch), and Kva rebuilds the top three. Earlier selections collapse into short links, and the history is sent to the server as context for the model (the server stores nothing). The "Start over" button clears the conversation.

If the guest just says hello, asks "who are you", asks about somewhere other than Baikal, or sends a photo unrelated to travel (a cat, a screenshot, food), Kva replies in a friendly way and gently returns to the point: it suggests what to describe and offers example queries as one-click buttons. Without an LLM, the same cases are covered by template replies.

## Quick start

Only Docker is required (Compose v2.24+).

```bash
docker compose up --build   # without .env or arguments: runs on rules, without an LLM
# optional: cp .env.example .env — adds the built-in Ollama (gemma3:4b, ≈3.3 GB on first start)
```

Open http://localhost:8080. Liveness check: `curl localhost:8080/health` → `{"status":"ok"}` (the same address is used in the compose healthcheck).

The image is built for `linux/amd64` (on Apple Silicon Docker runs it through emulation — `docker-compose.yml` has `platform: linux/amd64`). The first start takes less than a minute: data is taken from the repository, nothing is downloaded. Resources: without an LLM, 1 CPU core and 512 MB RAM are enough; with the `llm` profile — 4 cores and 8 GB.

**Data seeding** runs automatically on first start: the service reads `data/raw/` and computes the characteristics (`app/pipeline.py`); you don't need to run anything by hand. Test credentials and routes are in the "Routes and test credentials" section. The same commands are in the `Makefile`: `make up`, `make down`, `make seed` (recompute the data in an already running service).

### Data in the repository

`data/raw/` holds real data collected from ostrovok.ru and OpenStreetMap, so launching requires neither internet nor a long download:

* `catalog/russia_baykal.json` — index of the whole "Hotels near Lake Baikal" region: 912 hotels, each with price, stars, rating, distances, amenities, coordinates;
* `ostrovok/*.json` — 52 hotels studied in depth: listing, rules, description, reviews with text, category ratings, photos;
* `osm/tiles/` — 244 OpenStreetMap tiles (buildings with heights, roads, the lake shore, venues), from which sunset, quietness, view and walking paths are computed.

The remaining hotels of the region (860) are studied by the service itself, on user requests (see below), if internet is available.

### How hotels are selected: region index and "on-demand" checking

A deep check of a hotel (listing, reviews, map, sunset/quietness/view calculation) takes minutes per dozen hotels, and the region has thousands, so "the first N from the catalog" is a poor sample. The service works in two stages, like search over a large inventory:

1. **Region index** (cheap): `collect` walks through catalog pages (`COLLECT_CATALOG_PAGES`, 20 hotels each) and saves for each hotel the price, stars, rating, distance to the metro, beach and center, amenity flags and coordinates. The initial deep sample (`COLLECT_LIMIT`) is **diverse**: map cells × price terciles × accommodation type, with the hotels with the most reviews taken within each cell.
2. **On-demand enrichment** (expensive, but only what's needed): when a user writes a query, the service uses the index to pick the best not-yet-checked candidates (proximity to the metro and beach, price, stars, amenities; sunset and quietness can't be learned from the catalog, they are computed after the check), puts them in a queue, and a background worker politely checks them one at a time. The interface shows "checked X of Y", marks candidates as "catalog data, not verified", and refreshes the results by itself. The result is cached in the DB, so the next similar query is instant; limits: `ENRICH_BATCH` candidates per query, `ENRICH_MAX_PER_HOUR` checks per hour. Without internet, a hotel is marked "could not be checked" with a reason, and search over what has already been collected keeps working. Queue statuses: `GET /api/jobs`.

To rebuild the data from scratch (internet required, ~10–20 minutes for 50 hotels):

```bash
docker compose --profile collect run --rm collect     # Ostrovok + OpenStreetMap tiles → ./data
git add data && git commit -m "Ostrovok and OSM data"
```

Without Docker: `pip install -r requirements.txt && python scripts/collect_ostrovok.py && python scripts/collect_osm.py`.

If Ostrovok serves the script an anti-bot page, there is a fallback through a regular browser: `scripts/browser_export.js` exports listings and reviews to `kva-bundle.json`, then `python scripts/import_bundle.py kva-bundle.json`; OSM tiles can be exported from the browser via api.openstreetmap.org and loaded with `python scripts/import_osm_bundle.py`. This is exactly how the data in the repository was collected.

## What the service can do

| Track requirement | Where it is in the project |
|---|---|
| The agent collects unique characteristics from open data and from the hotelier | `app/collectors/` (listing, reviews, ratings, geodata), dialog — `app/hotelier.py` |
| At least one characteristic is computed from geodata | sunset and sunrise accounting for **mountains (digital elevation model)** and forest, view of mountains, landmarks and water, noise on 8 sides, walking routes **with climbs**, nightlife, bus stops, construction, white nights — `app/geo/`, `app/dem.py` |
| Characteristics from external open sources | winter climate, air quality, landmarks and the history of the building (Wikipedia, Wikidata), street photos (Wikimedia Commons), cell towers — `app/probes/` |
| Every value has a source, confidence, date | the `observations` table; the "Where is this from?" button shows evidence, quotes and method |
| Conflicts are detected and resolved | `app/reconcile.py`: statuses "confirmed / likely / weak / disputed / outdated", a discrepancy text showing both sides |
| Dialog with the hotelier, free-text answer → fields | `app/hotelier.py` + the `#/hotelier` dashboard; parsing by rules, smarter with an LLM key; the answer can be "simulated" with a button |
| Extensibility: a new characteristic from a description | the "Characteristics" page: a description in words → a draft (LLM or keywords) → applied to the reviews of all hotels, wired into search and questions; also an entry in `app/features/registry.yaml` |
| Natural-language search | `app/search.py`: "a quiet room with a sunset view not far from the sea" → criteria (chips that can be removed), a score and a "why it fits / doesn't fit" explanation |
| Comparison, hotelier dashboard | `#/compare`, `#/hotelier` |
| Re-checking outdated data | `app/freshness.py`; "Re-check" buttons, the demo "a house was built nearby → the sunset is gone" |
| Quality metrics and a labeled sample | the "Quality and freshness" page, `app/metrics.py` |

## Hidden characteristics: where from, how reliable, how to verify, when they expire

For every characteristic we store not only the value but also the answers to four questions: **source**, **confidence**, **how to verify it yourself**, **expiry**. In the interface this is the "How we know this" block (the "Where is this from?" button); in the registry — the fields `ttl_days`, `verify`, `limits`. A language-model guess is not counted as a source: the LLM does not take part in computing these values.

| Characteristic | Source and method | Confidence ceiling | How to verify | Expiry |
|---|---|---|---|---|
| Sunset / sunrise from the window | OSM buildings and forest + terrain horizon (Terrarium), sun on the 15th of each month | 0.85; without terrain ×0.85 and a "terrain not accounted for" note | silhouette and sun path in the card; photos from windows in reviews | 365 days |
| Mountain view | terrain horizon angle > 2° above buildings and forest, named OSM peaks (accounting for Earth's curvature and refraction) | 0.7 | photos from rooms; terrain map | 730 days |
| Winter sun (cloudless) | terrain horizon, December–February | 0.7 | compare with the climate reference | 730 days |
| View of a landmark, water | OSM + terrain, minimum floor | by building-height coverage | photos, panorama map | 365 days |
| Noise | OSM: roads, bars, railways, construction sites | a model, not decibels | reviews, an evening visit | 180 days |
| Walking to the metro/water/bus stop | OSM street graph + Tobler's function (climbs slow you down) | 0.6 by graph, 0.4 by straight line | a navigator | 365 days |
| Temperature and winter sun (climate) | ERA5-Land reanalysis via Open-Meteo, 2015–2024 normal, ≈9 km grid | 0.72 / 0.55 | Open-Meteo Historical Weather by coordinates | 730 days |
| Air quality (PM2.5) | CAMS model via Open-Meteo, ≈45 km step, 12 months | 0.45 | IQAir / OpenAQ nearby | 180 days |
| Landmarks, history of the building | Wikipedia (geosearch) and Wikidata; an article about the building only if the name matches and the distance is ≤ 200 m | 0.65–0.7 | links to articles | 365–730 days |
| Street photo | Wikimedia Commons, geotagged files within 150 m, author and license are shown | 0.5 | links to files | 365 days |
| Mobile coverage | OSM towers within 10 km (+ OpenCellID with a key) | 0.4 (0.6 with OpenCellID) | operator coverage maps | 365 days |

Honesty rules built into the code:

* No data — no value. A probe of an external source returns a status `ok / cached / no_data / offline / error`, not an invented number; statuses are visible in the hotel card ("External sources").
* "Not found" does not turn into "no": "no towers in OSM" and "no article about the building" are recorded as weak observations with the words "this doesn't mean that…".
* Model-based and computed sources (`osm_geo`, `dem_geo`, Open-Meteo, OSM towers) without independent confirmation reach "likely" at most.
* If the network is unavailable, a saved response is used with its age indicated (`stale_cache`); API keys do not end up in the cache or in links.
* A discrepancy of more than 150 m between the climate grid cell's elevation and the hotel's elevation by terrain lowers the temperature confidence.

### Additional data: how to collect

The project works offline on what is in `data/`. Terrain, forest, bus stops, towers and external-source responses are collected separately (internet required, no keys needed):

```bash
docker compose --profile collect run --rm check-sources   # 1 minute: do all sources respond the way the code expects (run this first)
docker compose --profile collect run --rm collect-extra   # terrain → data/raw/dem, forest/towers/peaks → data/raw/osm_extra, API responses → data/raw/ext
```

Without Docker: `python scripts/check_sources.py`, `python scripts/collect_extra.py` (`--only dem --limit 5` — a trial run on five hotels).

What is collected can be committed: with it, `docker compose up` works without a network. The on-demand check (`enrich`) and the "Query sources now" button download the same things for a single hotel. Without collected terrain the service doesn't crash: sunset and mountains are computed for flat ground and marked as such.

More details: [architecture.md](architecture.md) (how reliability and geo models are computed) and [research.md](research.md) (how Booking.com and Airbnb do it and what the research says).

## Routes and test credentials

Interface (hash routes): `#/` home page with the frog (query + photo → top 3) · `#/search` detailed search with a map · `#/hotel/<id>` hotel card · `#/compare?ids=a,b` comparison · `#/hotelier` hotelier dashboard.

**Hotelier dashboard:** login `hotelier`, password `hotelier123` (changeable in `.env`: `HOTELIER_LOGIN`, `HOTELIER_PASSWORD`). Everything else is open without logging in; answers "on behalf of the hotelier" given without logging in are marked as simulated and weigh less.

API (JSON): `GET /health`, `/api/meta`, `/api/hotels`, `/api/hotels/{id}`, `/api/hotels/{id}/facts/{feature}?scope=`, `/api/hotels/{id}/horizon?az=&floor=&month=`, `/api/search?q=`, `POST /api/frog/ask` (query + photo → top 3), `POST /api/frog/say`, `/api/compare?ids=`, `/api/features` (+ `POST`, `POST /suggest`, `DELETE`), `/api/questions`, `POST /api/questions/{id}/answer|simulate`, `POST /api/hotelier/login|confirm`, `/api/freshness`, `POST /api/hotels/{id}/refresh`, `/api/metrics`, `/api/eval/candidates`, `POST /api/eval/labels`.

## Environment variables (`.env.example`)

| Variable | Default | Purpose |
|---|---|---|
| `APP_PORT` | `8080` | port inside the container (outside it is always 8080:8080) |
| `COMPOSE_PROFILES`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_PROJECT` | `llm`, `http://ollama:11434/v1`, `ollama`, `gemma3:4b` | LLM out of the box (built-in Ollama); can be replaced with any OpenAI-compatible API or turned off — then rules are used. Ready-made examples (Ollama, Yandex AI Studio) are in `.env.example`; check: `docker compose exec app python scripts/check_llm.py` |
| `REGION_SLUG` | `russia/baykal` | Ostrovok catalog region to collect |
| `COLLECT_LIMIT`, `COLLECT_DELAY` | `60`, `1.5` | size of the initial deep sample and the pause between requests (sec) |
| `COLLECT_CATALOG_PAGES` | `100` | how many catalog pages to walk for the region index (20 hotels each) |
| `ENRICH_ON_DEMAND`, `ENRICH_BATCH`, `ENRICH_MAX_PER_HOUR` | `true`, `8`, `120` | background checking of candidates for a query: whether it is enabled, how many to queue per query, hourly limit |
| `HOTELIER_LOGIN`, `HOTELIER_PASSWORD` | `hotelier`, `hotelier123` | dashboard login |
| `DEM_ENABLED`, `DEM_FETCH` | `true`, `true` | use the digital elevation model; download missing tiles during an online check |
| `PROBES_ONLINE`, `PROBE_TIMEOUT` | `true`, `8` | whether to query external sources during an online hotel check; timeout of a single request, s |
| `OPENCELLID_KEY` | empty | optional: OpenCellID key for estimating mobile coverage |
| `FOREST_HEIGHT_M` | `14` | typical forest height when computing visibility |
| `REFRESH_ENABLED`, `REFRESH_INTERVAL_MIN`, `REFRESH_ONLINE` | `false`, `60`, `false` | background re-checking of outdated data (online — with re-downloading) |

Secrets are read only from the environment/`.env` (which doesn't go into git). Large models are not needed and are not in the repository; an external API can be connected if desired.

## LLM: what it does and where it comes from

Characteristics are computed from geodata and reviews, not "guessed by the model". The LLM understands free text: it parses the guest's query, sorts the hotelier's answer into fields, simulates the hotelier, and writes a draft of a new characteristic.

**Out of the box.** `.env.example` enables the `llm` profile: together with the service, Ollama is started (`ollama/ollama`, version pinned) and downloads a small open model `gemma3:4b` by itself (≈3.3 GB, understands images too, once; the model is not in git). The application doesn't wait for the download: while it is in progress, rules and recorded model answers for the example queries from the interface (`data/llm_cache.json`) work, and then the live model is connected. The page footer shows the status ("LLM is loading" → "LLM: model"). We recommend 4 CPU cores and 8 GB of memory for Docker; on CPU, parsing a new query takes 10–30 seconds, while a repeated one and the examples from the interface are instant.

**Your own API.** Remove `COMPOSE_PROFILES=llm` and fill in `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` of any OpenAI-compatible service (examples: Ollama on your computer, Yandex AI Studio — in `.env.example`). Keys are stored only in `.env`, which doesn't go into git. Check: `docker compose exec app python scripts/check_llm.py`.

**Without an LLM.** With `COMPOSE_PROFILES=` and `LLM_API_KEY=` empty, everything works on rules, and the examples on recorded answers. To update the recorded answers: `docker compose exec app python scripts/record_llm.py`, then `docker compose cp app:/app/runtime/llm_cache.json data/llm_cache.json`.

## How trust in the data works

* Any value is an **observation**: characteristic, scope (hotel, "west-facing windows", "courtyard side"…), value, source, confidence, evidence date, quote, method.
* Weight = confidence × freshness. Freshness falls with age relative to the characteristic's lifetime (quietness — 180 days, elevator — 365).
* Independent sources reinforce each other; a contradiction with noticeable weight produces "disputed" and is shown to the user with dates and quotes.
* The hotelier's word carries weight, but the map is checked against it: if the OSM calculation doesn't allow what they say, the fact remains "disputed".
* Calculations based only on the map and terrain, as well as model-based sources (weather reanalysis, air model) without independent confirmation, do not get "confirmed"; neither does a simulated hotelier.

An example from real data: in the hotel's listing the front desk works 10:00–22:00, while fresh reviews say "the front desk is open around the clock" — the magnifier shows both pieces of evidence with dates and asks the hotelier a question.

## Limitations (honestly)

* The data in the repository was collected through a regular browser (the Ostrovok catalog and listings, OSM tiles via api.openstreetmap.org). Direct collection with the `collect` script from a container was not run end to end in the development environment: if Ostrovok serves an anti-bot page, the fallback through the browser applies (above). The parsers were checked on real pages and with tests.
* **Terrain**: the Terrarium model (SRTM and others) gives a general silhouette with a step of 12 / 47 / 190 m across three layers (up to 1.8 / 9 / 75 km), not individual cliffs; artifacts are possible at high latitudes and near water — the hotel's elevation is taken as the median of the surrounding area, and values near water are clipped. The horizon is computed with the r.horizon algorithm with Earth's curvature and refraction, but cloud cover and haze are not taken into account.
* **Forest** — OSM polygons without "holes" (clearings and settlements inside a forest are often not cut out), tree height is typical (`FOREST_HEIGHT_M`). This may understate the view where OSM shows forest in place of buildings.
* The visibility model is 2.5D: building heights come from OSM, where they aren't filled in everywhere (otherwise the typical 18 m is used); the share of explicit heights lowers confidence. Geo values without independent confirmation are marked "likely" at most.
* **External sources were checked in development on recorded responses, not live**: the development environment had no access to Open-Meteo, Wikipedia, Wikimedia Commons, AWS and Overpass. Addresses and fields were checked against documentation (Air Quality date windows, 0.4° ≈ 45 km for CAMS — per the Open-Meteo documentation; `sunshine_duration` in the archive API — per a third-party description), but the first live run is yours: `python scripts/check_sources.py` will show exactly what doesn't match. The OpenCellID key and its response format were not verified at all.
* Climate is a 10-year normal from a reanalysis (≈9 km), not a forecast and not weather-station measurements; air quality is a ≈45 km model, not a sensor: it doesn't see stove smoke on a particular street. OSM towers are mapped incompletely: they can't be used to promise "there is coverage".
* Nightlife, parking and construction sites come from OSM tags, which are updated with a delay.
* Review analysis uses rules and dictionaries; for new characteristics described in words, quality is lower than for the built-in ones.
* There is no labeled sample: we don't make one up. Accuracy metrics will appear after labeling (the "Quality and freshness" tab).
* A guest's photo is analyzed in two ways. If it is a picture of a hotel from the database (from the website, a screenshot, a re-saved file), Kva recognizes it by the perceptual hashes of hotel photos (`app/identify.py`, the index is computed in the background into `runtime/photo_index.json`) and puts that hotel first. Otherwise the multimodal model turns the photo into wishes. Analysis of guests' photos in reviews (source `photo_vision`) is the next step.
* A hotel can be called by name: "Mayak", "Cozy Fox guesthouse with a banya" (Russian: «Маяк», «гостевой дом Уютная лиса с баней») — search by name handles grammatical cases and typos; accommodation type (apartment, cottage, guesthouse…) and settlement ("in Listvyanka", "on Olkhon", `app/places.py`) work as search conditions.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

123 tests: geo models (sun azimuth, sunset, obstructions, noise, walking routes), **terrain** (tile encoding, horizon angle on a synthetic ridge, Earth's curvature, light lost at sunset, peaks, climbs on foot, forest), **external sources on recorded responses** (cache, statuses, offline, keys don't end up in the cache), reliability, parsing of Ostrovok and reviews, parsing of the hotelier's answer, Kva's replies (top 3, comparison, small talk, unrelated photos), the end-to-end pipeline on a synthetic world, and the API.

## Structure

```
app/            service: main (Starlette), pipeline, collectors/, geo/ (+terrain), dem, osmextra, probes/, extcache, reconcile, search, hotelier, freshness, metrics
app/features/   registry.yaml — characteristics and templates; landmarks.yaml — landmarks
web/            build-free interface (HTML/CSS/JS), Leaflet from a CDN
scripts/        collect_ostrovok.py, collect_osm.py, collect_extra.py (terrain, forest, towers, external APIs), check_sources.py (live source check), browser_export.js, import_*.py
data/raw/       collected Ostrovok data, OSM tiles, terrain (dem/), extra OSM data (osm_extra/) and the external API cache (ext/)
data/eval/      labels.json — manual labeling for accuracy metrics
docs/           details.md, architecture.md, research.md (Russian versions: *.ru.md)
```
