# Follow the Fake

Links suspicious ads and landing pages into campaigns, and shows the evidence behind each link.
HackNation challenge "Kolekcjoner fejków", set by NASK.

- Design: [docs/architecture.md](docs/architecture.md)
- Signals: [docs/signal-catalogue.md](docs/signal-catalogue.md)
- [Pattern Library](PATTERN_LIBRARY.md) — Pattern discovery, library versioning, novelty tracking

## Quick start

**You need:** Docker with Compose v2, 8 GB of RAM, about 15 GB of free disk, internet on the first
start, and port 8000 free.

```bash
docker compose up
```

Open <http://localhost:8000>. The app starts with the known sample campaigns already loaded.

- **No keys are required.** Local analysis, campaign linking and reports work without them. To
  turn on model reading, run `cp .env.example .env`, add `OPENAI_API_KEY` and `GEMINI_API_KEY`,
  then run `docker compose up -d`.
- **Face matching** prepares itself in the background on the first start, which took about 2
  minutes in testing. The app is usable meanwhile.
- **Probe agent** starts automatically in its own isolated container.
- **After updating the code:** `docker compose up --build`. **To stop:** `docker compose down`.

## Pre-analysed dataset (for mentors)

Our full local analysis (10 cases, 37 items, 11 campaign groups) is shared as one archive,
`ftf-data-full-fd9e296.tgz` (1.0 GB), on Google Drive. It works with commit `fd9e296`. How to
download, unpack and use it: [docs/mentor-dataset.md](docs/mentor-dataset.md).

