"""Second-wave sources for Terrain: looks pack, interracial marriage, FBI, and
subnational Canada / Mexico / Brazil. Every function is non-fatal: it returns
what it could get and logs what it could not, so one dead API never blanks the map.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import zipfile
from collections import defaultdict

import requests

UA = {"User-Agent": "terrain-pipeline/1.0 (github.com/AssiamahS/terrain)"}
KEY = os.environ.get("CENSUS_API_KEY", "").strip()
FBI_KEY = os.environ.get("DATA_GOV_API_KEY", "").strip()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v <= -666666666 else v


def div(a, b, scale=1.0, nd=3):
    if a is None or b in (None, 0):
        return None
    return round(a / b * scale, nd)


CACHE = os.environ.get("TERRAIN_CACHE", "").strip()


class _Cached:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)


def get(url, timeout=120, tries=3, headers=None, **kw):
    import hashlib
    cpath = None
    if CACHE:
        os.makedirs(CACHE, exist_ok=True)
        cpath = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest())
        if os.path.exists(cpath):
            return _Cached(open(cpath, "rb").read())
    hdrs = {**UA, **(headers or {})}
    for i in range(tries):
        try:
            r = requests.get(url, headers=hdrs, timeout=timeout, **kw)
            if r.status_code == 200:
                if cpath:
                    open(cpath, "wb").write(r.content)
                return r
            log(f"  {url[:100]} -> {r.status_code}")
        except requests.RequestException as e:
            log(f"  {url[:100]} -> {e}")
        time.sleep(4 * (i + 1))
    return None


# ---------------------------------------------------------------- CDC PLACES (county, 2024 release = 2022 BRFSS)
PLACES = "https://data.cdc.gov/resource/fu4u-a9bh.json"
PLACES_MEASURES = {"OBESITY": "obesity_pct", "LPA": "inactivity_pct", "DEPRESSION": "depression_pct", "BINGE": "binge_pct",
                   "CSMOKING": "smoking_pct", "ISOLATION": "isolation_pct", "SLEEP": "short_sleep_pct"}


def cdc_places():
    out = defaultdict(dict)
    for mid, key in PLACES_MEASURES.items():
        r = get(f"{PLACES}?measureid={mid}&datavaluetypeid=AgeAdjPrv&$select=locationid,data_value&$limit=5000")
        if not r:
            continue
        for row in r.json():
            v = num(row.get("data_value"))
            if v is not None:
                out[row["locationid"].zfill(5)][key] = v
    log(f"  PLACES: {len(out)} counties")
    return out


# ---------------------------------------------------------------- extra CBP (gyms, salons)
CBP = "https://api.census.gov/data/2022/cbp"
CBP_EXTRA = {"713940": "gyms", "812112": "beauty_salons", "812113": "nail_salons"}


def cbp_extra(geo):
    out = defaultdict(dict)
    if not KEY:
        return out
    for naics, key in CBP_EXTRA.items():
        r = get(f"{CBP}?get=ESTAB&for={geo}&NAICS2017={naics}&key={KEY}")
        if not r:
            continue
        try:
            rows = r.json()
        except ValueError:
            continue
        head = rows[0]
        for row in rows[1:]:
            d = dict(zip(head, row))
            out[d["state"] + d.get("county", "")][key] = num(d["ESTAB"])
    return out


# ---------------------------------------------------------------- Google Trends by state (pytrends, best effort)
TRENDS = {"BBL": "trend_bbl", "Tinder": "trend_tinder", "Hinge": "trend_hinge", "OnlyFans": "trend_onlyfans"}
STATE_FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13",
              "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24", "MA": "25",
              "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36",
              "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48",
              "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56"}


def google_trends():
    out = defaultdict(dict)
    try:
        from pytrends.request import TrendReq
    except ImportError:
        log("  pytrends not installed")
        return out
    try:
        p = TrendReq(hl="en-US", tz=300, timeout=(10, 30))
        for term, key in TRENDS.items():
            p.build_payload([term], timeframe="today 12-m", geo="US")
            df = p.interest_by_region(resolution="REGION", inc_low_vol=True, inc_geo_code=True)
            for _, row in df.reset_index().iterrows():
                code = row.get("geoCode") or ""
                fips = STATE_FIPS.get(str(code).replace("US-", ""))
                if fips and num(row[term]) is not None:
                    out[fips][key] = float(row[term])
            time.sleep(2)
        log(f"  trends: {len(out)} states")
    except Exception as e:  # Google rate-limits runners; keep whatever came back
        log(f"  trends failed: {type(e).__name__}: {str(e)[:100]}")
    return out


# ---------------------------------------------------------------- interracial marriage (ACS PUMS)
PUMS = "https://api.census.gov/data/2023/acs/acs5/pums"
TRACT_PUMA = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/2020_Census_Tract_to_2020_PUMA.txt"


def race_group(rac1p, hisp):
    return "H" if hisp not in ("01", "1", None, "") else rac1p


def interracial(state_fips_list):
    """Weighted share of married couples (both spouses in the household) whose
    spouses fall in different race/ethnicity groups (Hispanic counted as one group).
    Returns ({state+puma: share}, {state: share})."""
    puma, state = {}, {}
    if not KEY:
        return puma, state
    for st in state_fips_list:
        url = f"{PUMS}?get=SERIALNO,RELSHIPP,RAC1P,HISP,PWGTP&for=public%20use%20microdata%20area:*&in=state:{st}&MAR=1&RELSHIPP=20,21,23&key={KEY}"
        r = get(url, timeout=300, tries=2)
        if not r:
            continue
        try:
            rows = r.json()
        except ValueError:
            continue
        head = rows[0]
        idx = {h: i for i, h in enumerate(head)}
        by_hh = defaultdict(list)
        for row in rows[1:]:
            by_hh[row[idx["SERIALNO"]]].append(row)
        mixed, total = defaultdict(float), defaultdict(float)
        st_mixed = st_total = 0.0
        for members in by_hh.values():
            ref = next((m for m in members if m[idx["RELSHIPP"]] == "20"), None)
            sp = next((m for m in members if m[idx["RELSHIPP"]] in ("21", "23")), None)
            if not ref or not sp:
                continue
            w = num(ref[idx["PWGTP"]]) or 0
            p = st + ref[idx["public use microdata area"]]
            g1 = race_group(ref[idx["RAC1P"]], ref[idx["HISP"]])
            g2 = race_group(sp[idx["RAC1P"]], sp[idx["HISP"]])
            total[p] += w
            st_total += w
            if g1 != g2:
                mixed[p] += w
                st_mixed += w
        for p in total:
            if total[p] > 0:
                puma[p] = round(mixed[p] / total[p] * 100, 2)
        if st_total > 0:
            state[st] = round(st_mixed / st_total * 100, 2)
        log(f"  PUMS {st}: {len(by_hh)} households, {len(total)} PUMAs")
        time.sleep(1)
    return puma, state


def puma_to_county(puma_values):
    """County value = tract-count-weighted mean of the PUMAs covering it."""
    r = get(TRACT_PUMA)
    if not r:
        return {}
    text = r.content.decode("utf-8-sig")
    weights = defaultdict(lambda: defaultdict(int))
    for row in csv.DictReader(io.StringIO(text)):
        county = row["STATEFP"] + row["COUNTYFP"]
        weights[county][row["STATEFP"] + row["PUMA5CE"]] += 1
    out = {}
    for county, pumas in weights.items():
        num_, den = 0.0, 0
        for p, n in pumas.items():
            if p in puma_values:
                num_ += puma_values[p] * n
                den += n
        if den:
            out[county] = round(num_ / den, 2)
    return out


# ---------------------------------------------------------------- FBI Crime Data Explorer (state estimates)
CDE = "https://api.usa.gov/crime/fbi/cde"
FBI_OFFENSES = {"violent-crime": "violent_crime", "robbery": "robbery", "aggravated-assault": "assault", "rape": "rape", "property-crime": "property_crime"}


OPENCRIME = "https://www.opencrime.us/data/state-trends.json"
STATE_BY_ABBR = STATE_FIPS


def fbi_states():
    """State rates per 100k for the latest year. Primary: OpenCrime's processed mirror of the FBI
    Crime Data Explorer (no key, has rape/robbery/assault/burglary/vehicle theft counts).
    Fallback: the CDE API itself when DATA_GOV_API_KEY is set."""
    out = defaultdict(dict)
    r = get(OPENCRIME, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
    if r:
        try:
            for st in r.json():
                fips = STATE_BY_ABBR.get(st.get("abbr"))
                years = [y for y in st.get("years", []) if num(y.get("population")) and num(y.get("violentCrime")) is not None]
                if not fips or not years:
                    continue
                y = max(years, key=lambda x: x.get("year", 0))
                pop = y["population"]
                per = lambda k: round((num(y.get(k)) or 0) / pop * 100_000, 1) if num(y.get(k)) is not None else None
                out[fips] = {k: v for k, v in {
                    "violent_crime": num(y.get("violentRate")) or per("violentCrime"),
                    "property_crime": num(y.get("propertyRate")) or per("propertyCrime"),
                    "robbery": per("robbery"), "assault": per("aggravatedAssault"), "rape": per("rape"),
                    "burglary": per("burglary"), "vehicle_theft": per("motorVehicleTheft"),
                    "crime_year": float(y.get("year")),
                }.items() if v is not None}
            log(f"  FBI via OpenCrime: {len(out)} states, year {max(v.get('crime_year', 0) for v in out.values()):.0f}")
        except (ValueError, KeyError, TypeError) as e:
            log(f"  OpenCrime parse failed: {e}")
    if out or not FBI_KEY:
        if not out:
            log("  FBI: no data (OpenCrime down, no DATA_GOV_API_KEY)")
        return out
    for abbr, fips in STATE_FIPS.items():
        for offense, key in FBI_OFFENSES.items():
            url = f"{CDE}/summarized/state/{abbr}/{offense}?from=01-2023&to=12-2023&API_KEY={FBI_KEY}"
            rr = get(url, timeout=60, tries=2)
            if not rr:
                break
            try:
                rate = _fbi_rate(rr.json())
            except ValueError:
                break
            if rate is not None:
                out[fips][key] = rate
        time.sleep(0.3)
    log(f"  FBI CDE: {len(out)} states")
    return out


def _fbi_rate(data):
    """summarized/state payload: offenses.rates["<State> Offenses"] = monthly rate per 100k.
    The twelve months sum to the annual rate."""
    try:
        rates = data["offenses"]["rates"]
    except (KeyError, TypeError):
        return None
    for name, months in rates.items():
        if name.endswith(" Offenses") and not name.startswith("United States") and isinstance(months, dict):
            vals = [num(v) for v in months.values() if num(v) is not None]
            if vals:
                return round(sum(vals), 1)
    return None


# ---------------------------------------------------------------- Canada: census divisions
STATCAN = "https://api.statcan.gc.ca/census-recensement/profile/sdmx/rest"
CA_CHARS = {"1": "pop", "8": "age_total", "15": "a20_24", "16": "a25_29", "17": "a30_34", "40": "median_age", "58": "mar_total", "59": "married",
            "66": "not_married", "229": "income_cad", "1513": "imm_total", "1515": "immigrants", "1669": "vm_total",
            "1671": "south_asian", "1672": "chinese", "1673": "black", "1674": "filipino", "1998": "edu_total", "2008": "degree"}
CA_SHP = "https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/files-fichiers/lcd_000a21a_e.zip"


def canada():
    """Returns (metrics by CDUID, geojson features)."""
    r = get(f"{STATCAN}/codelist/STC_CP/CL_GEO_CD", timeout=120)
    if not r:
        return {}, []
    import xml.etree.ElementTree as ET
    ns = {"s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure", "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"}
    root = ET.fromstring(r.content)
    geos = [(c.get("id"), c.find("c:Name", ns).text.strip()) for c in root.iter("{%s}Code" % ns["s"])]
    raw = {}
    chars = "+".join(CA_CHARS)
    for i in range(0, len(geos), 25):
        batch = geos[i:i + 25]
        url = f"{STATCAN}/data/STC_CP,DF_CD/A5.{'+'.join(g for g, _ in batch)}.1+2+3.{chars}.1?format=jsondata"
        rr = get(url, timeout=180, headers={**UA, "Accept": "application/json"})
        if not rr:
            continue
        try:
            d = rr.json()["data"]
        except (ValueError, KeyError):
            continue
        st = (d.get("structures") or [d.get("structure")])[0]
        dims = {x["id"]: [v["id"] for v in x["values"]] for x in st["dimensions"]["series"]}
        for key, series in d["dataSets"][0]["series"].items():
            ix = [int(k) for k in key.split(":")]
            geo = dims["REF_AREA"][ix[1]]
            gender = dims["GENDER"][ix[2]]
            char = dims["CHARACTERISTIC"][ix[3]]
            obs = series.get("observations", {}).get("0")
            v = num(obs[0]) if obs else None
            if v is None:
                continue
            raw.setdefault(geo, {})[(CA_CHARS[char], gender)] = v
    names = dict(geos)
    metrics = {}
    for geo, d in raw.items():
        t = lambda k: d.get((k, "1"))
        m_ = lambda k: d.get((k, "2"))
        f_ = lambda k: d.get((k, "3"))
        cduid = geo[-4:]
        pop = t("pop")
        young_f = sum(f_(k) or 0 for k in ("a20_24", "a25_29", "a30_34"))
        young_m = sum(m_(k) or 0 for k in ("a20_24", "a25_29", "a30_34"))
        m = {
            "pop": pop, "median_age": t("median_age"),
            "f_per_m": div(f_("age_total") or f_("pop"), m_("age_total") or m_("pop")),
            "f_per_m_20_34": div(young_f, young_m) if young_m > 200 else None,
            "age20s_pct": div((t("a20_24") or 0) + (t("a25_29") or 0), pop, 100),
            "age30_34_pct": div(t("a30_34"), pop, 100),
            "married_pct": div(t("married"), t("mar_total"), 100),
            "single_pct": div(t("not_married"), t("mar_total"), 100),
            "income_cad": t("income_cad"),
            "foreign_pct": div(t("immigrants"), t("imm_total"), 100),
            "south_asian_pct": div(t("south_asian"), t("vm_total"), 100),
            "chinese_pct": div(t("chinese"), t("vm_total"), 100),
            "black_pct": div(t("black"), t("vm_total"), 100),
            "filipino_pct": div(t("filipino"), t("vm_total"), 100),
            "degree_pct": div(t("degree"), t("edu_total"), 100),
        }
        metrics[cduid] = {k: v for k, v in m.items() if v is not None}
    log(f"  Canada: {len(metrics)} census divisions")
    feats = canada_shapes(names)
    return metrics, feats


def canada_shapes(names):
    import shapefile
    from pyproj import CRS, Transformer
    from shapely.geometry import shape, mapping
    r = get(CA_SHP, timeout=300)
    if not r:
        return []
    z = zipfile.ZipFile(io.BytesIO(r.content))
    base = [n for n in z.namelist() if n.endswith(".shp")][0][:-4]
    sf = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")), shx=io.BytesIO(z.read(base + ".shx")), encoding="latin-1")
    tr = Transformer.from_crs(CRS.from_wkt(z.read(base + ".prj").decode()), "EPSG:4326", always_xy=True)
    feats = []
    for sr in sf.shapeRecords():
        rec = sr.record.as_dict()
        geom = shape(sr.shape.__geo_interface__)
        geom = geom.simplify(2000, preserve_topology=True)  # metres in the Lambert projection
        gj = mapping(geom)
        gj = {"type": gj["type"], "coordinates": _reproject(gj["coordinates"], tr)}
        feats.append({"type": "Feature", "id": rec["CDUID"], "properties": {"name": rec["CDNAME"], "st": rec["PRUID"]}, "geometry": gj})
    return feats


def _reproject(coords, tr, nd=4):
    if coords and isinstance(coords[0], (int, float)):
        x, y = tr.transform(coords[0], coords[1])
        return [round(x, nd), round(y, nd)]
    return [_reproject(c, tr, nd) for c in coords]


# ---------------------------------------------------------------- Mexico: municipios (Censo 2020 ITER)
ITER = "https://www.inegi.org.mx/contenidos/programas/ccpv/2020/datosabiertos/iter/iter_00_cpv2020_csv.zip"
MX_GEO = "https://raw.githubusercontent.com/MacWilliXD/INEGI-geojson/main/geojson_descargas/AGEM_{st:02d}.geojson"


def mexico():
    r = get(ITER, timeout=600, headers={"User-Agent": "Mozilla/5.0"})
    if not r:
        return {}, []
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = [n for n in z.namelist() if n.lower().endswith(".csv") and "conjunto_de_datos" in n][0]
    text = z.read(name).decode("utf-8-sig", "replace")
    metrics, names = {}, {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("LOC") != "0000" or row.get("MUN") == "000":
            continue
        code = row["ENTIDAD"].zfill(2) + row["MUN"].zfill(3)
        g = lambda k: num(row.get(k))
        pop = g("POBTOT")
        young_f = sum(g(k) or 0 for k in ("P_20A24_F", "P_25A29_F", "P_30A34_F"))
        young_m = sum(g(k) or 0 for k in ("P_20A24_M", "P_25A29_M", "P_30A34_M"))
        m = {
            "pop": pop,
            "f_per_m": div(g("POBFEM"), g("POBMAS")),
            "f_per_m_20_34": div(young_f, young_m) if young_m > 200 else None,
            "age20s_pct": div((g("P_20A24") or 0) + (g("P_25A29") or 0), pop, 100),
            "age30_34_pct": div(g("P_30A34"), pop, 100),
            "age65_pct": div(g("POB65_MAS"), pop, 100),
            "single_pct": div(g("P12YM_SOLT"), g("P_12YMAS"), 100),
            "married_pct": div(g("P12YM_CASA"), g("P_12YMAS"), 100),
            "separated_pct": div(g("P12YM_SEPA"), g("P_12YMAS"), 100),
            "schooling_years": g("GRAPROES"),
            "born_elsewhere_pct": div(g("PNACOE"), pop, 100),
        }
        metrics[code] = {k: v for k, v in m.items() if v is not None}
        names[code] = row["NOM_MUN"]
    log(f"  Mexico: {len(metrics)} municipios")
    from shapely.geometry import shape, mapping
    feats = []
    for st in range(1, 33):
        rr = get(MX_GEO.format(st=st), timeout=180)
        if not rr:
            continue
        for f in rr.json()["features"]:
            p = f["properties"]
            code = p.get("cvegeo")
            if not code:
                continue
            geom = shape(f["geometry"]).simplify(0.01, preserve_topology=True)
            feats.append({"type": "Feature", "id": code, "properties": {"name": names.get(code, p.get("nom_agem")), "st": p.get("cve_agee", "")},
                          "geometry": _round(mapping(geom))})
    return metrics, feats


def _round(gj, nd=4):
    def rc(c):
        if c and isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [rc(x) for x in c]
    return {"type": gj["type"], "coordinates": rc(gj["coordinates"])}


# ---------------------------------------------------------------- Brazil: municípios (Censo 2022 via SIDRA)
SIDRA = "https://apisidra.ibge.gov.br/values/t/9514/n6/all/v/93/p/2022/c2/4,5/c287/100362,93087,93088,93089/c286/113635"
BR_GEO = "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?formato=application/vnd.geo+json&qualidade=minima&intrarregiao=municipio"
AGE_CODES = {"100362": "all", "93087": "a20_24", "93088": "a25_29", "93089": "a30_34"}


def brazil():
    r = get(SIDRA, timeout=300)
    if not r:
        return {}, []
    raw, names = defaultdict(dict), {}
    for row in r.json()[1:]:
        code = row["D1C"]
        names[code] = row["D1N"].rsplit(" - ", 1)[0]
        sex = "m" if row["D4C"] == "4" else "f"
        age = AGE_CODES.get(row["D5C"])
        v = num(row["V"])
        if age and v is not None:
            raw[code][(age, sex)] = v
    metrics = {}
    for code, d in raw.items():
        tot = lambda a: (d.get((a, "m")) or 0) + (d.get((a, "f")) or 0)
        pop = tot("all")
        young_f = sum(d.get((a, "f")) or 0 for a in ("a20_24", "a25_29", "a30_34"))
        young_m = sum(d.get((a, "m")) or 0 for a in ("a20_24", "a25_29", "a30_34"))
        m = {"pop": pop, "f_per_m": div(d.get(("all", "f")), d.get(("all", "m"))),
             "f_per_m_20_34": div(young_f, young_m) if young_m > 200 else None,
             "age20s_pct": div(tot("a20_24") + tot("a25_29"), pop, 100),
             "age30_34_pct": div(tot("a30_34"), pop, 100)}
        metrics[code] = {k: v for k, v in m.items() if v is not None}
    log(f"  Brazil: {len(metrics)} municípios")
    rr = get(BR_GEO, timeout=300)
    feats = []
    if rr:
        for f in rr.json()["features"]:
            code = f["properties"].get("codarea")
            if code in metrics:
                feats.append({"type": "Feature", "id": code, "properties": {"name": names.get(code, code), "st": code[:2]}, "geometry": _round(f["geometry"])})
    return metrics, feats
