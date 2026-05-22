"""Core algorithms for warehouse matching, routing and tracking."""

from __future__ import annotations

import math
import urllib.parse
import urllib.request
import json
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Optional import: kept for future exact VRP solving, but the current implementation
# uses a deterministic greedy planner for fast prototype validation.
try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # noqa: F401
except Exception:  # pragma: no cover - OR-Tools is optional at runtime for now.
    pywrapcp = None
    routing_enums_pb2 = None


MOCK_WAREHOUSES: List[Dict[str, object]] = [
    {"id": "W01", "name": "Hangzhou West Hub", "distance_m": 15000, "stock": 500, "vehicles": 5},
    {"id": "W02", "name": "Yuhang Origin Hub", "distance_m": 45000, "stock": 2000, "vehicles": 12},
    {"id": "W03", "name": "Xiaoshan Front Warehouse", "distance_m": 22000, "stock": 100, "vehicles": 2},
    {"id": "W04", "name": "Ningbo Beilun Cold Hub", "distance_m": 145000, "stock": 800, "vehicles": 8},
]


CITY_CENTER_HINTS: Dict[str, Tuple[float, float]] = {
    "beijing": (39.9042, 116.4074),
    "shanghai": (31.2304, 121.4737),
    "hangzhou": (30.2741, 120.1551),
    "ningbo": (29.8683, 121.5440),
    "shaoxing": (30.0024, 120.5802),
    "chengdu": (30.5728, 104.0668),
    "guizhou": (26.6470, 106.6302),
    "guiyang": (26.6470, 106.6302),
    "guangzhou": (23.1291, 113.2644),
    "shenzhen": (22.5431, 114.0579),
    "nanjing": (32.0603, 118.7969),
    "wuxi": (31.4912, 120.3119),
    "huangshan": (29.7149, 118.3376),
    "xiong'an": (39.0432, 115.8925),
    "jinan": (36.6512, 117.1201),
    "haidian": (39.9561, 116.3100),
    "鏉窞": (30.2741, 120.1551),
    "瀹佹尝": (29.8683, 121.5440),
    "缁嶅叴": (30.0024, 120.5802),
    "鎴愰兘": (30.5728, 104.0668),
    "璐甸槼": (26.6470, 106.6302),
    "涓婃捣": (31.2304, 121.4737),
    "鍖椾含": (39.9042, 116.4074),
    "骞垮窞": (23.1291, 113.2644),
    "娣卞湷": (22.5431, 114.0579),
    "鍗椾含": (32.0603, 118.7969),
}


# Ensure common Chinese city names are always mapped correctly.
CITY_CENTER_HINTS.update(
    {
        "\u676d\u5dde": (30.2741, 120.1551),  # 杭州
        "\u5b81\u6ce2": (29.8683, 121.5440),  # 宁波
        "\u7ecd\u5174": (30.0024, 120.5802),  # 绍兴
        "\u6210\u90fd": (30.5728, 104.0668),  # 成都
        "\u8d35\u9633": (26.6470, 106.6302),  # 贵阳
        "\u4e0a\u6d77": (31.2304, 121.4737),  # 上海
        "\u5317\u4eac": (39.9042, 116.4074),  # 北京
        "\u5e7f\u5dde": (23.1291, 113.2644),  # 广州
        "\u6df1\u5733": (22.5431, 114.0579),  # 深圳
        "\u5357\u4eac": (32.0603, 118.7969),  # 南京
    }
)
def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute geodesic distance between two coordinates in meters."""
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def resolve_address_coordinates(address: str) -> Dict[str, object]:
    """Offline geocoding fallback using city hints and deterministic hashing.

    Returns:
        {
            "lat": float,
            "lng": float,
            "engine": "hint" | "hash",
            "confidence": float
        }
    """
    normalized = (address or "").strip()
    if not normalized:
        raise ValueError("address is empty")

    lower = normalized.lower()
    for key, (lat, lng) in CITY_CENTER_HINTS.items():
        if key in normalized or key in lower:
            return {"lat": lat, "lng": lng, "engine": "hint", "confidence": 0.92}

    # Deterministic fallback to keep prototype testable without internet APIs.
    seed = sum((idx + 1) * ord(ch) for idx, ch in enumerate(normalized))
    lat = 18.0 + (seed % 2400) / 100.0  # 18.00 ~ 41.99
    lng = 73.0 + (seed % 6200) / 100.0  # 73.00 ~ 134.99
    return {"lat": round(lat, 6), "lng": round(lng, 6), "engine": "hash", "confidence": 0.35}


def _http_get_json(url: str, timeout: int = 8, headers: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def geocode_address(
    address: str,
    provider: str = "offline",
    amap_key: Optional[str] = None,
    baidu_ak: Optional[str] = None,
) -> Dict[str, object]:
    """Resolve address coordinates via amap/baidu/offline fallback.

    Returns:
        {
            "lat": float,
            "lng": float,
            "engine": str,
            "confidence": float,
            "used_fallback": bool
        }
    """
    provider = (provider or "offline").lower()
    address = (address or "").strip()
    if not address:
        raise ValueError("address is empty")

    # Free provider: OpenStreetMap Nominatim
    if provider in {"free", "nominatim", "osm", "osm_free"}:
        try:
            query = urllib.parse.urlencode({"format": "json", "limit": 1, "q": address})
            url = f"https://nominatim.openstreetmap.org/search?{query}"
            data = _http_get_json(
                url,
                headers={
                    "User-Agent": "BUPT-Logistics-Demo/1.0 (academic prototype)",
                    "Accept": "application/json",
                },
            )
            if isinstance(data, list) and data:
                row = data[0]
                return {
                    "lat": float(row["lat"]),
                    "lng": float(row["lon"]),
                    "engine": "nominatim",
                    "confidence": 0.95,
                    "used_fallback": False,
                }
        except Exception:
            pass

    # High-priority: AMap
    if provider == "amap" and amap_key:
        try:
            query = urllib.parse.urlencode({"address": address, "key": amap_key})
            url = f"https://restapi.amap.com/v3/geocode/geo?{query}"
            data = _http_get_json(url)
            geocodes = data.get("geocodes") or []
            if str(data.get("status")) == "1" and geocodes:
                loc = geocodes[0].get("location", "")
                lng_str, lat_str = loc.split(",")
                return {
                    "lat": float(lat_str),
                    "lng": float(lng_str),
                    "engine": "amap",
                    "confidence": 0.98,
                    "used_fallback": False,
                }
        except Exception:
            pass

    # Second priority: Baidu
    if provider == "baidu" and baidu_ak:
        try:
            query = urllib.parse.urlencode({"address": address, "output": "json", "ak": baidu_ak})
            url = f"https://api.map.baidu.com/geocoding/v3/?{query}"
            data = _http_get_json(url)
            if int(data.get("status", -1)) == 0:
                loc = (data.get("result") or {}).get("location") or {}
                lat = loc.get("lat")
                lng = loc.get("lng")
                if lat is not None and lng is not None:
                    return {
                        "lat": float(lat),
                        "lng": float(lng),
                        "engine": "baidu",
                        "confidence": 0.98,
                        "used_fallback": False,
                    }
        except Exception:
            pass

    fallback = resolve_address_coordinates(address)
    fallback["used_fallback"] = fallback.get("engine") == "hash"
    return fallback


def fetch_driving_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    provider: str = "offline",
    amap_key: Optional[str] = None,
    baidu_ak: Optional[str] = None,
    waypoints: Optional[List[Tuple[float, float]]] = None,
) -> Dict[str, object]:
    """Fetch route metrics and geometry from amap/baidu/offline fallback."""
    provider = (provider or "offline").lower()
    waypoints = waypoints or []

    # Free provider: OSRM public demo
    if provider in {"free", "osrm", "osm", "osm_free"}:
        try:
            coord_pairs: List[str] = [f"{origin_lng},{origin_lat}"]
            for lat, lng in waypoints:
                coord_pairs.append(f"{lng},{lat}")
            coord_pairs.append(f"{dest_lng},{dest_lat}")
            coords_str = ";".join(coord_pairs)
            query = urllib.parse.urlencode({"overview": "full", "geometries": "geojson"})
            url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}?{query}"
            data = _http_get_json(url)
            routes = data.get("routes") or []
            if data.get("code") == "Ok" and routes:
                route = routes[0]
                geometry = route.get("geometry", {}).get("coordinates", []) or []
                polyline = [[float(p[1]), float(p[0])] for p in geometry if len(p) >= 2]
                if not polyline:
                    polyline = [[origin_lat, origin_lng], [dest_lat, dest_lng]]
                return {
                    "provider": "osrm",
                    "distance_km": round(float(route.get("distance", 0.0)) / 1000.0, 2),
                    "duration_min": int(round(float(route.get("duration", 0.0)) / 60.0)),
                    "polyline": polyline,
                    "used_fallback": False,
                }
        except Exception:
            pass

    # AMap route
    if provider == "amap" and amap_key:
        try:
            params = {
                "origin": f"{origin_lng},{origin_lat}",
                "destination": f"{dest_lng},{dest_lat}",
                "key": amap_key,
                "show_fields": "cost,polyline",
            }
            if waypoints:
                params["waypoints"] = ";".join([f"{lng},{lat}" for lat, lng in waypoints])
            url = "https://restapi.amap.com/v5/direction/driving?" + urllib.parse.urlencode(params)
            data = _http_get_json(url)
            route = data.get("route") or {}
            paths = route.get("paths") or []
            if str(data.get("status")) == "1" and paths:
                first = paths[0]
                distance_m = float(first.get("distance") or 0.0)
                duration_s = float(first.get("cost", {}).get("duration") or 0.0)
                polyline: List[List[float]] = []
                for step in first.get("steps") or []:
                    for pair in (step.get("polyline") or "").split(";"):
                        if not pair or "," not in pair:
                            continue
                        lng_str, lat_str = pair.split(",")
                        polyline.append([float(lat_str), float(lng_str)])
                if not polyline:
                    polyline = [[origin_lat, origin_lng], [dest_lat, dest_lng]]
                return {
                    "provider": "amap",
                    "distance_km": round(distance_m / 1000.0, 2),
                    "duration_min": int(round(duration_s / 60.0)),
                    "polyline": polyline,
                    "used_fallback": False,
                }
        except Exception:
            pass

    # Baidu route
    if provider == "baidu" and baidu_ak:
        try:
            params = {
                "origin": f"{origin_lat},{origin_lng}",
                "destination": f"{dest_lat},{dest_lng}",
                "ak": baidu_ak,
            }
            if waypoints:
                params["waypoints"] = "|".join([f"{lat},{lng}" for lat, lng in waypoints])
            url = "https://api.map.baidu.com/directionlite/v1/driving?" + urllib.parse.urlencode(params)
            data = _http_get_json(url)
            if int(data.get("status", -1)) == 0:
                routes = (data.get("result") or {}).get("routes") or []
                if routes:
                    first = routes[0]
                    distance_m = float((first.get("distance") or 0.0))
                    duration_s = float((first.get("duration") or 0.0))
                    return {
                        "provider": "baidu",
                        "distance_km": round(distance_m / 1000.0, 2),
                        "duration_min": int(round(duration_s / 60.0)),
                        "polyline": [[origin_lat, origin_lng], [dest_lat, dest_lng]],
                        "used_fallback": False,
                    }
        except Exception:
            pass

    # Offline fallback
    distance_km = haversine_meters(origin_lat, origin_lng, dest_lat, dest_lng) / 1000.0
    speed_kmh = 42.0
    duration_min = int(round((distance_km / speed_kmh) * 60.0))
    return {
        "provider": "offline",
        "distance_km": round(distance_km, 2),
        "duration_min": max(1, duration_min),
        "polyline": [[origin_lat, origin_lng], [dest_lat, dest_lng]],
        "used_fallback": True,
    }


def enrich_route_plan_with_provider(
    route_nodes: Sequence[Dict[str, object]],
    provider: str = "offline",
    amap_key: Optional[str] = None,
    baidu_ak: Optional[str] = None,
) -> Dict[str, object]:
    """Enrich route nodes with provider distance/time and merged geometry."""
    if not route_nodes or len(route_nodes) < 2:
        return {
            "provider": provider,
            "distance_km": 0.0,
            "duration_min": 0,
            "polyline": [],
            "used_fallback": True,
            "legs": [],
        }

    total_km = 0.0
    total_min = 0
    legs: List[Dict[str, object]] = []
    full_polyline: List[List[float]] = []
    any_fallback = False

    for idx in range(1, len(route_nodes)):
        prev = route_nodes[idx - 1]
        curr = route_nodes[idx]
        p_lat, p_lng = float(prev["coord"][0]), float(prev["coord"][1])
        c_lat, c_lng = float(curr["coord"][0]), float(curr["coord"][1])
        leg = fetch_driving_route(
            origin_lat=p_lat,
            origin_lng=p_lng,
            dest_lat=c_lat,
            dest_lng=c_lng,
            provider=provider,
            amap_key=amap_key,
            baidu_ak=baidu_ak,
        )
        total_km += float(leg["distance_km"])
        total_min += int(leg["duration_min"])
        any_fallback = any_fallback or bool(leg["used_fallback"])
        legs.append(
            {
                "from_seq": int(prev["seq"]),
                "to_seq": int(curr["seq"]),
                "distance_km": leg["distance_km"],
                "duration_min": leg["duration_min"],
                "provider": leg["provider"],
                "used_fallback": leg["used_fallback"],
            }
        )

        points = leg["polyline"] or []
        if not full_polyline:
            full_polyline.extend(points)
        else:
            full_polyline.extend(points[1:] if len(points) > 1 else points)

    return {
        "provider": provider,
        "distance_km": round(total_km, 2),
        "duration_min": int(total_min),
        "polyline": full_polyline,
        "used_fallback": any_fallback,
        "legs": legs,
    }


def match_best_warehouse(target_distance: int = 300000) -> List[Dict[str, object]]:
    """Backward-compatible demo matcher used by the old API."""
    max_inv = max(int(w["stock"]) for w in MOCK_WAREHOUSES)
    max_veh = max(int(w["vehicles"]) for w in MOCK_WAREHOUSES)
    result: List[Dict[str, object]] = []

    for wh in MOCK_WAREHOUSES:
        distance_m = float(wh["distance_m"])
        dist_score = max(0.0, (target_distance - distance_m) / target_distance)
        inv_score = float(wh["stock"]) / max_inv if max_inv > 0 else 0.0
        veh_score = float(wh["vehicles"]) / max_veh if max_veh > 0 else 0.0
        total_score = (0.5 * dist_score) + (0.3 * inv_score) + (0.2 * veh_score)

        row = dict(wh)
        row["score"] = round(total_score * 100, 1)
        row["details"] = {
            "dist": round(dist_score * 100),
            "inv": round(inv_score * 100),
            "veh": round(veh_score * 100),
        }
        result.append(row)

    result.sort(key=lambda x: x["score"], reverse=True)
    return result


def score_warehouse_candidates(
    candidates: Sequence[Dict[str, object]],
    target_lat: float,
    target_lng: float,
    demand_qty: int,
    sla_minutes: int,
    traffic_index: float = 1.0,
    avg_speed_kmh: float = 45.0,
) -> List[Dict[str, object]]:
    """Rank warehouse candidates by distance, stock, capacity and SLA feasibility."""
    if demand_qty <= 0:
        raise ValueError("demand_qty must be positive")

    ranked: List[Dict[str, object]] = []
    speed = max(8.0, avg_speed_kmh / max(0.5, traffic_index))
    max_dist_km = 1500.0
    demand_base = max(1, demand_qty)

    for wh in candidates:
        stock = int(wh.get("stock", 0) or 0)
        vehicles = int(wh.get("vehicles", 0) or 0)
        lat = float(wh["lat"])
        lng = float(wh["lng"])

        distance_km = haversine_meters(target_lat, target_lng, lat, lng) / 1000.0
        eta_minutes = (distance_km / max(1.0, speed)) * 60.0

        stock_ok = stock >= demand_qty
        capacity_ok = vehicles > 0
        sla_ok = eta_minutes <= sla_minutes
        feasible = stock_ok and capacity_ok

        dist_score = max(0.0, 1.0 - min(distance_km, max_dist_km) / max_dist_km)
        inv_score = min(1.0, stock / float(demand_base * 2))
        veh_score = min(1.0, vehicles / 10.0)
        if sla_minutes <= 0:
            sla_score = 0.0
        elif eta_minutes <= sla_minutes:
            sla_score = 1.0
        else:
            overflow = eta_minutes - sla_minutes
            sla_score = max(0.0, 1.0 - (overflow / float(sla_minutes)))

        total_score = (0.4 * dist_score) + (0.3 * inv_score) + (0.2 * veh_score) + (0.1 * sla_score)
        ranked.append(
            {
                "id": wh.get("id"),
                "name": wh.get("name"),
                "lat": lat,
                "lng": lng,
                "distance_km": round(distance_km, 2),
                "eta_minutes": int(round(eta_minutes)),
                "stock": stock,
                "vehicles": vehicles,
                "stock_ok": stock_ok,
                "capacity_ok": capacity_ok,
                "sla_ok": sla_ok,
                "feasible": feasible,
                "score": round(total_score * 100, 2),
                "details": {
                    "distance": round(dist_score * 100),
                    "inventory": round(inv_score * 100),
                    "capacity": round(veh_score * 100),
                    "sla": round(sla_score * 100),
                },
            }
        )

    ranked.sort(key=lambda row: (row["feasible"], row["score"]), reverse=True)
    return ranked


def build_greedy_vrp_plan(
    depot: Dict[str, float],
    orders: Sequence[Dict[str, object]],
    traffic_index: float = 1.0,
    weather_index: float = 1.0,
    history_index: float = 1.0,
    base_speed_kmh: float = 42.0,
) -> Dict[str, object]:
    """Build a multi-order merged route using nearest-neighbor heuristic."""
    if not orders:
        return {
            "status": "error",
            "message": "no orders to route",
            "total_distance_km": 0.0,
            "total_time_minutes": 0,
            "route_nodes": [],
        }

    traffic = max(0.5, traffic_index)
    weather = max(0.5, weather_index)
    history = max(0.5, history_index)
    speed = max(8.0, base_speed_kmh / (traffic * weather * history))

    remaining = [dict(order) for order in orders]
    current = {"lat": float(depot["lat"]), "lng": float(depot["lng"])}
    route_nodes: List[Dict[str, object]] = [
        {
            "seq": 0,
            "node_type": "depot",
            "name": "depot_start",
            "order_no": None,
            "coord": [current["lat"], current["lng"]],
            "eta_minutes_from_start": 0,
            "leg_distance_km": 0.0,
        }
    ]

    seq = 1
    elapsed_min = 0.0
    total_km = 0.0

    while remaining:
        nearest_idx = 0
        nearest_dist = float("inf")
        for idx, order in enumerate(remaining):
            dist_km = haversine_meters(current["lat"], current["lng"], float(order["lat"]), float(order["lng"])) / 1000.0
            if dist_km < nearest_dist:
                nearest_dist = dist_km
                nearest_idx = idx

        target = remaining.pop(nearest_idx)
        leg_minutes = (nearest_dist / speed) * 60.0
        elapsed_min += leg_minutes
        total_km += nearest_dist
        current = {"lat": float(target["lat"]), "lng": float(target["lng"])}

        route_nodes.append(
            {
                "seq": seq,
                "node_type": "delivery",
                "name": target.get("name") or f"order_{target.get('order_no', seq)}",
                "order_no": target.get("order_no"),
                "coord": [current["lat"], current["lng"]],
                "eta_minutes_from_start": int(round(elapsed_min)),
                "leg_distance_km": round(nearest_dist, 2),
            }
        )
        seq += 1

    back_km = haversine_meters(current["lat"], current["lng"], float(depot["lat"]), float(depot["lng"])) / 1000.0
    back_min = (back_km / speed) * 60.0
    total_km += back_km
    elapsed_min += back_min

    route_nodes.append(
        {
            "seq": seq,
            "node_type": "depot",
            "name": "depot_end",
            "order_no": None,
            "coord": [float(depot["lat"]), float(depot["lng"])],
            "eta_minutes_from_start": int(round(elapsed_min)),
            "leg_distance_km": round(back_km, 2),
        }
    )

    return {
        "status": "success",
        "total_distance_km": round(total_km, 2),
        "total_time_minutes": int(round(elapsed_min)),
        "route_nodes": route_nodes,
        "merged_orders": len(orders),
        "traffic_index": traffic_index,
        "weather_index": weather_index,
        "history_index": history_index,
        "effective_speed_kmh": round(speed, 2),
    }


def solve_vrp_with_constraints(
    depot: Dict[str, float],
    orders: Sequence[Dict[str, object]],
    vehicle_count: int = 1,
    vehicle_capacity: int = 3000,
    max_route_minutes: int = 600,
    traffic_index: float = 1.0,
    weather_index: float = 1.0,
    history_index: float = 1.0,
    base_speed_kmh: float = 42.0,
    time_limit_sec: int = 5,
) -> Dict[str, object]:
    """Solve VRP with OR-Tools (capacity + time-window constraints).

    Returns a dict with:
    - `vehicle_routes`: per-vehicle route nodes
    - `route_nodes`: compatibility field using the primary route
    - `solver`: 'ortools'
    """
    if not orders:
        return {
            "status": "error",
            "message": "no orders to route",
            "solver": "ortools",
            "vehicle_routes": [],
            "route_nodes": [],
            "total_distance_km": 0.0,
            "total_time_minutes": 0,
        }

    if pywrapcp is None or routing_enums_pb2 is None:
        return {
            "status": "error",
            "message": "ortools_not_available",
            "solver": "ortools",
            "vehicle_routes": [],
            "route_nodes": [],
            "total_distance_km": 0.0,
            "total_time_minutes": 0,
        }

    vehicle_count = max(1, int(vehicle_count))
    vehicle_capacity = max(1, int(vehicle_capacity))
    max_route_minutes = max(60, int(max_route_minutes))
    time_limit_sec = max(1, int(time_limit_sec))

    traffic = max(0.5, float(traffic_index))
    weather = max(0.5, float(weather_index))
    history = max(0.5, float(history_index))
    speed = max(8.0, float(base_speed_kmh) / (traffic * weather * history))

    nodes: List[Dict[str, object]] = [{"kind": "depot", "lat": float(depot["lat"]), "lng": float(depot["lng"]), "demand": 0}]
    for idx, order in enumerate(orders, start=1):
        nodes.append(
            {
                "kind": "delivery",
                "order_no": order.get("order_no") or f"ORD-{idx}",
                "name": order.get("name") or f"order_{idx}",
                "lat": float(order["lat"]),
                "lng": float(order["lng"]),
                "demand": int(order.get("demand_qty", 1) or 1),
                "tw_start_min": int(order.get("tw_start_min", 0) or 0),
                "tw_end_min": int(order.get("tw_end_min", max_route_minutes) or max_route_minutes),
                "service_min": int(order.get("service_min", 5) or 0),
            }
        )

    n = len(nodes)
    distance_matrix_m: List[List[int]] = [[0] * n for _ in range(n)]
    time_matrix_min: List[List[int]] = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            dist_m = haversine_meters(float(nodes[i]["lat"]), float(nodes[i]["lng"]), float(nodes[j]["lat"]), float(nodes[j]["lng"]))
            distance_matrix_m[i][j] = int(round(dist_m))
            move_min = max(1, int(round((dist_m / 1000.0 / speed) * 60.0)))
            service_min = int(nodes[i].get("service_min", 0) or 0) if nodes[i]["kind"] == "delivery" else 0
            time_matrix_min[i][j] = move_min + service_min

    manager = pywrapcp.RoutingIndexManager(n, vehicle_count, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix_m[from_node][to_node]

    transit_cb_idx = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    demands = [int(node.get("demand", 0) or 0) for node in nodes]

    def demand_callback(from_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        return demands[from_node]

    demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_idx,
        0,
        [vehicle_capacity] * vehicle_count,
        True,
        "Capacity",
    )

    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return time_matrix_min[from_node][to_node]

    time_cb_idx = routing.RegisterTransitCallback(time_callback)
    routing.AddDimension(
        time_cb_idx,
        30,  # wait slack
        max_route_minutes,
        True,
        "Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    depot_idx = manager.NodeToIndex(0)
    time_dim.CumulVar(depot_idx).SetRange(0, max_route_minutes)
    for node_id in range(1, n):
        idx = manager.NodeToIndex(node_id)
        start = max(0, int(nodes[node_id].get("tw_start_min", 0) or 0))
        end = max(start + 1, int(nodes[node_id].get("tw_end_min", max_route_minutes) or max_route_minutes))
        end = min(end, max_route_minutes)
        time_dim.CumulVar(idx).SetRange(start, end)

    penalty = 200000
    for node_id in range(1, n):
        routing.AddDisjunction([manager.NodeToIndex(node_id)], penalty)

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search.time_limit.seconds = time_limit_sec

    solution = routing.SolveWithParameters(search)
    if solution is None:
        return {
            "status": "error",
            "message": "no_feasible_solution",
            "solver": "ortools",
            "vehicle_routes": [],
            "route_nodes": [],
            "total_distance_km": 0.0,
            "total_time_minutes": 0,
            "effective_speed_kmh": round(speed, 2),
        }

    vehicle_routes: List[Dict[str, object]] = []
    total_distance_km = 0.0
    total_time_minutes = 0
    visited_orders: List[str] = []

    for vehicle_idx in range(vehicle_count):
        index = routing.Start(vehicle_idx)
        route_nodes: List[Dict[str, object]] = []
        route_distance_m = 0
        seq = 0

        while not routing.IsEnd(index):
            node_id = manager.IndexToNode(index)
            node = nodes[node_id]
            eta = int(solution.Value(time_dim.CumulVar(index)))
            route_nodes.append(
                {
                    "seq": seq,
                    "vehicle_index": vehicle_idx,
                    "node_type": str(node["kind"]),
                    "name": str(node.get("name") or "depot"),
                    "order_no": node.get("order_no"),
                    "coord": [float(node["lat"]), float(node["lng"])],
                    "eta_minutes_from_start": eta,
                    "leg_distance_km": 0.0,
                }
            )

            next_index = solution.Value(routing.NextVar(index))
            route_distance_m += routing.GetArcCostForVehicle(index, next_index, vehicle_idx)
            index = next_index
            seq += 1

        end_node = nodes[manager.IndexToNode(index)]
        end_eta = int(solution.Value(time_dim.CumulVar(index)))
        route_nodes.append(
            {
                "seq": seq,
                "vehicle_index": vehicle_idx,
                "node_type": str(end_node["kind"]),
                "name": str(end_node.get("name") or "depot"),
                "order_no": end_node.get("order_no"),
                "coord": [float(end_node["lat"]), float(end_node["lng"])],
                "eta_minutes_from_start": end_eta,
                "leg_distance_km": 0.0,
            }
        )

        # Derive leg distance per step for presentation.
        for i in range(1, len(route_nodes)):
            prev = route_nodes[i - 1]["coord"]
            curr = route_nodes[i]["coord"]
            leg_km = haversine_meters(float(prev[0]), float(prev[1]), float(curr[0]), float(curr[1])) / 1000.0
            route_nodes[i]["leg_distance_km"] = round(leg_km, 2)

        delivered = [n["order_no"] for n in route_nodes if n.get("order_no")]
        visited_orders.extend([str(o) for o in delivered])

        route_distance_km = round(route_distance_m / 1000.0, 2)
        route_time_minutes = end_eta
        total_distance_km += route_distance_km
        total_time_minutes = max(total_time_minutes, route_time_minutes)

        vehicle_routes.append(
            {
                "vehicle_index": vehicle_idx,
                "distance_km": route_distance_km,
                "duration_min": route_time_minutes,
                "delivered_orders": delivered,
                "route_nodes": route_nodes,
            }
        )

    vehicle_routes.sort(key=lambda x: (len(x["delivered_orders"]), x["distance_km"]), reverse=True)
    primary_route_nodes = vehicle_routes[0]["route_nodes"] if vehicle_routes else []
    dropped = [str(order.get("order_no")) for order in orders if str(order.get("order_no")) not in set(visited_orders)]

    return {
        "status": "success",
        "solver": "ortools",
        "vehicle_routes": vehicle_routes,
        "route_nodes": primary_route_nodes,  # compatibility for existing callers
        "total_distance_km": round(total_distance_km, 2),
        "total_time_minutes": int(total_time_minutes),
        "merged_orders": len(orders),
        "served_orders": len(visited_orders),
        "dropped_orders": dropped,
        "traffic_index": traffic_index,
        "weather_index": weather_index,
        "history_index": history_index,
        "effective_speed_kmh": round(speed, 2),
        "vehicle_count": vehicle_count,
        "vehicle_capacity": vehicle_capacity,
        "max_route_minutes": max_route_minutes,
    }


def propose_reroute(
    current_lat: float,
    current_lng: float,
    remaining_orders: Sequence[Dict[str, object]],
    traffic_index: float = 1.0,
    weather_index: float = 1.0,
    history_index: float = 1.0,
) -> Dict[str, object]:
    """Generate a reroute suggestion from current location."""
    depot_like = {"lat": current_lat, "lng": current_lng}
    result = build_greedy_vrp_plan(
        depot=depot_like,
        orders=remaining_orders,
        traffic_index=traffic_index,
        weather_index=weather_index,
        history_index=history_index,
    )
    result["reroute"] = True
    return result


def estimate_eta_minutes(
    current_lat: float,
    current_lng: float,
    dest_lat: float,
    dest_lng: float,
    traffic_index: float = 1.0,
    avg_speed_kmh: float = 40.0,
) -> int:
    """Estimate ETA from current point to destination."""
    dist_km = haversine_meters(current_lat, current_lng, dest_lat, dest_lng) / 1000.0
    speed = max(8.0, avg_speed_kmh / max(0.5, traffic_index))
    return int(round((dist_km / speed) * 60.0))


def route_deviation_meters(
    current_lat: float, current_lng: float, planned_points: Iterable[Sequence[float]]
) -> float:
    """Approximate route deviation by min distance to planned polyline vertices."""
    min_m = float("inf")
    for point in planned_points:
        lat = float(point[0])
        lng = float(point[1])
        min_m = min(min_m, haversine_meters(current_lat, current_lng, lat, lng))
    return 0.0 if min_m == float("inf") else round(min_m, 2)


def solve_dynamic_routing() -> Dict[str, object]:
    """Backward-compatible demo endpoint for old `/api/plan_route` route."""
    depot = {"lat": 30.2904, "lng": 120.0673}
    demo_orders = [
        {"order_no": "ORD-001", "name": "Shaoxing Stop", "lat": 29.9958, "lng": 120.5861},
        {"order_no": "ORD-002", "name": "Ningbo Stop", "lat": 29.8753, "lng": 121.5503},
        {"order_no": "ORD-003", "name": "Zhoushan Stop", "lat": 30.0198, "lng": 122.1069},
    ]
    route = build_greedy_vrp_plan(depot=depot, orders=demo_orders, traffic_index=1.1, weather_index=1.0, history_index=1.0)
    return route

