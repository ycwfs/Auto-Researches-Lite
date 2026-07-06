#!/usr/bin/env bash
# One command: collect a venue from OpenReview and embed it into the sidecar's
# ChromaDB (tagged with venue/year), then refresh the venues manifest.
#
#   docker compose run --rm \
#     -e OPENREVIEW_EMAIL=you@x.com -e OPENREVIEW_PASSWORD=*** \
#     paperfinder bash add_venue.sh ICLR 2025 Accepted
#
# For CVF venues (CVPR/ICCV), produce a JSONL with the same fields some other way
# and pipe it straight into build_corpus.py --venue/--year.
set -euo pipefail

VENUE="${1:?usage: add_venue.sh <VENUE> <YEAR> [STATE]}"
YEAR="${2:?usage: add_venue.sh <VENUE> <YEAR> [STATE]}"
STATE="${3:-Submission}"

TMP="$(mktemp /tmp/${VENUE}_${YEAR}.XXXX.jsonl)"
trap 'rm -f "$TMP"' EXIT

echo ">> collecting ${VENUE} ${YEAR} (${STATE}) from OpenReview..."
python collect_openreview.py --venue "$VENUE" --year "$YEAR" --state "$STATE" > "$TMP"

echo ">> embedding into ChromaDB..."
python build_corpus.py --input "$TMP" --venue "$VENUE" --year "$YEAR"

echo ">> done."
