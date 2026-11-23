"""Núcleo do caçador de clientes.

Usa a Google Places API (New) - Text Search - para achar negócios por nicho,
dentro de um raio em km ao redor de um local, ou numa região inteira
(estado/país) coberta por uma grade de buscas.

Para cada negócio devolve se ele tem site próprio, só rede social/diretório
ou nenhum site.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "leadhunter/1.0 (prospeccao local)"

MAX_BIAS_RADIUS_M = 50_000  # limite do locationBias circular da API
TILE_RADIUS_KM = 50.0
TILE_STEP_KM = 70.0  # com raio 50 km, grade quadrada de 70 km não deixa buracos
PAGE_SIZE = 20

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.rating",
        "places.userRatingCount",
        "places.googleMapsUri",
        "places.businessStatus",
        "places.primaryTypeDisplayName",
        "nextPageToken",
    ]
)

# Links que não contam como "site próprio"
NON_OWN_SITE_DOMAINS = {
    "instagram.com", "facebook.com", "fb.com", "fb.me", "m.facebook.com",
    "wa.me", "whatsapp.com", "api.whatsapp.com", "linktr.ee", "linktree.com",
    "tiktok.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
    "linkedin.com", "bio.link", "beacons.ai", "taplink.cc", "lojaintegrada.com.br",
    "business.site", "google.com", "goo.gl", "maps.app.goo.gl",
    "doctoralia.com.br", "jusbrasil.com.br", "guiamais.com.br", "telelistas.net",
    "apontador.com.br", "yelp.com", "tripadvisor.com", "tripadvisor.com.br",
    "ifood.com.br", "psicologiaviva.com.br", "zenklub.com.br", "olx.com.br",
}

SITE_NONE = "Sem site"
SITE_SOCIAL = "Só rede social/diretório"
SITE_OWN = "Com site"
SITE_STATUSES = [SITE_NONE, SITE_SOCIAL, SITE_OWN]


class PlacesError(RuntimeError):
    """Erro devolvido pela API (chave inválida, API desativada, cota...)."""


class GeocodeError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Geografia
# --------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Geo:
    name: str
    lat: float
    lon: float
    south: float
    north: float
    west: float
    east: float


def geocode(query: str, timeout: float = 20.0) -> Geo:
    """Geocodifica cidade/estado/país via OpenStreetMap Nominatim (sem chave)."""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise GeocodeError(f"Não encontrei o local: {query!r}")
    item = data[0]
    s, n, w, e = (float(x) for x in item["boundingbox"])
    return Geo(item.get("display_name", query), float(item["lat"]), float(item["lon"]), s, n, w, e)


def circle_bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def make_grid(south: float, north: float, west: float, east: float, step_km: float = TILE_STEP_KM) -> list[tuple[float, float]]:
    """Pontos espaçados de step_km cobrindo o retângulo (sempre ao menos 1 ponto)."""
    mid = (south + north) / 2
    dlat = step_km / 110.574
    dlon = step_km / (111.320 * max(math.cos(math.radians(mid)), 0.01))
    n_lat = max(1, math.ceil((north - south) / dlat))
    n_lon = max(1, math.ceil((east - west) / dlon))
    pts = []
    for i in range(n_lat):
        lat = south + (i + 0.5) * (north - south) / n_lat
        for j in range(n_lon):
            lon = west + (j + 0.5) * (east - west) / n_lon
            pts.append((lat, lon))
    return pts


# --------------------------------------------------------------------------
# Classificação do site
# --------------------------------------------------------------------------
def _host(url: str) -> str:
    try:
        host = urlparse(url if "//" in url else "//" + url).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def classify_website(url: Optional[str]) -> str:
    if not url:
        return SITE_NONE
    host = _host(url)
    if not host:
        return SITE_NONE
    for dom in NON_OWN_SITE_DOMAINS:
        if host == dom or host.endswith("." + dom):
            return SITE_SOCIAL
    return SITE_OWN


def whatsapp_link(international_phone: Optional[str]) -> str:
    if not international_phone:
        return ""
    digits = re.sub(r"\D", "", international_phone)
    if len(digits) < 10:
        return ""
    if digits.startswith("55"):
        local = digits[2:]
        # celular BR: DDD (2) + 9 dígitos começando com 9
        if not (len(local) == 11 and local[2] == "9"):
            return ""
    return f"https://wa.me/{digits}"


# --------------------------------------------------------------------------
# Places API
# --------------------------------------------------------------------------
def search_text(
    api_key: str,
    query: str,
    lat: float,
    lon: float,
    radius_m: float,
    *,
    language: str = "pt-BR",
    max_pages: int = 3,
    on_request: Optional[Callable[[], None]] = None,
    timeout: float = 30.0,
) -> list[dict]:
    """Uma busca (até max_pages páginas de 20) com viés para o círculo dado."""
    body = {
        "textQuery": query,
        "pageSize": PAGE_SIZE,
        "languageCode": language,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": float(min(max(radius_m, 1.0), MAX_BIAS_RADIUS_M)),
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    out: list[dict] = []
    for _ in range(max_pages):
        data = _post_with_retry(body, headers, timeout)
        if on_request:
            on_request()
        out.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token:
            break
        body = {**body, "pageToken": token}
    return out


def _post_with_retry(body: dict, headers: dict, timeout: float, tries: int = 4) -> dict:
    delay = 1.5
    for attempt in range(tries):
        resp = requests.post(PLACES_URL, json=body, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 503) and attempt < tries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        try:
            msg = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            msg = resp.text
        raise PlacesError(f"Google Places respondeu {resp.status_code}: {msg}")
    raise PlacesError("Falha após várias tentativas.")


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------
MODE_RADIUS = "radius"
MODE_REGION = "region"


@dataclass
class SearchConfig:
    api_key: str
    niches: list[str]
    location: str
    mode: str = MODE_RADIUS
    radius_km: float = 10.0
    max_pages: int = 3
    max_requests: int = 200
    only_operational: bool = True
    language: str = "pt-BR"


@dataclass
class Plan:
    geo: Geo
    tiles: list[tuple[float, float]]
    tile_radius_km: float
    est_requests: int
    max_requests: int = 0
    truncated: bool = False


def build_plan(cfg: SearchConfig, geo: Optional[Geo] = None) -> Plan:
    geo = geo or geocode(cfg.location)
    if cfg.mode == MODE_RADIUS:
        if cfg.radius_km <= TILE_RADIUS_KM:
            tiles = [(geo.lat, geo.lon)]
            tr = max(cfg.radius_km, 0.1)
        else:
            s, n, w, e = circle_bbox(geo.lat, geo.lon, cfg.radius_km)
            tiles = [
                p for p in make_grid(s, n, w, e)
                if haversine_km(geo.lat, geo.lon, *p) <= cfg.radius_km + TILE_STEP_KM / 2
            ] or [(geo.lat, geo.lon)]
            tr = TILE_RADIUS_KM
    else:
        tiles = make_grid(geo.south, geo.north, geo.west, geo.east)
        tr = TILE_RADIUS_KM
    est = len(tiles) * len(cfg.niches) * cfg.max_pages
    return Plan(geo, tiles, tr, est, cfg.max_requests, est > cfg.max_requests)


def _to_lead(p: dict, niche: str, geo: Geo) -> dict:
    loc = p.get("location", {})
    lat, lon = loc.get("latitude"), loc.get("longitude")
    website = p.get("websiteUri") or ""
    phone = p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or ""
    return {
        "place_id": p.get("id", ""),
        "nicho": niche,
        "nome": (p.get("displayName") or {}).get("text", ""),
        "tipo": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
        "situacao_site": classify_website(website),
        "site": website,
        "telefone": phone,
        "whatsapp": whatsapp_link(p.get("internationalPhoneNumber")),
        "endereco": p.get("formattedAddress", ""),
        "nota": p.get("rating"),
        "avaliacoes": p.get("userRatingCount") or 0,
        "distancia_km": round(haversine_km(geo.lat, geo.lon, lat, lon), 1) if lat is not None else None,
        "google_maps": p.get("googleMapsUri", ""),
        "lat": lat,
        "lon": lon,
        "status": p.get("businessStatus", ""),
    }


def run_search(
    cfg: SearchConfig,
    plan: Optional[Plan] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:
    plan = plan or build_plan(cfg)
    geo = plan.geo
    done = 0
    total = min(plan.est_requests, cfg.max_requests)
    leads: dict[tuple[str, str], dict] = {}

    def tick():
        nonlocal done
        done += 1
        if progress:
            progress(min(done, total), total, "")

    budget_left = cfg.max_requests
    for niche in cfg.niches:
        for lat, lon in plan.tiles:
            if budget_left <= 0:
                break
            pages = min(cfg.max_pages, budget_left)
            before = done
            places = search_text(
                cfg.api_key, niche, lat, lon, plan.tile_radius_km * 1000,
                language=cfg.language, max_pages=pages, on_request=tick,
            )
            budget_left -= max(done - before, 1)
            for p in places:
                if cfg.only_operational and p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
                    continue
                lead = _to_lead(p, niche, geo)
                if lead["lat"] is None:
                    continue
                if cfg.mode == MODE_RADIUS:
                    if lead["distancia_km"] > cfg.radius_km:
                        continue
                else:
                    if not (geo.south <= lead["lat"] <= geo.north and geo.west <= lead["lon"] <= geo.east):
                        continue
                key = (lead["place_id"], niche)
                if key not in leads:
                    leads[key] = lead
    result = sorted(
        leads.values(),
        key=lambda x: (SITE_STATUSES.index(x["situacao_site"]), -(x["avaliacoes"] or 0)),
    )
    return result


def filter_leads(
    leads: list[dict],
    statuses: list[str],
    min_rating: float = 0.0,
    min_reviews: int = 0,
    only_with_phone: bool = False,
) -> list[dict]:
    out = []
    for l in leads:
        if l["situacao_site"] not in statuses:
            continue
        if (l["nota"] or 0) < min_rating:
            continue
        if (l["avaliacoes"] or 0) < min_reviews:
            continue
        if only_with_phone and not l["telefone"]:
            continue
        out.append(l)
    return out
