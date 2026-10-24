# Requirements Document — FitMap

## Introduction

FitMap is a static web map of census tracts within 10 miles of Boston that scores each tract 0–100 against a user's priorities (budget, commute, transit, campus, safety, financial comfort) and explains every score in plain language with a next step. It serves Hackachusetts 2026, Civic Action track, challenge V1 ("One task. Too many sites and agencies. → Plain language. Show the next step.").

Requirements use EARS notation. "THE System" means the pipeline plus the static frontend unless a requirement names one. Correctness properties derived from these requirements are listed in `design.md` §Correctness Properties and are implemented as property-based tests.

## Glossary

- **Tract** — 2020 Census tract, identified by an 11-digit GEOID string (2 state + 3 county + 6 tract).
- **Study area** — tracts whose TIGER internal point (INTPTLAT/INTPTLON) lies within 10 statute miles (16.0934 km) of the center point (see design).
- **Criterion** — one of exactly seven scored axes: `rent`, `value`, `burden`, `commute`, `transit`, `campus`, `crime`.
- **Active criteria** — criteria participating in scoring: all seven minus the inactive budget axis (per Rent/Buy switch) minus `commute` while no work tract is set.
- **Must-have** — a per-criterion binary hard floor with a user-set threshold.
- **Excluded tract** — a tract that fails at least one must-have; it has no score.
- **Context (ctx_) field** — prose-only data (language, demographics/race, EJ2020); never scored.
- **Work tract** — the tract containing the user's clicked work location.

## Requirements

### Requirement 1: Study area and tract base layer

**User Story:** As a person relocating near Boston, I want every neighborhood within reach shown on one map, so that I don't have to search city by city.

#### Acceptance Criteria

1. THE pipeline SHALL select all 2024 TIGER/Line census tracts in Massachusetts whose internal point lies within 10 miles of the configured Boston center point, and no others.
2. THE pipeline SHALL join ACS attributes to tract geometry on the 11-digit GEOID string.
3. THE pipeline SHALL emit every GEOID as an 11-character string of digits, unique within the output.
4. THE pipeline SHALL emit features sorted ascending by GEOID with an `idx` property equal to each feature's array position.
5. IF a tract has zero households or zero population, THEN THE pipeline SHALL still emit its geometry with null criterion values so the map has no holes.

### Requirement 2: Census values and sentinel handling

**User Story:** As a user, I want the numbers to be real, so that a Census error code never shows up as a price.

#### Acceptance Criteria

1. THE pipeline SHALL fetch ACS 2020–2024 5-year tables B25064 (rent), B25077 (value), B25070 (renter burden), B25091 (owner burden), B16001 (language), and DP05 (demographics) for study-area tracts.
2. WHEN an ACS estimate or margin of error is a Census sentinel/annotation value (any negative value, including -666666666, -999999999, -888888888, -222222222, -333333333, -555555555), THE pipeline SHALL store null instead of the number.
3. THE pipeline SHALL never emit a negative rent, home value, cost-burden percentage, distance, time, or crime rate.
4. WHEN B25077 is at the ACS top-code, THE pipeline SHALL set `value_topcoded = true` and keep the top-coded value for ranking.
5. WHEN the B25077 margin of error exceeds 30% of the estimate, THE pipeline SHALL set `value_moe_high = true`.
6. THE pipeline SHALL read the Census API key only from the `CENSUS_API_KEY` environment variable and SHALL NOT write it to any file, log, or output.

### Requirement 3: Cost burden ("financial comfort")

**User Story:** As a user, I want to know how stretched households already are in a neighborhood, so that a low rent that reflects low incomes doesn't look like a bargain.

#### Acceptance Criteria

1. THE pipeline SHALL compute `burden_pct` as the percentage of renter and owner households (B25070 + B25091, excluding "Not computed") paying 30% or more of income on housing.
2. THE pipeline SHALL also emit `burden_renter_pct` and `burden_owner_pct` for the breakdown panel.
3. THE pipeline SHALL emit every burden percentage in [0, 100] or null.

### Requirement 4: Crime (town-level)

**User Story:** As a user, I want a sense of safety, so that I can weigh it against cost without being misled by false precision.

#### Acceptance Criteria

1. THE pipeline SHALL compute per-1,000-resident rates of serious (violent) and property offenses per municipality from EOPSS NIBRS/UCR data for a single stated year.
2. THE pipeline SHALL exclude non-municipal reporting agencies (MBTA Transit Police, State Police, college/university police) from town rates.
3. THE pipeline SHALL assign each tract the rates of the town (county subdivision) containing its internal point, uniformly across the town.
4. WHEN a tract is clicked, THE frontend SHALL state that crime is a town-level rate, uniform across the town, with the source and year.

### Requirement 5: Campus proximity and transit accessibility

**User Story:** As a student or commuter, I want to see how close a neighborhood is to a campus and to fast transit.

#### Acceptance Criteria

1. THE pipeline SHALL compute `campus_km`, the great-circle distance from each tract internal point to the nearest MassGIS HigherEd campus, and `campus_name`.
2. THE pipeline SHALL compute `transit_walk_min`, the OSRM foot-profile walking time from each tract internal point to the nearest MBTA rapid-transit or commuter-rail station (GTFS route types 0, 1, 2), and `transit_station`.

### Requirement 6: Tract-to-tract commute

**User Story:** As a worker, I want to pick where I work anywhere on the map and see commute times by walking, biking, driving, and transit, so that I can choose a realistic place to live.

#### Acceptance Criteria

1. THE pipeline SHALL precompute commute minutes from every study-area tract to every study-area tract for four modes: walk, bike, car (self-hosted OSRM `table`), and transit (MBTA GTFS).
2. THE pipeline SHALL compute transit time as walk-to-stop + scheduled waiting/in-vehicle/transfer time + walk-from-stop, as the median over weekday departures at 07:30, 08:00, and 08:30.
3. THE pipeline SHALL encode each mode's vector as N unsigned bytes of whole minutes (0–254) with 255 meaning "no route or > 254 min", base64-encoded, one string per mode per tract.
4. THE System SHALL NOT call Google Distance Matrix or any billed routing API.
5. WHEN the user clicks the map in "set work location" mode, THE frontend SHALL snap the point to the containing study-area tract and set it as the work tract.
6. IF the clicked point lies outside every study-area tract, THEN THE frontend SHALL keep the previous work tract and show a plain-language message.
7. THE frontend SHALL use the fastest of the user-enabled modes as the commute value; by default all four modes are enabled.
8. THE frontend SHALL label car times as "free-flow, no traffic".

### Requirement 7: Scoring

**User Story:** As a user, I want one 0–100 score per tract that reflects my priorities, so that I can see the best fits at a glance.

#### Acceptance Criteria

1. THE frontend SHALL compute each tract's score only from the seven criterion values; no other field SHALL influence a score.
2. THE frontend SHALL derive criterion weights from the importance ranks of the active criteria such that every weight is in (0, 1] and the weights sum to 1.
3. THE frontend SHALL map each criterion value to a subscore in [0, 1] using its mid-rank percentile among all study-area tracts with a non-null value, oriented so that 1 is best (lower rent, value, burden, commute, walk time, distance, crime are better).
4. THE frontend SHALL compute the score as 100 × the weighted mean of available subscores, in [0, 100].
5. WHEN a nice-to-have criterion value is null for a tract, THE frontend SHALL renormalize the remaining weights for that tract and flag the missing criterion in the breakdown.
6. WHEN every active criterion value is null for a tract, THE frontend SHALL show the tract as "not enough data" with no score.
7. THE frontend SHALL produce identical scores for identical inputs regardless of feature order or criterion listing order (deterministic, order-independent).
8. WHEN one criterion value of a tract improves and nothing else changes, THE frontend SHALL NOT lower that tract's score.
9. THE frontend SHALL treat a commute value of 255 (no route) as the worst possible commute, not as missing.

### Requirement 8: Must-haves and importance ranks

**User Story:** As a user, I want to mark deal-breakers and reorder what matters, so that the map reflects my real constraints.

#### Acceptance Criteria

1. THE frontend SHALL provide, for each active criterion, a must-have toggle, a threshold input, and move-up / move-down controls for its rank.
2. WHEN a tract fails any must-have threshold, THE frontend SHALL exclude it: no score, grey pattern fill, and the failing criteria listed in its breakdown.
3. WHEN a tract's value for a must-have criterion is null, THE frontend SHALL show it as "not enough data" (distinct pattern) rather than as passing.
4. WHEN the app loads, THE frontend SHALL default the active budget axis and commute to must-have and all other criteria to nice-to-have.
5. WHEN the app loads, THE frontend SHALL default every threshold to "no limit" and prompt the user to enter a budget and pick a work location.
6. WHEN the user reorders ranks, toggles a must-have, changes a threshold, toggles Rent/Buy, or moves the work tract, THE frontend SHALL recompute weights and recolor the map without a page reload.
7. WHEN zero tracts pass all must-haves, THE frontend SHALL show an empty state naming the must-have that excludes the most tracts and suggesting how to relax it.
8. THE frontend SHALL NOT offer a must-have, rank, threshold, filter, sort, or color option for any context (ctx_) field.

### Requirement 9: Rent / Buy switch and buy-value display

**User Story:** As a renter or a buyer, I want the budget criterion to match my situation, and I want honest home-value data.

#### Acceptance Criteria

1. THE frontend SHALL provide a Rent / Buy switch; WHILE "Rent" is selected the `rent` criterion SHALL be active and `value` inactive, and WHILE "Buy" is selected the reverse.
2. THE frontend SHALL class buy values by percentile rank over the full study area (quintiles), not by equal-interval dollar bins.
3. THE frontend SHALL place top-coded tracts in a separate legend bin labeled "$2M+".
4. WHERE `value_moe_high` is true, THE frontend SHALL hatch the tract in the buy-value display rather than drop it.
5. THE frontend SHALL display the label "Median owner-estimated home value, ACS 2020–2024 5-year (B25077). Self-reported; lags market prices. Hatched tracts have high margin of error." wherever buy value is shown.

### Requirement 10: Tract breakdown, context prose, next step

**User Story:** As a user, I want to know why a tract got its color and what to do next, in plain language.

#### Acceptance Criteria

1. WHEN the user clicks a tract, THE frontend SHALL show its total score (or exclusion reason) and, per active criterion: raw value with units, subscore, weight, weighted contribution, and must-have pass/fail.
2. WHEN the user clicks a tract, THE frontend SHALL show context prose for language (B16001), demographics (DP05), and EJ2020 status, each with source and vintage, using neutral non-evaluative wording.
3. WHEN the user clicks a tract, THE frontend SHALL show a next-step panel with at least three plain-language actions, each linking to a URL listed in `web/data/next-steps.json`.
4. THE project SHALL include in `web/data/next-steps.json` only URLs that returned HTTP 200 when last verified, with the verification date recorded.
5. THE frontend SHALL be fully operable by keyboard and SHALL announce map recolors (number of passing tracts) via an ARIA live region.

### Requirement 11: Static output and delivery

**User Story:** As a judge or user, I want the site to load fast from a public URL with no backend.

#### Acceptance Criteria

1. THE pipeline SHALL write exactly one GeoJSON FeatureCollection at `web/data/fitmap.geojson` containing geometry, criterion values, context fields, and commute vectors.
2. THE pipeline SHALL keep `web/data/fitmap.geojson` at or below 2,000,000 bytes gzip-compressed.
3. THE System SHALL deploy `web/` to GitHub Pages with no server-side code.
4. THE repository SHALL commit the `.kiro/` folder at the repo root and SHALL NOT list it in `.gitignore`.
5. THE repository SHALL NOT contain API keys or other secrets.

### Requirement 12: Equity guardrail (audit B2)

**User Story:** As a fair-housing-conscious builder, I want it to be impossible for FitMap to sort people by race or language.

#### Acceptance Criteria

1. THE System SHALL never score, weight, filter, rank, sort, or color tracts by language, demographics/race, or EJ2020 status.
2. THE frontend scoring module SHALL NOT reference any `ctx_`-prefixed field.
3. THE System SHALL present language, demographics/race, and EJ2020 only as sourced prose in the tract panel.
