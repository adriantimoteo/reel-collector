# Decision Stub: Google Places Terms Compliance

**Status:** Unresolved. Accepted as a private-beta risk (A12). A public
launch requires a real terms review and, most likely, a different
geocoder/map combination — tracked as a gate in the V2A phase ("Part A —
Decision spikes").

## The risk, as understood during planning

Secondary sources (not the primary Google Maps Platform Terms of Service)
suggest:

1. Places content (names, coordinates) obtained via the Places API may not
   be displayed on a **non-Google map**. This product renders places on a
   **Leaflet + OpenStreetMap-tiles** map, not Google Maps.
2. There are restrictions on how long Places data (in particular raw
   coordinates, as opposed to a Place ID) may be cached/stored.

Neither of these has been verified against the actual, current primary terms
text as of this phase. **Do not treat this stub as a compliance sign-off.**

## What P2 does about it

- The `Resolver` protocol (`pipeline/resolve/base.py`) is provider-neutral;
  `GooglePlacesResolver` is one implementation, swappable without touching
  merge/rebuild/render logic.
- `geocode_cache` TTLs are configurable (`GEOCODE_CACHE_TTL_DAYS`,
  `GEOCODE_NEGATIVE_TTL_DAYS`) rather than hardcoded, so retention can be
  tightened later without a schema change.
- Nothing in P2 enforces a compliance gate at runtime; that is explicitly
  deferred to V2A's `PUBLIC_MODE` startup guard (per `technical-decisions.md`).

## What must happen before public launch (V2A)

1. Read the **primary** Google Maps Platform Terms of Service (Service
   Specific Terms → Places API section, and the general
   caching/"Restrictions" clauses) and quote the relevant clauses directly.
2. Decide between: (A) Google Places + Google Maps JavaScript API for the
   page, (B) an OSM-compatible geocoder with Leaflet/MapLibre, or (C) a
   hybrid (compliant geocoder for coordinates, Google only for an "Open in
   Google Maps" search-by-name link with no Places content stored).
3. Record the decision, evidence, and any required data purge in
   `docs/decisions/map-and-geocoder.md` (V2A), with the user's explicit
   sign-off.

Until that ADR exists and is signed off, this product must not be presented
to the public as launched, per the phase index's stated gate.
