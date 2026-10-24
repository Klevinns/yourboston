# Self-hosted OSRM for FitMap

FitMap's commute matrices (walk / bike / car) are computed against a
self-hosted [OSRM](https://project-osrm.org/) backend running on a Massachusetts
OpenStreetMap extract. No Google or paid routing API is ever used.

## Prebuilt graphs

For this project the three routing graphs are already compiled and staged at
`/root/hackachusetts/data/`:

- `foot.osrm*`      — walking profile
- `bicycle.osrm*`   — cycling profile
- `car.osrm*`       — driving profile (free-flow, no traffic)

**Do not rebuild them** — they are ready to serve.

## Serving the prebuilt graphs

Start one container per profile (MLD algorithm), mapping each to a port the
pipeline expects (`config.py`: foot 5001, bike 5002, car 5003):

```bash
docker run -d --name osrm-foot -p 5001:5000 \
  -v /root/hackachusetts/data:/data \
  osrm/osrm-backend \
  osrm-routed --algorithm mld /data/foot.osrm --max-table-size 700

docker run -d --name osrm-bike -p 5002:5000 \
  -v /root/hackachusetts/data:/data \
  osrm/osrm-backend \
  osrm-routed --algorithm mld /data/bicycle.osrm --max-table-size 700

docker run -d --name osrm-car -p 5003:5000 \
  -v /root/hackachusetts/data:/data \
  osrm/osrm-backend \
  osrm-routed --algorithm mld /data/car.osrm --max-table-size 700
```

Verify a server responds:

```bash
curl "http://localhost:5001/table/v1/driving/-71.0580,42.3603;-71.1097,42.3736?sources=0&destinations=1&annotations=duration"
```

`--max-table-size 700` is large enough for the pipeline's 100-source chunks; the
`osrm.py` client also chunks requests defensively.

## Building from scratch (reference only)

If you ever need to rebuild (you do not for this project), see `build.sh`.
