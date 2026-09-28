# Terrain

A [Human Terrain](https://pudding.cool/2018/10/city_3d/) style population map for iPhone, with filters.
Every US county, every US state and every country in the Americas, colored by whatever you pick:
20-somethings, women per man, young household income, single women, strip clubs, gun deaths,
voter turnout, and thirty more. Tap a place for its full sheet.

Install (registered iPhones): https://assiamahs.github.io/terrain/install.html

## How it is built

Nothing is built on a Mac. Two GitHub Actions do all the work:

- `data.yml` (ubuntu, weekly) runs `pipeline/build_data.py`, which pulls every number from public
  sources and writes `web/data/` (metrics, catalog, simplified GeoJSON). GitHub Pages serves it,
  so the app picks up new data without a rebuild.
- `ios.yml` (macos-26) compiles the SwiftUI app with XcodeGen, archives unsigned, cloud-signs an
  ad hoc IPA with the App Store Connect key and publishes `web/install.html`.

## Sources

| What | Source |
|---|---|
| Age, race, sex ratio, marriage, income, education, housing | US Census ACS 5-year 2023 |
| Bars, businesses | Census County Business Patterns 2022 |
| Homicides, firearm deaths, chlamydia, excessive drinking, teen births | County Health Rankings 2025 |
| 2024 turnout and party lean | county-level presidential returns (tonmcg) + ACS citizen voting-age population |
| Strip clubs | OpenStreetMap `amenity=stripclub` via Overpass |
| Boundaries | Census cartographic boundaries 2023 (20m), Natural Earth 110m |
| Countries | CIA World Factbook (factbook.json) |

Asked for but not publicly available by location: cosmetic surgery counts, inheritance, social app
usage, porn use, interracial marriage (needs a PUMS run). The app lists these with the reason.

## Secrets

`CENSUS_API_KEY` (free, api.census.gov) for the data job; `ASC_KEY_ID` / `ASC_ISSUER_ID` /
`ASC_KEY_P8` for signing.
