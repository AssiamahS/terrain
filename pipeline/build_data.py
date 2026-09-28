#!/usr/bin/env python3
"""Build the Terrain dataset: county / state / country metrics + geometry.

Runs in GitHub Actions (ubuntu). Everything is pulled from public sources at
run time; nothing is hand-typed. Outputs land in web/data/ and are served from
GitHub Pages, so the app updates without a rebuild.

Sources
  ACS 5-year 2023 (api.census.gov, needs CENSUS_API_KEY)  demographics, money, marriage
  County Business Patterns 2022 (api.census.gov)          bars, establishments
  County Health Rankings 2025 CSV                         homicides, firearm deaths, STIs, drinking, teen births
  tonmcg county-level presidential results (GitHub)      2024 turnout + party lean
  OpenStreetMap via Overpass                              strip clubs (amenity=stripclub)
  Census cartographic boundaries 2023 (20m)               county + state polygons
  Natural Earth 110m + CIA World Factbook (factbook.json) the Americas at country level
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data"
OUT.mkdir(parents=True, exist_ok=True)

KEY = os.environ.get("CENSUS_API_KEY", "").strip()
UA = {"User-Agent": "terrain-pipeline/1.0 (github.com/AssiamahS/terrain)"}
ACS = "https://api.census.gov/data/2023/acs/acs5"
CBP = "https://api.census.gov/data/2022/cbp"
CHR = "https://www.countyhealthrankings.org/sites/default/files/media/document/analytic_data2025.csv"
OVERPASS = "https://overpass-api.de/api/interpreter"
COUNTY_SHP = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_20m.zip"
STATE_SHP = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_20m.zip"
NE_COUNTRIES = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
FACTBOOK = "https://raw.githubusercontent.com/factbook/factbook.json/master/{region}/{code}.json"

# ---------------------------------------------------------------- ACS variables
ACS_VARS = {
    "pop": "B01003_001E", "median_age": "B01002_001E",
    "white": "B02001_002E", "black": "B02001_003E", "asian": "B02001_005E", "hispanic": "B03003_003E",
    "male": "B01001_002E", "female": "B01001_026E",
    "m20": "B01001_008E", "m21": "B01001_009E", "m22_24": "B01001_010E", "m25_29": "B01001_011E", "m30_34": "B01001_012E",
    "f20": "B01001_032E", "f21": "B01001_033E", "f22_24": "B01001_034E", "f25_29": "B01001_035E", "f30_34": "B01001_036E",
    "men15": "B12001_002E", "men_never": "B12001_003E", "men_married": "B12001_005E",
    "women15": "B12001_011E", "women_never": "B12001_012E", "women_married": "B12001_014E", "women_divorced": "B12001_019E",
    "marriage_age_m": "B12007_001E", "marriage_age_f": "B12007_002E",
    "income": "B19013_001E", "income_25_44": "B19049_003E",
    "hh": "B19054_001E", "hh_invest": "B19054_002E",
    "pop25": "B15003_001E", "ba": "B15003_022E", "ma": "B15003_023E", "prof": "B15003_024E", "phd": "B15003_025E",
    "cvap": "B29001_001E",
    "pop_nat": "B05002_001E", "foreign": "B05002_013E",
    "home_value": "B25077_001E", "rent": "B25064_001E",
    "labor": "B23025_003E", "unemployed": "B23025_005E",
}

# ---------------------------------------------------------------- catalog
# id, label, group, unit, one-line meaning, source, scopes
CATALOG = [
    # People
    ("pop", "Population", "People", "count", "Total residents.", "ACS 2023", ["county", "state", "country", "ca_cd", "mx_mun", "br_mun"]),
    ("density", "People per sq mile", "People", "count", "Population divided by land area.", "ACS 2023 + TIGER", ["county", "state"]),
    ("median_age", "Median age", "People", "years", "Half the residents are younger than this.", "ACS 2023 / Factbook", ["county", "state", "country", "ca_cd"]),
    ("age20s_pct", "20-somethings", "People", "pct", "Share of residents aged 20 to 29.", "ACS 2023", ["county", "state", "ca_cd", "mx_mun", "br_mun"]),
    ("age30_34_pct", "Aged 30 to 34", "People", "pct", "Share of residents aged 30 to 34.", "ACS 2023", ["county", "state", "ca_cd", "mx_mun", "br_mun"]),
    ("asian_pct", "Asian", "People", "pct", "Residents identifying as Asian alone.", "ACS 2023", ["county", "state"]),
    ("black_pct", "Black", "People", "pct", "Residents identifying as Black alone.", "ACS 2023", ["county", "state", "ca_cd"]),
    ("hispanic_pct", "Hispanic or Latino", "People", "pct", "Residents of Hispanic or Latino origin.", "ACS 2023", ["county", "state"]),
    ("white_pct", "White", "People", "pct", "Residents identifying as White alone.", "ACS 2023", ["county", "state"]),
    ("foreign_pct", "Foreign-born", "People", "pct", "Residents born outside the US.", "ACS 2023", ["county", "state", "ca_cd"]),
    # Sex & relationships
    ("f_per_m", "Women per man", "Sex & relationships", "ratio", "Females for every male, all ages.", "ACS 2023 / Factbook", ["county", "state", "country", "ca_cd", "mx_mun", "br_mun"]),
    ("f_per_m_20_34", "Women per man, 20 to 34", "Sex & relationships", "ratio", "Females per male among 20 to 34 year olds.", "ACS 2023", ["county", "state", "ca_cd", "mx_mun", "br_mun"]),
    ("single_women_pct", "Single women", "Sex & relationships", "pct", "Women 15+ who have never married.", "ACS 2023", ["county", "state"]),
    ("married_women_pct", "Married women", "Sex & relationships", "pct", "Women 15+ married with spouse present.", "ACS 2023", ["county", "state"]),
    ("divorced_women_pct", "Divorced women", "Sex & relationships", "pct", "Women 15+ currently divorced.", "ACS 2023", ["county", "state"]),
    ("single_men_pct", "Single men", "Sex & relationships", "pct", "Men 15+ who have never married.", "ACS 2023", ["county", "state"]),
    ("marriage_age_f", "Women's age at first marriage", "Sex & relationships", "years", "Median age women first marry. Census publishes this for states only.", "ACS 2023", ["state"]),
    ("marriage_age_m", "Men's age at first marriage", "Sex & relationships", "years", "Median age men first marry. Census publishes this for states only.", "ACS 2023", ["state"]),
    ("teen_births", "Teen births per 1,000", "Sex & relationships", "count", "Births per 1,000 females aged 15 to 19.", "County Health Rankings 2025", ["county", "state"]),
    ("sti_rate", "Chlamydia per 100k", "Sex & relationships", "count", "Newly diagnosed chlamydia cases per 100,000.", "County Health Rankings 2025", ["county", "state"]),
    # Money
    ("income", "Median household income", "Money", "money", "Half of households earn more than this.", "ACS 2023", ["county", "state"]),
    ("income_25_44", "Young household income", "Money", "money", "Median income where the householder is 25 to 44.", "ACS 2023", ["county", "state"]),
    ("invest_pct", "Households with investment income", "Money", "pct", "Households reporting interest, dividend or rental income. Closest public proxy for family money.", "ACS 2023", ["county", "state"]),
    ("degree_pct", "Bachelor's or higher", "Money", "pct", "Adults 25+ with at least a bachelor's degree.", "ACS 2023", ["county", "state", "ca_cd"]),
    ("home_value", "Median home value", "Money", "money", "Owner-estimated median home value.", "ACS 2023", ["county", "state"]),
    ("rent", "Median rent", "Money", "money", "Median gross monthly rent.", "ACS 2023", ["county", "state"]),
    ("unemployment", "Unemployment", "Money", "pct", "Unemployed share of the civilian labor force.", "ACS 2023", ["county", "state"]),
    ("gdp_pc", "GDP per person", "Money", "money", "Real GDP per capita, PPP.", "CIA World Factbook", ["country"]),
    # Nightlife & business
    ("bars_per_10k", "Bars per 10k people", "Nightlife & business", "rate", "Drinking places (NAICS 722410) per 10,000 residents.", "County Business Patterns 2022", ["county", "state"]),
    ("stripclubs", "Strip clubs", "Nightlife & business", "count", "Venues tagged amenity=stripclub on OpenStreetMap.", "OpenStreetMap", ["county", "state"]),
    ("stripclubs_per_100k", "Strip clubs per 100k", "Nightlife & business", "rate", "Strip clubs per 100,000 residents (counties over 20k people).", "OpenStreetMap + ACS", ["county", "state"]),
    ("estab_per_1k", "Businesses per 1k people", "Nightlife & business", "rate", "All establishments per 1,000 residents.", "County Business Patterns 2022", ["county", "state"]),
    ("drinking_pct", "Excessive drinking", "Nightlife & business", "pct", "Adults reporting binge or heavy drinking.", "County Health Rankings 2025", ["county", "state"]),
    # Crime & civic
    ("homicides", "Homicides per 100k", "Crime & civic", "rate", "Deaths from homicide per 100,000 (multi-year).", "County Health Rankings 2025", ["county", "state"]),
    ("firearm_deaths", "Firearm deaths per 100k", "Crime & civic", "rate", "Firearm fatalities per 100,000 (multi-year).", "County Health Rankings 2025", ["county", "state"]),
    ("turnout", "Voter turnout 2024", "Crime & civic", "pct", "2024 presidential votes divided by citizen voting-age population.", "County returns 2024 + ACS", ["county", "state"]),
    ("dem_share", "Democratic share 2024", "Crime & civic", "pct", "Democratic share of the 2024 presidential vote.", "County returns 2024", ["county", "state"]),
    ("rep_share", "Republican share 2024", "Crime & civic", "pct", "Republican share of the 2024 presidential vote.", "County returns 2024", ["county", "state"]),
    # Country only
    ("urban_pct", "Urban population", "People", "pct", "Share living in urban areas.", "CIA World Factbook", ["country"]),
    ("growth", "Population growth", "People", "pct", "Annual population growth rate.", "CIA World Factbook", ["country"]),
    ("unemployment_country", "Unemployment", "Money", "pct", "Unemployment rate.", "CIA World Factbook", ["country"]),
    ("literacy", "Literacy", "People", "pct", "Adults who can read and write.", "CIA World Factbook", ["country"]),
    ("life_exp", "Life expectancy", "People", "years", "Life expectancy at birth.", "CIA World Factbook", ["country"]),
    ("net_migration", "Net migration per 1,000", "People", "count", "Net migrants per 1,000 people per year.", "CIA World Factbook", ["country"]),
    # Looks pack (CDC PLACES 2024 = 2022 BRFSS, County Business Patterns, Google Trends)
    ("fit_groomed", "Fit & groomed score", "Looks", "count", "0 to 100. Average rank of low obesity, low inactivity, gyms and salons per capita. Closest public stand-in for 'attractive'.", "CDC PLACES + CBP", ["county", "state"]),
    ("obesity_pct", "Obesity", "Looks", "pct", "Adults with BMI 30 or more.", "CDC PLACES 2024", ["county", "state"]),
    ("inactivity_pct", "No exercise", "Looks", "pct", "Adults with no leisure-time physical activity.", "CDC PLACES 2024", ["county", "state"]),
    ("gyms_per_10k", "Gyms per 10k", "Looks", "rate", "Fitness and recreational sports centers (NAICS 713940) per 10,000 residents.", "County Business Patterns 2022", ["county", "state"]),
    ("salons_per_10k", "Beauty & nail salons per 10k", "Looks", "rate", "Beauty salons plus nail salons (NAICS 812112, 812113) per 10,000 residents.", "County Business Patterns 2022", ["county", "state"]),
    ("trend_bbl", "BBL search interest", "Looks", "count", "Google search interest for 'BBL', past 12 months, 0 to 100 by state. Counties show their state.", "Google Trends", ["county", "state"]),
    ("trend_tinder", "Tinder search interest", "Looks", "count", "Google search interest for Tinder, 0 to 100 by state.", "Google Trends", ["county", "state"]),
    ("trend_hinge", "Hinge search interest", "Looks", "count", "Google search interest for Hinge, 0 to 100 by state.", "Google Trends", ["county", "state"]),
    ("trend_onlyfans", "OnlyFans search interest", "Looks", "count", "Google search interest for OnlyFans, 0 to 100 by state.", "Google Trends", ["county", "state"]),
    ("depression_pct", "Depression", "Looks", "pct", "Adults ever told they have depression.", "CDC PLACES 2024", ["county", "state"]),
    ("binge_pct", "Binge drinking", "Looks", "pct", "Adults reporting binge drinking.", "CDC PLACES 2024", ["county", "state"]),
    ("smoking_pct", "Smoking", "Looks", "pct", "Adults who currently smoke.", "CDC PLACES 2024", ["county", "state"]),
    ("isolation_pct", "Socially isolated", "Looks", "pct", "Adults who feel socially isolated.", "CDC PLACES 2024", ["county", "state"]),
    # Interracial marriage (PUMS)
    ("interracial_pct", "Interracial couples", "Sex & relationships", "pct", "Married couples whose spouses are of different race or Hispanic origin. Counties inherit their PUMA average.", "ACS PUMS 2023", ["county", "state"]),
    # FBI state estimates
    ("violent_crime", "Violent crime per 100k", "Crime & civic", "rate", "FBI state figure, latest year (2024). Counties show their state.", "FBI via OpenCrime", ["county", "state"]),
    ("robbery", "Robbery per 100k", "Crime & civic", "rate", "FBI state figure, latest year (2024).", "FBI via OpenCrime", ["county", "state"]),
    ("assault", "Aggravated assault per 100k", "Crime & civic", "rate", "FBI state figure, latest year (2024).", "FBI via OpenCrime", ["county", "state"]),
    ("rape", "Rape per 100k", "Crime & civic", "rate", "FBI state figure, latest year (2024).", "FBI via OpenCrime", ["county", "state"]),
    ("property_crime", "Property crime per 100k", "Crime & civic", "rate", "FBI state figure, latest year (2024).", "FBI via OpenCrime", ["county", "state"]),
    ("burglary", "Burglary per 100k", "Crime & civic", "rate", "FBI state figure, latest year. Counties show their state.", "FBI via OpenCrime", ["county", "state"]),
    ("vehicle_theft", "Vehicle theft per 100k", "Crime & civic", "rate", "FBI state figure, latest year. Counties show their state.", "FBI via OpenCrime", ["county", "state"]),
    # Canada / Mexico / Brazil
    ("married_pct", "Married or common-law", "Sex & relationships", "pct", "Share of people 15+ (12+ in Mexico) who are married or in a union.", "StatCan 2021 / INEGI 2020", ["ca_cd", "mx_mun"]),
    ("single_pct", "Single (never in a union)", "Sex & relationships", "pct", "Share of people 15+ (12+ in Mexico) never married or in a union.", "StatCan 2021 / INEGI 2020", ["ca_cd", "mx_mun"]),
    ("separated_pct", "Separated or divorced", "Sex & relationships", "pct", "Share of people 12+ separated, divorced or widowed.", "INEGI 2020", ["mx_mun"]),
    ("income_cad", "Median household income (CAD)", "Money", "money", "Median total household income, 2020, Canadian dollars.", "StatCan 2021", ["ca_cd"]),
    ("south_asian_pct", "South Asian", "People", "pct", "Share of residents who are South Asian.", "StatCan 2021", ["ca_cd"]),
    ("chinese_pct", "Chinese", "People", "pct", "Share of residents who are Chinese.", "StatCan 2021", ["ca_cd"]),
    ("filipino_pct", "Filipino", "People", "pct", "Share of residents who are Filipino.", "StatCan 2021", ["ca_cd"]),
    ("age65_pct", "Aged 65+", "People", "pct", "Share of residents 65 and older.", "INEGI 2020", ["mx_mun"]),
    ("schooling_years", "Years of schooling", "Money", "years", "Average years of schooling, people 15+.", "INEGI 2020", ["mx_mun"]),
    ("born_elsewhere_pct", "Born in another state", "People", "pct", "Residents born in a different Mexican state.", "INEGI 2020", ["mx_mun"]),
]

SCOPES = [
    {"id": "county", "title": "US counties", "geometry": "counties.geojson", "lat": 38.5, "lon": -96.5, "latDelta": 32, "lonDelta": 40},
    {"id": "state", "title": "US states", "geometry": "states.geojson", "lat": 38.5, "lon": -96.5, "latDelta": 32, "lonDelta": 40},
    {"id": "ca_cd", "title": "Canada", "geometry": "canada.geojson", "lat": 58, "lon": -96, "latDelta": 40, "lonDelta": 70},
    {"id": "mx_mun", "title": "Mexico", "geometry": "mexico.geojson", "lat": 23.5, "lon": -102, "latDelta": 20, "lonDelta": 24},
    {"id": "br_mun", "title": "Brazil", "geometry": "brazil.geojson", "lat": -14, "lon": -53, "latDelta": 40, "lonDelta": 40},
    {"id": "country", "title": "Americas", "geometry": "countries.geojson", "lat": 10, "lon": -80, "latDelta": 110, "lonDelta": 100},
]

SUGGESTIONS = [
    {"title": "Fit & groomed", "metric": "fit_groomed", "scope": "county"},
    {"title": "Interracial couples", "metric": "interracial_pct", "scope": "county"},
    {"title": "Women per man, Mexico", "metric": "f_per_m_20_34", "scope": "mx_mun"},
    {"title": "20-somethings, Brazil", "metric": "age20s_pct", "scope": "br_mun"},
    {"title": "Single Canada", "metric": "single_pct", "scope": "ca_cd"},
    {"title": "BBL country", "metric": "trend_bbl", "scope": "state"},
    {"title": "Where the 20-somethings are", "metric": "age20s_pct", "scope": "county"},
    {"title": "Most women per man (20 to 34)", "metric": "f_per_m_20_34", "scope": "county"},
    {"title": "Highest-paid young households", "metric": "income_25_44", "scope": "county"},
    {"title": "Where Asian Americans live", "metric": "asian_pct", "scope": "county"},
    {"title": "Most single women", "metric": "single_women_pct", "scope": "county"},
    {"title": "Married-women country", "metric": "married_women_pct", "scope": "county"},
    {"title": "Family money (investment income)", "metric": "invest_pct", "scope": "county"},
    {"title": "Strip club capitals", "metric": "stripclubs_per_100k", "scope": "county"},
    {"title": "Bar density", "metric": "bars_per_10k", "scope": "county"},
    {"title": "Gun deaths", "metric": "firearm_deaths", "scope": "county"},
    {"title": "Homicide rate", "metric": "homicides", "scope": "county"},
    {"title": "Who actually votes", "metric": "turnout", "scope": "county"},
    {"title": "Latest to marry (women)", "metric": "marriage_age_f", "scope": "state"},
    {"title": "Immigrant hubs", "metric": "foreign_pct", "scope": "county"},
    {"title": "Cheapest rent", "metric": "rent", "scope": "county", "ascending": True},
    {"title": "Youngest countries in the Americas", "metric": "median_age", "scope": "country", "ascending": True},
    {"title": "Richest countries in the Americas", "metric": "gdp_pc", "scope": "country"},
    {"title": "Women per man across the Americas", "metric": "f_per_m", "scope": "country"},
]

UNAVAILABLE = [
    {"topic": "Cosmetic surgery (BBL, breast augmentation)", "why": "ASPS publishes national and 5-region totals only. No city or county series exists."},
    {"topic": "Inheritance money", "why": "The Fed's Survey of Consumer Finances is national. Investment-income households (ACS B19054) is the closest local proxy and is included."},
    {"topic": "Facebook / Snapchat / Tinder / Bumble / Hinge / X usage", "why": "Platforms do not publish users by location. Pew surveys are national."},
    {"topic": "Porn use by location", "why": "Only ad-hoc state-level marketing posts exist, not a maintained dataset."},
    {"topic": "Attractiveness itself", "why": "No agency rates faces. The Looks group maps the measurable parts: obesity, exercise, gyms, salons, and what people search for."},
]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, **kw):
    import extra
    cached = extra.get(url, timeout=kw.pop("timeout", 120), tries=4, **kw) if extra.CACHE else None
    if cached is not None:
        return cached
    for attempt in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 120), **kw)
            if r.status_code == 200:
                return r
            log(f"  {url[:90]} -> {r.status_code}")
        except requests.RequestException as e:
            log(f"  {url[:90]} -> {e}")
        time.sleep(3 * (attempt + 1))
    raise SystemExit(f"failed: {url}")


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v <= -666666666 or math.isnan(v):  # ACS suppression sentinels
        return None
    return v


def div(a, b, scale=1.0):
    if a is None or b in (None, 0):
        return None
    return round(a / b * scale, 3)


# ---------------------------------------------------------------- Census API
def census(base, get_vars, geo, extra=""):
    if not KEY:
        raise SystemExit("CENSUS_API_KEY is not set")
    url = f"{base}?get={','.join(get_vars)}&for={geo}{extra}&key={KEY}"
    r = get(url)
    try:
        rows = r.json()
    except ValueError:
        raise SystemExit(f"census returned non-JSON for {geo}: {r.text[:200]}")
    head, body = rows[0], rows[1:]
    return [dict(zip(head, row)) for row in body]


def acs(geo):
    names = list(ACS_VARS)
    codes = [ACS_VARS[n] for n in names]
    out = {}
    for row in census(ACS, ["NAME"] + codes, geo):
        geoid = row["state"] + row.get("county", "")
        d = {n: num(row[c]) for n, c in zip(names, codes)}
        d["name"] = row["NAME"]
        out[geoid] = d
    return out


def cbp(geo):
    out = defaultdict(dict)
    for naics, key in (("00", "estab"), ("722410", "bars")):
        try:
            rows = census(CBP, ["ESTAB"], geo, f"&NAICS2017={naics}")
        except SystemExit as e:
            log(f"  CBP {naics} skipped: {e}")
            continue
        for row in rows:
            geoid = row["state"] + row.get("county", "")
            out[geoid][key] = num(row["ESTAB"])
    return out


# ---------------------------------------------------------------- County Health Rankings
CHR_COLS = {"homicides": "v015_rawvalue", "firearm_deaths": "v148_rawvalue", "teen_births": "v014_rawvalue",
            "sti_rate": "v045_rawvalue", "drinking_pct": "v049_rawvalue"}


def chr_data():
    r = get(CHR)
    lines = r.text.splitlines()
    # line 1 = human labels, line 2 = codes; use codes as header
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])))
    counties, states = {}, {}
    for row in reader:
        fips = (row.get("fipscode") or "").zfill(5)
        if not fips.strip("0"):
            continue
        d = {}
        for k, col in CHR_COLS.items():
            v = num(row.get(col))
            if v is None:
                continue
            if k == "drinking_pct":
                v = round(v * 100, 2)
            d[k] = v
        if row.get("countycode") == "000":
            states[fips[:2]] = d
        else:
            counties[fips] = d
    return counties, states


# ---------------------------------------------------------------- 2024 presidential returns
ELECTIONS = "https://raw.githubusercontent.com/tonmcg/US_County_Level_Election_Results_08-24/master/2024_US_County_Level_Presidential_Results.csv"


def elections():
    """County-level 2024 presidential returns (compiled from state SoS reports, public on GitHub).
    The MIT Election Lab file on Dataverse sits behind a guestbook, so it cannot be scripted."""
    try:
        r = get(ELECTIONS)
    except SystemExit:
        return {}, {}
    counties, st = {}, defaultdict(lambda: {"votes": 0.0, "dem": 0.0, "rep": 0.0})
    for row in csv.DictReader(io.StringIO(r.text)):
        fips = (row.get("county_fips") or "").zfill(5)
        tv, dem, rep = num(row.get("total_votes")), num(row.get("votes_dem")), num(row.get("votes_gop"))
        if not tv:
            continue
        counties[fips] = {"votes": tv, "dem": dem or 0, "rep": rep or 0}
        s = st[fips[:2]]
        s["votes"] += tv; s["dem"] += dem or 0; s["rep"] += rep or 0
    return counties, dict(st)


# ---------------------------------------------------------------- geometry
def shapefile_geojson(url):
    import shapefile  # pyshp
    r = get(url)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    base = [n for n in z.namelist() if n.endswith(".shp")][0][:-4]
    sf = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                          shx=io.BytesIO(z.read(base + ".shx")))
    feats = []
    for sr in sf.shapeRecords():
        rec = sr.record.as_dict()
        feats.append({"type": "Feature", "geometry": sr.shape.__geo_interface__, "properties": rec})
    return feats


def round_coords(obj, nd=4):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(obj[0], nd), round(obj[1], nd)]
        return [round_coords(x, nd) for x in obj]
    return obj


def overpass_stripclubs():
    q = '[out:json][timeout:180];nwr["amenity"="stripclub"](14,-170,72,-50);out center;'
    for attempt in range(3):
        try:
            r = requests.post(OVERPASS, data={"data": q}, headers=UA, timeout=240)
            if r.status_code == 200:
                pts = []
                for el in r.json()["elements"]:
                    if "lat" in el:
                        pts.append((el["lon"], el["lat"]))
                    elif "center" in el:
                        pts.append((el["center"]["lon"], el["center"]["lat"]))
                return pts
            log(f"  overpass -> {r.status_code}")
        except requests.RequestException as e:
            log(f"  overpass -> {e}")
        time.sleep(20)
    return []


def count_points_in(features, points):
    from shapely.geometry import shape, Point
    from shapely.strtree import STRtree
    geoms = [shape(f["geometry"]) for f in features]
    tree = STRtree(geoms)
    counts = defaultdict(int)
    for lon, lat in points:
        p = Point(lon, lat)
        for idx in tree.query(p):
            if geoms[idx].contains(p):
                counts[features[idx]["properties"]["GEOID"]] += 1
                break
    return counts


# ---------------------------------------------------------------- Factbook
FACTBOOK_CODES = {  # ISO2 -> (region folder, factbook code)
    "US": ("north-america", "us"), "CA": ("north-america", "ca"), "MX": ("north-america", "mx"), "GL": ("north-america", "gl"),
    "GT": ("central-america-n-caribbean", "gt"), "BZ": ("central-america-n-caribbean", "bh"), "HN": ("central-america-n-caribbean", "ho"),
    "SV": ("central-america-n-caribbean", "es"), "NI": ("central-america-n-caribbean", "nu"), "CR": ("central-america-n-caribbean", "cs"),
    "PA": ("central-america-n-caribbean", "pm"), "CU": ("central-america-n-caribbean", "cu"), "HT": ("central-america-n-caribbean", "ha"),
    "DO": ("central-america-n-caribbean", "dr"), "JM": ("central-america-n-caribbean", "jm"), "BS": ("central-america-n-caribbean", "bf"),
    "PR": ("central-america-n-caribbean", "rq"), "TT": ("central-america-n-caribbean", "td"),
    "BR": ("south-america", "br"), "AR": ("south-america", "ar"), "CL": ("south-america", "ci"), "CO": ("south-america", "co"),
    "PE": ("south-america", "pe"), "VE": ("south-america", "ve"), "EC": ("south-america", "ec"), "BO": ("south-america", "bl"),
    "PY": ("south-america", "pa"), "UY": ("south-america", "uy"), "GY": ("south-america", "gy"), "SR": ("south-america", "ns"),
    "FK": ("south-america", "fk"),
}


def fb_num(text):
    """'31 years (2025 est.)' -> 31.0 ; '$21,100 (2023 est.)' -> 21100 ; '0.96 male(s)/female' -> 0.96"""
    if not text:
        return None
    m = re.search(r"-?\$?([\d,]*\.?\d+)", text.replace(",", ""))
    return float(m.group(1)) if m else None


def fb_latest(d):
    """Factbook year-keyed dicts: {'Real GDP per capita 2024': {...}, 'note': ...} -> newest year's number."""
    if not isinstance(d, dict):
        return None
    years = [(int(re.search(r"(\d{4})$", k).group(1)), k) for k in d if re.search(r"\d{4}$", k)]
    for _, k in sorted(years, reverse=True):
        v = fb_num(fb_get(d, k))
        if v is not None:
            return v
    return None


def fb_get(d, *path):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    if isinstance(d, dict):
        d = d.get("text")
    return d


def factbook(iso2):
    region, code = FACTBOOK_CODES[iso2]
    try:
        r = requests.get(FACTBOOK.format(region=region, code=code), headers=UA, timeout=60)
        if r.status_code != 200:
            return {}
        d = r.json()
    except (requests.RequestException, ValueError):
        return {}
    ps, econ = d.get("People and Society", {}), d.get("Economy", {})
    out = {}
    out["pop"] = fb_num(fb_get(ps, "Population", "total") or fb_get(ps, "Population"))
    out["median_age"] = fb_num(fb_get(ps, "Median age", "total"))
    sr = fb_num(fb_get(ps, "Sex ratio", "total population"))
    out["f_per_m"] = round(1 / sr, 3) if sr else None
    out["urban_pct"] = fb_num(fb_get(ps, "Urbanization", "urban population"))
    out["growth"] = fb_num(fb_get(ps, "Population growth rate"))
    out["life_exp"] = fb_num(fb_get(ps, "Life expectancy at birth", "total population"))
    out["net_migration"] = fb_num(fb_get(ps, "Net migration rate"))
    out["literacy"] = fb_num(fb_get(ps, "Literacy", "total population"))
    out["gdp_pc"] = fb_latest(econ.get("Real GDP per capita"))
    out["unemployment_country"] = fb_latest(econ.get("Unemployment rate"))
    return {k: v for k, v in out.items() if v is not None}


# ---------------------------------------------------------------- derive metrics
def derive(a, b=None, h=None, e=None, clubs=0, aland=None):
    b, h, e = b or {}, h or {}, e or {}
    pop = a.get("pop")
    m = {"pop": pop, "median_age": a.get("median_age")}
    if aland:
        m["density"] = div(pop, aland / 2_589_988.11)  # m^2 -> sq mi
    young_m = sum(a.get(k) or 0 for k in ("m20", "m21", "m22_24", "m25_29", "m30_34"))
    young_f = sum(a.get(k) or 0 for k in ("f20", "f21", "f22_24", "f25_29", "f30_34"))
    twenties = sum(a.get(k) or 0 for k in ("m20", "m21", "m22_24", "m25_29", "f20", "f21", "f22_24", "f25_29"))
    m["age20s_pct"] = div(twenties, pop, 100)
    m["age30_34_pct"] = div((a.get("m30_34") or 0) + (a.get("f30_34") or 0), pop, 100)
    for k in ("asian", "black", "hispanic", "white"):
        m[f"{k}_pct"] = div(a.get(k), pop, 100)
    m["foreign_pct"] = div(a.get("foreign"), a.get("pop_nat"), 100)
    m["f_per_m"] = div(a.get("female"), a.get("male"))
    m["f_per_m_20_34"] = div(young_f, young_m) if young_m > 200 else None
    m["single_women_pct"] = div(a.get("women_never"), a.get("women15"), 100)
    m["married_women_pct"] = div(a.get("women_married"), a.get("women15"), 100)
    m["divorced_women_pct"] = div(a.get("women_divorced"), a.get("women15"), 100)
    m["single_men_pct"] = div(a.get("men_never"), a.get("men15"), 100)
    m["marriage_age_f"] = a.get("marriage_age_f")
    m["marriage_age_m"] = a.get("marriage_age_m")
    m["income"] = a.get("income")
    m["income_25_44"] = a.get("income_25_44")
    m["invest_pct"] = div(a.get("hh_invest"), a.get("hh"), 100)
    degrees = sum(a.get(k) or 0 for k in ("ba", "ma", "prof", "phd"))
    m["degree_pct"] = div(degrees, a.get("pop25"), 100)
    m["home_value"] = a.get("home_value")
    m["rent"] = a.get("rent")
    m["unemployment"] = div(a.get("unemployed"), a.get("labor"), 100)
    m["bars_per_10k"] = div(b.get("bars"), pop, 10_000)
    m["estab_per_1k"] = div(b.get("estab"), pop, 1_000)
    m["stripclubs"] = clubs
    m["stripclubs_per_100k"] = div(clubs, pop, 100_000) if pop and pop >= 20_000 else None
    for k in CHR_COLS:
        m[k] = h.get(k)
    if e.get("votes") and a.get("cvap"):
        m["turnout"] = min(100.0, round(e["votes"] / a["cvap"] * 100, 2))
        m["dem_share"] = div(e.get("dem"), e["votes"], 100)
        m["rep_share"] = div(e.get("rep"), e["votes"], 100)
    return {k: v for k, v in m.items() if v is not None}


def fit_score(table):
    """0-100: mean percentile rank of low obesity, low inactivity, gyms, salons (needs at least 3 of 4)."""
    parts = {"obesity_pct": -1, "inactivity_pct": -1, "gyms_per_10k": 1, "salons_per_10k": 1}
    ranks = {}
    for key, sign in parts.items():
        vals = sorted((m[key] * sign, g) for g, m in table.items() if key in m)
        n = len(vals)
        for i, (_, g) in enumerate(vals):
            ranks.setdefault(g, {})[key] = i / max(1, n - 1) * 100
    for g, r in ranks.items():
        if len(r) >= 3:
            table[g]["fit_groomed"] = round(sum(r.values()) / len(r), 1)


def main():
    t0 = time.time()
    import extra
    log("ACS counties"); acs_c = acs("county:*")
    log("ACS states"); acs_s = acs("state:*")
    log("CBP counties"); cbp_c = cbp("county:*")
    log("CBP states"); cbp_s = cbp("state:*")
    log("County Health Rankings")
    try:
        chr_c, chr_s = chr_data()
    except SystemExit as e:
        log(f"  skipped: {e}"); chr_c, chr_s = {}, {}
    log("2024 election returns"); mit_c, mit_s = elections()
    log("County shapes"); counties = shapefile_geojson(COUNTY_SHP)
    log("State shapes"); states = shapefile_geojson(STATE_SHP)
    log("Strip clubs (OSM)"); pts = overpass_stripclubs()
    clubs_c = count_points_in(counties, pts) if pts else {}
    clubs_s = defaultdict(int)
    for geoid, n in clubs_c.items():
        clubs_s[geoid[:2]] += n
    log(f"  {len(pts)} venues, {sum(clubs_c.values())} inside a county")

    log("CDC PLACES"); places = extra.cdc_places()
    log("CBP extra"); cbpx_c = extra.cbp_extra("county:*"); cbpx_s = extra.cbp_extra("state:*")
    log("Google Trends"); trends_s = extra.google_trends()
    log("FBI"); fbi_s = extra.fbi_states()
    log("PUMS interracial"); puma_vals, inter_s = extra.interracial(sorted({g[:2] for g in acs_c}))
    inter_c = extra.puma_to_county(puma_vals) if puma_vals else {}
    log(f"  interracial: {len(inter_c)} counties, {len(inter_s)} states")

    aland_c = {f["properties"]["GEOID"]: f["properties"]["ALAND"] for f in counties}
    aland_s = {f["properties"]["GEOID"]: f["properties"]["ALAND"] for f in states}

    metrics_c = {g: derive(a, cbp_c.get(g), chr_c.get(g), mit_c.get(g), clubs_c.get(g, 0), aland_c.get(g)) for g, a in acs_c.items()}
    metrics_s = {g: derive(a, cbp_s.get(g), chr_s.get(g), mit_s.get(g), clubs_s.get(g, 0), aland_s.get(g)) for g, a in acs_s.items()}
    for g, m in metrics_c.items():
        st = g[:2]
        m.update(places.get(g, {}))
        x = cbpx_c.get(g, {})
        pop = m.get("pop")
        if x.get("gyms") is not None: m["gyms_per_10k"] = div(x["gyms"], pop, 10_000)
        sal = (x.get("beauty_salons") or 0) + (x.get("nail_salons") or 0)
        if sal and pop: m["salons_per_10k"] = div(sal, pop, 10_000)
        m.update(trends_s.get(st, {}))
        m.update(fbi_s.get(st, {}))
        if g in inter_c: m["interracial_pct"] = inter_c[g]
    for g, m in metrics_s.items():
        x = cbpx_s.get(g, {})
        pop = m.get("pop")
        if x.get("gyms") is not None: m["gyms_per_10k"] = div(x["gyms"], pop, 10_000)
        sal = (x.get("beauty_salons") or 0) + (x.get("nail_salons") or 0)
        if sal and pop: m["salons_per_10k"] = div(sal, pop, 10_000)
        m.update(trends_s.get(g, {}))
        m.update(fbi_s.get(g, {}))
        if g in inter_s: m["interracial_pct"] = inter_s[g]
        # state PLACES = population-weighted mean of its counties
        for key in extra.PLACES_MEASURES.values():
            rows = [(metrics_c[c][key], metrics_c[c].get("pop") or 0) for c in metrics_c if c.startswith(g) and key in metrics_c[c]]
            if rows and sum(w for _, w in rows):
                m[key] = round(sum(v * w for v, w in rows) / sum(w for _, w in rows), 2)
    fit_score(metrics_c); fit_score(metrics_s)

    # names
    names_c = {g: a["name"] for g, a in acs_c.items()}
    names_s = {g: a["name"] for g, a in acs_s.items()}

    def slim(feats, names, keep):
        out = []
        for f in feats:
            p = f["properties"]
            gid = p["GEOID"]
            if gid not in keep:
                continue
            out.append({"type": "Feature", "id": gid,
                        "properties": {"name": names.get(gid, p.get("NAME")), "st": p.get("STUSPS", "")},
                        "geometry": {"type": f["geometry"]["type"], "coordinates": round_coords(f["geometry"]["coordinates"])}})
        return {"type": "FeatureCollection", "features": out}

    log("Countries"); ne = get(NE_COUNTRIES).json()
    countries, metrics_k = [], {}
    for f in ne["features"]:
        p = f["properties"]
        if p.get("CONTINENT") not in ("North America", "South America"):
            continue
        iso = p.get("ISO_A2_EH") or p.get("ISO_A2")
        if iso not in FACTBOOK_CODES:
            continue
        fb = factbook(iso)
        if not fb:
            continue
        metrics_k[iso] = fb
        countries.append({"type": "Feature", "id": iso, "properties": {"name": p.get("NAME_EN") or p.get("NAME"), "st": ""},
                          "geometry": {"type": f["geometry"]["type"], "coordinates": round_coords(f["geometry"]["coordinates"], 3)}})
    log(f"  {len(countries)} countries")

    log("Canada"); metrics_ca, feats_ca = extra.canada()
    log("Mexico"); metrics_mx, feats_mx = extra.mexico()
    log("Brazil"); metrics_br, feats_br = extra.brazil()

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (OUT / "canada.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats_ca}, separators=(",", ":")))
    (OUT / "mexico.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats_mx}, separators=(",", ":")))
    (OUT / "brazil.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats_br}, separators=(",", ":")))
    (OUT / "counties.geojson").write_text(json.dumps(slim(counties, names_c, metrics_c), separators=(",", ":")))
    (OUT / "states.geojson").write_text(json.dumps(slim(states, names_s, metrics_s), separators=(",", ":")))
    (OUT / "countries.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": countries}, separators=(",", ":")))
    (OUT / "metrics.json").write_text(json.dumps({"generated": generated, "county": metrics_c, "state": metrics_s, "country": metrics_k, "ca_cd": metrics_ca, "mx_mun": metrics_mx, "br_mun": metrics_br}, separators=(",", ":")))
    catalog = {
        "generated": generated,
        "scopes": SCOPES,
        "metrics": [dict(zip(("id", "label", "group", "unit", "about", "source", "scopes"), c)) for c in CATALOG],
        "suggestions": SUGGESTIONS,
        "unavailable": UNAVAILABLE,
    }
    (OUT / "catalog.json").write_text(json.dumps(catalog, indent=1))
    coverage = {c[0]: sum(1 for v in metrics_c.values() if c[0] in v) for c in CATALOG}
    (OUT / "coverage.json").write_text(json.dumps({"generated": generated, "counties": len(metrics_c), "coverage": coverage}, indent=1))
    log(f"done in {time.time() - t0:.0f}s: {len(metrics_c)} counties, {len(metrics_s)} states, {len(metrics_k)} countries, CA {len(metrics_ca)}, MX {len(metrics_mx)}, BR {len(metrics_br)}")
    log(json.dumps(coverage))


if __name__ == "__main__":
    main()
