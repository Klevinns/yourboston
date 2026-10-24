#!/usr/bin/env bash
# Build OSRM routing graphs for YourBoston (reference only — graphs are prebuilt).
#
# Downloads the Geofabrik Massachusetts extract and compiles foot / bicycle /
# car profiles with the MLD pipeline inside the osrm/osrm-backend Docker image.
#
# DO NOT run this for the hackathon build: the .osrm graphs are already staged
# at /root/hackachusetts/data/. This script documents how they were produced.
set -euo pipefail

DATA_DIR="${1:-$(pwd)/data}"
PBF_URL="https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf"
PBF="$DATA_DIR/massachusetts-latest.osm.pbf"
IMG="osrm/osrm-backend"

mkdir -p "$DATA_DIR"

if [[ ! -f "$PBF" ]]; then
  echo "Downloading MA extract…"
  curl -L -o "$PBF" "$PBF_URL"
fi

build_profile() {
  local profile="$1"      # foot | bicycle | car
  local lua="/opt/${profile}.lua"
  echo "=== Building $profile ==="
  docker run --rm -v "$DATA_DIR:/data" "$IMG" \
    osrm-extract -p "$lua" "/data/$(basename "$PBF")"
  # Rename the generic osrm output to the profile name
  for ext in osrm osrm.* ; do :; done
  docker run --rm -v "$DATA_DIR:/data" "$IMG" \
    osrm-partition "/data/massachusetts-latest.osrm"
  docker run --rm -v "$DATA_DIR:/data" "$IMG" \
    osrm-customize "/data/massachusetts-latest.osrm"
  # Move artefacts to <profile>.osrm*
  for f in "$DATA_DIR"/massachusetts-latest.osrm*; do
    mv "$f" "${f/massachusetts-latest/$profile}"
  done
}

build_profile foot
build_profile bicycle
build_profile car

echo "Done. Serve with the commands in README.md."
