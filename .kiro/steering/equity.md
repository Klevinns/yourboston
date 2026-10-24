---
inclusion: always
---

# Equity & fair-housing rules (HARD — audit B2, redline risk)

## The rule
**Demographics, race, ethnicity, language, and EJ2020 environmental-justice status are NEVER scored, weighted, filtered, ranked, sorted, colored, or used as a must-have.** They appear only as **prose / context text** in the tract click-through panel.

Why: letting users filter or rank neighborhoods by who lives there reproduces redlining and steering, which fair-housing law and basic ethics prohibit. FitMap scores places by cost, access, and safety — never by people.

## What this means in code
- The scoring module (`web/js/scoring.js`) accepts only the seven criterion keys: `rent`, `value`, `burden`, `commute`, `transit`, `campus`, `crime`. No other key may influence a score.
- Prose fields are stored under the `ctx_` prefix (e.g. `ctx_lang`, `ctx_race`, `ctx_ej`). `scoring.js` must not reference any `ctx_` field — enforced by a test.
- No UI control (toggle, slider, rank, filter, legend, sort, search) may be bound to a `ctx_` field.
- The choropleth is never colored by a `ctx_` field, not even as an optional layer. (EJ2020 is an "overlay" in the design sense only: it is shown as text in the panel.)
- No derived metric may smuggle demographics into a score (e.g. no "diversity index", no "language match", no proxy built from DP05/B16001/EJ2020).

## How prose must be written
- Neutral, factual, sourced: "About 34% of residents age 5+ speak a language other than English at home (ACS 2020–2024, B16001)." Never evaluative ("diverse", "up-and-coming", "safe for families").
- Always name the source and vintage. Show margins of error or "estimate" language where relevant.
- Present EJ2020 as a public-policy designation that unlocks resources: "Part of this tract is a Massachusetts Environmental Justice block group (criteria: income, minority). EJ areas get extra review and outreach for permits and programs."
- Language data is used to **serve** users (e.g. noting translated services in the next-step panel), never to sort them.

## Other equity guardrails
- Crime is a **town-level** rate, uniform across the town — say so in the panel. Do not imply block-level precision. Exclude non-municipal agencies (MBTA Transit Police, State Police, campus police) so they don't distort host towns.
- Car commute times are free-flow; say so. Transit and walking are first-class modes, not afterthoughts.
- Accessibility: keyboard-operable controls (reorder via buttons, not drag-only), pattern + color for excluded tracts, colorblind-safe palette, ARIA live announcements when the map recolors.
- Plain language throughout (challenge V1).

## Review checklist (run before every commit touching scoring or UI)
1. Does any score, weight, filter, or color depend on a `ctx_` field? → must be **no**.
2. Is every prose sentence sourced, dated, and non-evaluative? → must be **yes**.
3. Is crime presented as town-level? → must be **yes**.
