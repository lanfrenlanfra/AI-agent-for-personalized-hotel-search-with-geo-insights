# AI Agent for Personalized Hotel Search with Geo Insights

**English** | [Русский](README.ru.md)

Describe your trip in your own words and, if you like, attach a photo. Kva picks three hotels that match even the wishes that aren't covered in Ostrovok listings: what you see from the window, how quiet it is, how to get around on foot. For everything it finds, it shows where the information comes from and how far it can be trusted.
- Region: Lake Baikal
- Demo video: [watch on Google Drive](https://drive.google.com/drive/folders/1ahUVVx5AGqXZvGPdsNkKGdb7JO3P3vSh?usp=sharing)
- Presentation: [docs/presentation.pdf](docs/presentation.pdf)
- The service runs locally

The agent extracts from open data what is missing from an Ostrovok listing: sunset and the view from the window, quietness, walking routes that account for elevation, nightlife, climate, air quality. Every fact comes with a source, a confidence level and an expiry date. Region: Lake Baikal.

## Features

- **Search by description and by photo.** Write the request in your own words or show a picture, e.g. "I want a view like this". Kva picks the three best hotels and explains the choice.
- **Sunset and view from the window.** Calculated from terrain and the height of neighboring buildings: when the sun sets, whether a building or a mountain will block it, whether the lake and peaks are visible.
- **Quietness.** Estimates noise on each side of the building: roads, venues, nearby construction.
- **Walking routes with elevation.** To the beach, bus stop, city center and airport: travel time accounts for elevation, not straight-line distance.
- **Nightlife, family, car.** What is open late, how convenient it is with kids, where to park and charge an electric car.
- **Climate and air.** How many sunny days per season, air quality and weather based on Open-Meteo data.
- **Local history.** Facts from Wikipedia and Wikidata, geotagged photos from Wikimedia Commons.
- **Honesty.** Every fact has a source, a date and a confidence level; if there is no data, Kva says so instead of making things up.
- **Disputed points are clarified by the hotelier.** In the hotelier dashboard they confirm or refute a fact; a free-text answer is parsed into fields.
- **Works offline.** All required data is stored in the repository.

## Quick start

```bash
git clone https://github.com/ostrovok-hackathon-2026/liagushki.git
cd liagushki
cp .env.example .env   # optional: enables the built-in LLM (Ollama, ≈3.3 GB on first start)
docker compose up --build
# open http://localhost:8080
```

## Requirements and environment variables

Recommended resources:
- 1 CPU core, 512 MB RAM (without LLM)
- 4 CPU cores, 8 GB RAM (with LLM)

Only Docker is required (Compose v2.24+). Python 3.13, Starlette, SQLite (volume `kva-runtime`).

- `APP_PORT` (default `8080`)
- `COMPOSE_PROFILES` (empty by default; `llm` starts Ollama)
- `LLM_MODEL` (default `gemma3:4b`)
- `HOTELIER_LOGIN` / `HOTELIER_PASSWORD` (default `hotelier` / `hotelier123`)
- `DEM_FETCH`, `PROBES_ONLINE` (default `true`: download terrain data and query external sources when internet is available)

The full list is in `.env.example`.

## Seeding

Runs automatically on first start: the service reads `data/raw/` and computes the characteristics. You don't need to run anything manually.

```bash
make seed  # recompute the data in a running service
docker compose --profile collect run --rm collect  # rebuild the data from scratch (internet required, 10–20 min)
```

## Routes / access

- `/` — UI (chat with the frog, search, hotel card, comparison)
- `/health` — 200 OK, JSON `{"status":"ok"}`
- `/#/hotelier` — hotelier dashboard: `hotelier / hotelier123`
- `/api/search?q=тихий+номер+с+закатом` — search (JSON); the example query means "quiet room with a sunset"

## Learn more

- [docs/details.md](docs/details.md) ([RU](docs/details.ru.md)) — how the service works, where each characteristic comes from, limitations
- [docs/architecture.md](docs/architecture.md) ([RU](docs/architecture.ru.md)) — data reliability and geo models
- [docs/research.md](docs/research.md) ([RU](docs/research.ru.md)) — how Booking.com and Airbnb do it

Tests: `pip install -r requirements-dev.txt && python -m pytest -q`.
