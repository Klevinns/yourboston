---
inclusion: always
---

# Product: FitMap

## One-liner
FitMap answers one question for a person moving to Greater Boston: **"Which neighborhoods fit my budget, my commute, and my life?"** — on one map, in plain language, with the next step shown.

## Why it exists (Hackachusetts 2026 · Civic Action · Challenge V1)
- Challenge **V1 — Simplified public services**: "One task. Too many sites and agencies." → *Plain language. Show the next step.*
- Theme: "Better Together: Improving Where We Live, Work, Study & Play."
- Today the housing-fit task is spread across the Census Bureau (rent/value/burden), the MBTA (transit), MassGIS (campuses), EOPSS (crime) and assorted trip planners. FitMap joins them per census tract and explains every color.

## Target users
- Students, early-career workers, and families relocating within ~10 miles of Boston.
- People with limited time, limited English, or limited data literacy. Copy must be plain language (aim for ~8th-grade reading level), no jargon without a one-line explanation.

## Core experience (locked)
1. **Choropleth** of census tracts within **10 miles of Boston**, colored by a weighted total score 0–100.
2. **Work location**: click anywhere on the map → snapped to the containing tract (tract-to-tract commute; no fixed hubs).
3. **Seven scored criteria**: rent · buy value · cost burden ("financial comfort") · commute to work · transit accessibility · campus proximity · crime.
4. Each criterion has a **must-have toggle** (hard floor, binary, user-set threshold) and an **importance rank** (reorderable). Reordering recomputes weights and recolors the map live.
5. Defaults: **budget (active rent-or-buy axis) + commute time = must-have**; all others nice-to-have.
6. **Rent / Buy switch** chooses which budget axis is active.
7. **Click a tract** → per-criterion breakdown ("why this color"), grounded context prose, and a **next-step panel** (who to call / where to go next).
8. Tracts failing a must-have are shown in a **grey pattern** (not removed). An **empty state** explains what to relax when nothing passes.

## Never scored (prose only)
Language, demographics/race, and EJ2020 environmental-justice status are **context text in the click-through panel only**. See `equity.md` — this is a hard rule.

## Out of scope (cut — do not reintroduce)
Redfin (ZIP-level, can't feed a tract map) · Google Distance Matrix (N² lookups unbillable) · YMCA/BGC points · Vision Zero crashes · industry / salary inputs (folded into budget).

## Success criteria for the demo (< 3 min video)
- Set work location → set rent budget → watch the map recolor → reorder priorities → click a tract → read the breakdown and next step.
- Every number on screen is traceable to a named public source and year.

## Labels that must appear verbatim
- Buy value: "Median owner-estimated home value, ACS 2020–2024 5-year (B25077). Self-reported; lags market prices. Hatched tracts have high margin of error."
