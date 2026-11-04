"""
Overture Maps source for the road network (alternative to Overpass).

Overture Maps Foundation publishes open global road data (transportation theme, built largely on
OpenStreetMap) as GeoParquet on a public S3 bucket. Reading it needs no account and no API server:
we fetch each file's small footer, keep only the row groups whose bounding box overlaps the study
area, and download just those (a few MB, not the 72 GB planet).

Overture road segments carry ``connectors`` (junction ids positioned along the segment). We split each
segment at its connectors, so the resulting pieces join exactly where real roads join, then reuse the
same <= 0.5 km subdivision, DEM sampling and hazard pipeline as the OSM path.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from .osm import DEFAULT_SPEED, Piece, _subdivide, is_drivable

BUCKET_URL = "https://overturemaps-us-west-2.s3.amazonaws.com/"
Bbox = Tuple[float, float, float, float]  # south, west, north, east

# Classes a truck can use between towns. Residential lanes, tracks and living streets are excluded by
# default: they add ~70 % more segments (hurting map and query speed) but little long-haul routing value.
TRUCK_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
                 "secondary_link", "tertiary", "tertiary_link", "unclassified"}
ALL_DRIVABLE_CLASSES = set(DEFAULT_SPEED)


class RangeFile(io.RawIOBase):
    """Read-only file over HTTPS range requests (proxy- and CA-friendly: plain ``requests``)."""

    def __init__(self, key: str, session: Optional[requests.Session] = None, block: int = 4 << 20):
        self.url, self.s, self.block, self.pos = BUCKET_URL + key, session or requests.Session(), block, 0
        self.cache: Dict[int, bytes] = {}
        r = self.s.head(self.url, timeout=30)
        r.raise_for_status()
        self.size = int(r.headers["Content-Length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, off: int, whence: int = 0) -> int:
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def _blk(self, i: int) -> bytes:
        if i not in self.cache:
            a = i * self.block
            b = min(a + self.block, self.size) - 1
            r = self.s.get(self.url, headers={"Range": f"bytes={a}-{b}"}, timeout=180)
            r.raise_for_status()
            self.cache[i] = r.content
        return self.cache[i]

    def read(self, n: int = -1) -> bytes:
        if n < 0 or self.pos + n > self.size:
            n = self.size - self.pos
        out, end = [], self.pos + n
        while self.pos < end:
            i, o = divmod(self.pos, self.block)
            chunk = self._blk(i)[o:o + end - self.pos]
            out.append(chunk)
            self.pos += len(chunk)
        return b"".join(out)

    def readinto(self, b) -> int:
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def latest_release(session: Optional[requests.Session] = None) -> str:
    r = (session or requests).get(BUCKET_URL, params={"prefix": "release/", "delimiter": "/"}, timeout=30)
    r.raise_for_status()
    rel = sorted(re.findall(r"release/([0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+)/", r.text))
    if not rel:
        raise RuntimeError("Could not list Overture releases")
    return rel[-1]


def _list_keys(prefix: str, session: requests.Session) -> List[str]:
    keys: List[str] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        r = session.get(BUCKET_URL, params=params, timeout=30)
        r.raise_for_status()
        keys += re.findall(r"<Key>([^<]+\.parquet)</Key>", r.text)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", r.text)
        if not m:
            return keys
        token = m.group(1)


def fetch_rows(theme: str, typ: str, bbox: Bbox, columns: List[str], release: Optional[str] = None,
               country: Optional[str] = None, progress: Callable[[str], None] = lambda s: None) -> Tuple[str, list]:
    """Rows of one Overture table overlapping ``bbox``; returns (release, list of row dicts)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    sess = requests.Session()
    release = release or latest_release(sess)
    s, w, n, e = bbox
    keys = _list_keys(f"release/{release}/theme={theme}/type={typ}/", sess)
    progress(f"Overture {release}: scanning {len(keys)} {theme}/{typ} file footers")
    out = []
    for key in keys:
        pf = pq.ParquetFile(RangeFile(key, sess, block=1 << 20))
        md = pf.metadata
        names = [md.schema.column(i).path for i in range(md.num_columns)]
        ix = {k: names.index(k) for k in ("bbox.xmin", "bbox.xmax", "bbox.ymin", "bbox.ymax")}
        hits = []
        for rg in range(md.num_row_groups):
            r = md.row_group(rg)
            st = {k: r.column(i).statistics for k, i in ix.items()}
            if any(x is None or not x.has_min_max for x in st.values()) or (
                    st["bbox.xmax"].max >= w and st["bbox.xmin"].min <= e
                    and st["bbox.ymax"].max >= s and st["bbox.ymin"].min <= n):
                hits.append(rg)
        if not hits:
            continue
        progress(f"  {key[-24:]}: reading {len(hits)}/{md.num_row_groups} row groups")
        cols = [c for c in columns + ["bbox"] if c in pf.schema_arrow.names]
        tb = pf.read_row_groups(hits, columns=cols)
        bb = tb["bbox"]
        mask = pc.and_(
            pc.and_(pc.greater_equal(pc.struct_field(bb, "xmax"), w), pc.less_equal(pc.struct_field(bb, "xmin"), e)),
            pc.and_(pc.greater_equal(pc.struct_field(bb, "ymax"), s), pc.less_equal(pc.struct_field(bb, "ymin"), n)))
        tb = tb.filter(mask)
        if country and "country" in tb.column_names:
            tb = tb.filter(pc.equal(tb["country"], country))
        out += tb.to_pylist()
    return release, out


# ------------------------------------------------------------ segment -> pieces
def _first(items, *path):
    for it in items or []:
        cur = it
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is not None:
            return cur
    return None


def segment_tags(row: dict) -> dict:
    tags = {"highway": row.get("class")}
    if (row.get("names") or {}).get("primary"):
        tags["name"] = row["names"]["primary"]
    surf = _first(row.get("road_surface"), "value")
    if surf:
        tags["surface"] = surf
    ms = _first(row.get("speed_limits"), "max_speed")
    if isinstance(ms, dict) and ms.get("value"):
        tags["maxspeed"] = f"{ms['value']} {'mph' if ms.get('unit') == 'mph' else 'km/h'}"
    return tags


def motor_traffic_denied(row: dict) -> bool:
    """True if the segment is closed to motor vehicles in both directions with no further conditions."""
    for ar in row.get("access_restrictions") or []:
        when = ar.get("when") or {}
        modes = set(when.get("mode") or [])
        other = {k for k, v in when.items() if k != "mode" and v}
        if ar.get("access_type") == "denied" and not other and (not modes or modes & {"motor_vehicle", "vehicle", "truck", "car"}):
            return True
    return False


def segments_to_pieces(rows: list, classes=TRUCK_CLASSES, max_piece_km: float = 0.5) -> List[Piece]:
    from shapely import from_wkb
    from shapely.ops import substring

    node_ids: Dict[str, int] = {}

    def nid(connector: str) -> int:
        return node_ids.setdefault(connector, len(node_ids) + 1)

    pieces: List[Piece] = []
    for way_id, row in enumerate(rows, start=1):
        tags = segment_tags(row)
        if row.get("subtype") != "road" or tags.get("highway") not in classes or not is_drivable(tags) \
                or motor_traffic_denied(row):
            continue
        conns = sorted(row.get("connectors") or [], key=lambda c: c["at"])
        if len(conns) < 2:
            continue
        line = from_wkb(row["geometry"])
        if line.geom_type != "LineString" or line.length == 0:
            continue
        counter = [0]
        for a, b in zip(conns, conns[1:]):
            if b["at"] - a["at"] < 1e-9 or a["connector_id"] == b["connector_id"]:
                continue
            sub = substring(line, a["at"], b["at"], normalized=True)
            coords = [(y, x) for x, y in sub.coords]  # shapely is (lng, lat); pieces are (lat, lng)
            ids = [nid(a["connector_id"])]
            for _ in coords[1:-1]:
                counter[0] += 1
                ids.append(-(way_id * 10_000 + counter[0]))
            ids.append(nid(b["connector_id"]))
            if len(ids) >= 2:
                pieces.extend(_subdivide(way_id, tags, ids, coords, max_piece_km, counter))
    return pieces


def _english_name(names: dict) -> Optional[str]:
    common = names.get("common") or []
    pairs = common.items() if isinstance(common, dict) else common
    return next((v for k, v in pairs if k == "en" and v), None)


WARD_NAME = re.compile(r"-\d+$")  # administrative wards such as "Kathmandu-16" are not places a dealer searches for


def division_places(rows: list) -> List[dict]:
    """Named towns, municipalities and villages from Overture divisions, for destination search.

    ``name`` is the English name when Overture has one, else the Nepali name; ``name_local`` keeps the
    Nepali (Devanagari) name so both scripts are searchable."""
    from shapely import from_wkb

    out = []
    for r in rows:
        names = r.get("names") or {}
        local = names.get("primary")
        name = _english_name(names) or local
        if not name or WARD_NAME.search(name) or r.get("subtype") not in {
                "locality", "localadmin", "neighborhood", "macrohood", "county"}:
            continue
        try:
            pt = from_wkb(r["geometry"])
        except Exception:  # noqa: BLE001 - skip malformed geometry
            continue
        if pt.geom_type != "Point":
            continue
        out.append({"id": f"overture:{r['id']}", "name": name, "name_local": local if local != name else "",
                    "lat": round(pt.y, 5), "lng": round(pt.x, 5),
                    "kind": r.get("class") or ("municipality" if r.get("subtype") == "locality" else r.get("subtype"))})
    return out


def load_overture(bbox: Bbox, cache_dir: Path, classes=TRUCK_CLASSES, release: Optional[str] = None,
                  progress: Callable[[str], None] = print) -> Tuple[List[Piece], List[dict], str]:
    """Download (or reuse cached) Overture roads + places for ``bbox``."""
    import pickle

    key = f"overture-{'_'.join(f'{v:.3f}' for v in bbox)}-{release or 'latest'}.pkl"
    cache = Path(cache_dir) / key
    if cache.exists():
        progress(f"Using cached Overture download {cache}")
        rel, segs, divs = pickle.loads(cache.read_bytes())
    else:
        rel, segs = fetch_rows("transportation", "segment", bbox,
                               ["id", "geometry", "subtype", "class", "names", "road_surface", "speed_limits",
                                "access_restrictions", "connectors"], release, progress=progress)
        _, divs = fetch_rows("divisions", "division", bbox, ["id", "geometry", "subtype", "class", "names", "country"],
                             rel, country="NP", progress=progress)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps((rel, segs, divs)))
    progress(f"{len(segs)} road segments, {len(divs)} division features")
    return segments_to_pieces(segs, classes), division_places(divs), rel
