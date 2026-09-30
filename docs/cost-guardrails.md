# Cost Guardrails (P2)

Complete this checklist **before the first real Places lookup**. Geocoding is
the other major cost driver alongside Gemini (see the brief's "API costs"
risk), and this product's whole budget is ~$10/month for the beta.

## Checklist

- [ ] **1. Dedicated Google Cloud project.** Create a project used only by
      this bot (not shared with reel-notes or anything else), with billing
      enabled.
- [ ] **2. Enable only Places API (New).** Do not enable the legacy Places
      API, Maps JavaScript API, or anything else not currently used.
- [ ] **3. Create and restrict an API key.**
      - API restriction: Places API (New) only.
      - Application restriction: "None" is acceptable for a key used only
        from a local server process (never shipped to a browser/app). If the
        host has a static IP once this runs on a server (V2A), add an IP
        restriction.
- [ ] **4. Set a budget with alerts at $5, $8 and $10/month**, in Cloud
      Billing → Budgets & alerts, scoped to this project.
- [ ] **5. Set a daily quota cap for Text Search requests**, well under the
      app-level `PLACES_MAX_LOOKUPS_PER_DAY` default (300), in the API's
      Quotas page (APIs & Services → Places API (New) → Quotas). This is a
      hard server-side backstop independent of application bugs.
- [ ] **6. Record the SKU and unit price** for Text Search (New) found on the
      [Places API pricing page](https://developers.google.com/maps/billing-and-pricing/pricing#text-search)
      at implementation time, since pricing pages change:
      - SKU: **Places API Text Search Pro** (4FDA-34B1-A910). Our request's
        field mask (`places.id,places.displayName,places.formattedAddress,`
        `places.location,places.types`) includes `displayName`, which
        triggers the Pro tier rather than the free Essentials (IDs Only)
        tier, even though most of the requested fields are Basic Data.
      - Price per 1,000 requests: **$32.00** for the first paid tier
        (5,001–100,000 requests/month); volume discounts step down to
        $2.40/1,000 above 5,000,000/month. Re-check this before relying on it
        for budget math, as Google's pricing pages change without notice.
      - Free monthly credit: **5,000 requests/month** included at no charge
        for this SKU (separate from any general Google Cloud free-tier
        credit). At the app's default `PLACES_MAX_LOOKUPS_PER_DAY=300`, the
        free allowance alone is exhausted in under 17 days if the daily cap
        were hit every day — cache hits do not count against this, so real
        usage should stay far below the cap.
- [ ] **7. Confirm the app-level ceilings in `.env`** match your intent:
      `PLACES_MAX_LOOKUPS_PER_RUN` (default 150), `PLACES_MAX_LOOKUPS_PER_DAY`
      (default 300), `GEOCODE_CACHE_TTL_DAYS` (default 30),
      `GEOCODE_NEGATIVE_TTL_DAYS` (default 7).

## After the beta week

Compare the app's counters (`gcreelmap show <trip> --stats`, or the
`places_lookups:YYYY-MM-DD` / `gemini_calls:YYYY-MM-DD` rows in the `kv`
table) against actual Google Cloud Billing. Record both here and adjust the
defaults above if real usage differs materially from what was assumed.

- Actual Gemini cost per reel: _(fill in after beta)_
- Actual Places cost per trip: _(fill in after beta)_
- Cache hit rate observed: _(fill in after beta)_
