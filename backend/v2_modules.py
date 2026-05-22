"""V2 backend modules for smart logistics competition requirements.

This module is intentionally additive:
- Keeps legacy endpoints unchanged.
- Registers new models and `/api/v2/*` routes.
"""

import datetime
import hashlib
import json
import os
from typing import List, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Session

from backend.algorithms import (
    build_greedy_vrp_plan,
    estimate_eta_minutes,
    fetch_driving_route,
    geocode_address,
    enrich_route_plan_with_provider,
    propose_reroute,
    route_deviation_meters,
    score_warehouse_candidates,
    solve_vrp_with_constraints,
)


def register_v2_api(app, Base, engine, get_db, Vehicle, Device, Alert) -> None:
    """Register V2 data models and APIs once."""
    if getattr(app.state, "_v2_registered", False):
        return
    app.state._v2_registered = True

    def _utcnow() -> datetime.datetime:
        return datetime.datetime.utcnow()

    def _iso(dt: Optional[datetime.datetime]) -> Optional[str]:
        if not dt:
            return None
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _event_hash(payload: dict) -> str:
        content = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    class WarehouseNode(Base):
        __tablename__ = "warehouse_nodes"
        id = Column(Integer, primary_key=True, index=True)
        code = Column(String, unique=True, index=True)
        name = Column(String, nullable=False)
        address = Column(String, nullable=False)
        lat = Column(Float, nullable=False)
        lng = Column(Float, nullable=False)
        available_vehicles = Column(Integer, default=0)
        created_at = Column(DateTime, default=_utcnow)
        updated_at = Column(DateTime, default=_utcnow)

    class WarehouseInventory(Base):
        __tablename__ = "warehouse_inventory"
        id = Column(Integer, primary_key=True, index=True)
        warehouse_id = Column(Integer, ForeignKey("warehouse_nodes.id"), index=True)
        sku_code = Column(String, index=True)
        quantity = Column(Integer, default=0)
        reserved_qty = Column(Integer, default=0)
        updated_at = Column(DateTime, default=_utcnow)

    class DispatchOrder(Base):
        __tablename__ = "dispatch_orders"
        id = Column(Integer, primary_key=True, index=True)
        order_no = Column(String, unique=True, index=True)
        receive_address = Column(String)
        target_lat = Column(Float)
        target_lng = Column(Float)
        demand_qty = Column(Integer)
        sla_minutes = Column(Integer)
        sku_code = Column(String, default="JIAOBAI")
        matched_warehouse_id = Column(Integer, ForeignKey("warehouse_nodes.id"), nullable=True)
        status = Column(String, default="pending")
        created_at = Column(DateTime, default=_utcnow)

    class RoutePlan(Base):
        __tablename__ = "route_plans"
        id = Column(Integer, primary_key=True, index=True)
        vehicle_id = Column(Integer, ForeignKey("vehicles.id"), index=True)
        status = Column(String, default="planned")
        order_nos_json = Column(Text)
        traffic_index = Column(Float, default=1.0)
        weather_index = Column(Float, default=1.0)
        history_index = Column(Float, default=1.0)
        planned_distance_km = Column(Float, default=0.0)
        planned_duration_min = Column(Integer, default=0)
        created_at = Column(DateTime, default=_utcnow)
        updated_at = Column(DateTime, default=_utcnow)

    class RoutePlanNode(Base):
        __tablename__ = "route_plan_nodes"
        id = Column(Integer, primary_key=True, index=True)
        route_plan_id = Column(Integer, ForeignKey("route_plans.id"), index=True)
        seq = Column(Integer, index=True)
        node_type = Column(String)  # depot | delivery
        order_no = Column(String, nullable=True)
        lat = Column(Float)
        lng = Column(Float)
        eta_minutes = Column(Integer, default=0)
        leg_distance_km = Column(Float, default=0.0)

    class GPSTrackPoint(Base):
        __tablename__ = "gps_track_points"
        id = Column(Integer, primary_key=True, index=True)
        vehicle_id = Column(Integer, ForeignKey("vehicles.id"), index=True)
        device_sn = Column(String, index=True)
        lat = Column(Float)
        lng = Column(Float)
        speed_kmh = Column(Float, default=0.0)
        temperature_c = Column(Float, nullable=True)
        humidity_pct = Column(Float, nullable=True)
        door_open = Column(Integer, default=0)
        collected_at = Column(DateTime, index=True)
        created_at = Column(DateTime, default=_utcnow)

    class TraceProduct(Base):
        __tablename__ = "trace_products"
        id = Column(Integer, primary_key=True, index=True)
        product_uid = Column(String, unique=True, index=True)
        batch_no = Column(String, index=True)
        quality_grade = Column(String)
        weight_kg = Column(Float)
        sort_image_url = Column(String, nullable=True)
        created_at = Column(DateTime, default=_utcnow)

    class TraceSortingEvent(Base):
        __tablename__ = "trace_sorting_events"
        id = Column(Integer, primary_key=True, index=True)
        product_uid = Column(String, index=True)
        quality_grade = Column(String)
        weight_kg = Column(Float)
        anomaly_flag = Column(String, default="none")
        image_url = Column(String, nullable=True)
        event_time = Column(DateTime, default=_utcnow)

    class TraceStorageEvent(Base):
        __tablename__ = "trace_storage_events"
        id = Column(Integer, primary_key=True, index=True)
        product_uid = Column(String, index=True)
        warehouse_code = Column(String, index=True)
        event_type = Column(String)  # inbound | outbound | move
        location_code = Column(String)
        temperature_c = Column(Float, nullable=True)
        humidity_pct = Column(Float, nullable=True)
        event_time = Column(DateTime, default=_utcnow)

    class TraceDeliveryEvent(Base):
        __tablename__ = "trace_delivery_events"
        id = Column(Integer, primary_key=True, index=True)
        product_uid = Column(String, index=True)
        vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
        event_type = Column(String)  # depart | in_transit | signed
        lat = Column(Float, nullable=True)
        lng = Column(Float, nullable=True)
        temperature_c = Column(Float, nullable=True)
        humidity_pct = Column(Float, nullable=True)
        sign_time = Column(DateTime, nullable=True)
        event_time = Column(DateTime, default=_utcnow)

    class TraceChainProof(Base):
        __tablename__ = "trace_chain_proofs"
        id = Column(Integer, primary_key=True, index=True)
        product_uid = Column(String, index=True)
        hash_value = Column(String, index=True)
        chain_name = Column(String, default="mock-chain")
        tx_id = Column(String, index=True)
        created_at = Column(DateTime, default=_utcnow)

    Base.metadata.create_all(bind=engine)

    class WarehouseUpsertReq(BaseModel):
        code: str
        name: str
        address: str
        lat: float
        lng: float
        available_vehicles: int = 0

    class WarehouseInventoryReq(BaseModel):
        sku_code: str = "JIAOBAI"
        quantity: int
        reserved_qty: int = 0

    class MatchWarehouseReq(BaseModel):
        order_no: str
        receive_address: Optional[str] = None
        target_lat: Optional[float] = None
        target_lng: Optional[float] = None
        demand_qty: int
        sla_minutes: int = 240
        sku_code: str = "JIAOBAI"
        traffic_index: float = 1.0
        map_provider: Optional[str] = None

    class RouteOrder(BaseModel):
        order_no: str
        lat: float
        lng: float
        name: Optional[str] = None
        demand_qty: int = 1
        tw_start_min: int = 0
        tw_end_min: int = 600
        service_min: int = 5

    class RoutePlanReq(BaseModel):
        vehicle_id: int
        depot_lat: float
        depot_lng: float
        orders: List[RouteOrder]
        solver: str = "ortools"
        vehicle_count: int = 1
        vehicle_capacity: int = 3000
        max_route_minutes: int = 600
        solver_time_limit_sec: int = 5
        traffic_index: float = 1.0
        weather_index: float = 1.0
        history_index: float = 1.0
        map_provider: Optional[str] = None

    class RerouteReq(BaseModel):
        route_plan_id: int
        current_lat: float
        current_lng: float
        remaining_order_nos: List[str] = []
        traffic_index: float = 1.0
        weather_index: float = 1.0
        history_index: float = 1.0
        map_provider: Optional[str] = None

    class GPSUploadReq(BaseModel):
        vehicle_id: int
        device_sn: str
        lat: float
        lng: float
        speed_kmh: float = 0.0
        temperature_c: Optional[float] = None
        humidity_pct: Optional[float] = None
        door_open: bool = False
        collected_at: Optional[datetime.datetime] = None

    class TraceProductReq(BaseModel):
        product_uid: str
        batch_no: str
        quality_grade: str
        weight_kg: float
        sort_image_url: Optional[str] = None

    class TraceSortingReq(BaseModel):
        product_uid: str
        quality_grade: str
        weight_kg: float
        anomaly_flag: str = "none"
        image_url: Optional[str] = None
        event_time: Optional[datetime.datetime] = None

    class TraceStorageReq(BaseModel):
        product_uid: str
        warehouse_code: str
        event_type: str
        location_code: str
        temperature_c: Optional[float] = None
        humidity_pct: Optional[float] = None
        event_time: Optional[datetime.datetime] = None

    class TraceDeliveryReq(BaseModel):
        product_uid: str
        vehicle_id: Optional[int] = None
        event_type: str
        lat: Optional[float] = None
        lng: Optional[float] = None
        temperature_c: Optional[float] = None
        humidity_pct: Optional[float] = None
        sign_time: Optional[datetime.datetime] = None
        event_time: Optional[datetime.datetime] = None

    def _get_or_create_gps_device(db: Session, vehicle_id: int, sn: str, now: datetime.datetime):
        dev = db.query(Device).filter(Device.sn == sn).first()
        if dev:
            return dev
        dev = Device(
            sn=sn,
            type="GPS Terminal",
            vehicle_id=vehicle_id,
            install_pos="vehicle",
            status="online",
            last_data="{}",
            last_active=now,
        )
        db.add(dev)
        db.flush()
        return dev

    def _append_chain_proof(db: Session, product_uid: str, payload: dict):
        digest = _event_hash(payload)
        proof = TraceChainProof(
            product_uid=product_uid,
            hash_value=digest,
            chain_name="mock-chain",
            tx_id=f"mock-{digest[:20]}",
            created_at=_utcnow(),
        )
        db.add(proof)
        return proof

    def _map_provider(req_provider: Optional[str]) -> str:
        return (req_provider or os.getenv("MAP_PROVIDER", "free")).lower()

    def _amap_key() -> Optional[str]:
        return os.getenv("AMAP_KEY")

    def _baidu_ak() -> Optional[str]:
        return os.getenv("BAIDU_AK")

    @app.get("/api/v2/warehouses")
    def v2_list_warehouses(sku_code: str = Query("JIAOBAI"), db: Session = Depends(get_db)):
        warehouses = db.query(WarehouseNode).all()
        rows = []
        for wh in warehouses:
            inv = (
                db.query(WarehouseInventory)
                .filter(WarehouseInventory.warehouse_id == wh.id, WarehouseInventory.sku_code == sku_code)
                .first()
            )
            qty = inv.quantity if inv else 0
            reserved = inv.reserved_qty if inv else 0
            rows.append(
                {
                    "id": wh.id,
                    "code": wh.code,
                    "name": wh.name,
                    "address": wh.address,
                    "lat": wh.lat,
                    "lng": wh.lng,
                    "available_vehicles": wh.available_vehicles,
                    "sku_code": sku_code,
                    "available_qty": max(0, qty - reserved),
                    "updated_at": _iso(wh.updated_at),
                }
            )
        return {"code": 200, "data": rows}

    @app.post("/api/v2/warehouses")
    def v2_upsert_warehouse(req: WarehouseUpsertReq, db: Session = Depends(get_db)):
        warehouse = db.query(WarehouseNode).filter(WarehouseNode.code == req.code).first()
        now = _utcnow()
        if warehouse is None:
            warehouse = WarehouseNode(
                code=req.code,
                name=req.name,
                address=req.address,
                lat=req.lat,
                lng=req.lng,
                available_vehicles=max(0, req.available_vehicles),
                created_at=now,
                updated_at=now,
            )
            db.add(warehouse)
        else:
            warehouse.name = req.name
            warehouse.address = req.address
            warehouse.lat = req.lat
            warehouse.lng = req.lng
            warehouse.available_vehicles = max(0, req.available_vehicles)
            warehouse.updated_at = now
        db.commit()
        db.refresh(warehouse)
        return {"code": 200, "data": {"id": warehouse.id, "code": warehouse.code, "name": warehouse.name}}

    @app.post("/api/v2/warehouses/{warehouse_id}/inventory")
    def v2_upsert_warehouse_inventory(
        warehouse_id: int, req: WarehouseInventoryReq, db: Session = Depends(get_db)
    ):
        warehouse = db.query(WarehouseNode).filter(WarehouseNode.id == warehouse_id).first()
        if warehouse is None:
            raise HTTPException(status_code=404, detail="warehouse not found")
        inv = (
            db.query(WarehouseInventory)
            .filter(WarehouseInventory.warehouse_id == warehouse_id, WarehouseInventory.sku_code == req.sku_code)
            .first()
        )
        now = _utcnow()
        if inv is None:
            inv = WarehouseInventory(
                warehouse_id=warehouse_id,
                sku_code=req.sku_code,
                quantity=max(0, req.quantity),
                reserved_qty=max(0, req.reserved_qty),
                updated_at=now,
            )
            db.add(inv)
        else:
            inv.quantity = max(0, req.quantity)
            inv.reserved_qty = max(0, min(req.reserved_qty, inv.quantity))
            inv.updated_at = now
        db.commit()
        db.refresh(inv)
        return {
            "code": 200,
            "data": {
                "warehouse_id": warehouse_id,
                "sku_code": inv.sku_code,
                "quantity": inv.quantity,
                "reserved_qty": inv.reserved_qty,
                "available_qty": max(0, inv.quantity - inv.reserved_qty),
            },
        }

    @app.post("/api/v2/orders/match-warehouse")
    def v2_match_warehouse(req: MatchWarehouseReq, db: Session = Depends(get_db)):
        if req.demand_qty <= 0:
            raise HTTPException(status_code=400, detail="demand_qty must be > 0")

        provider = _map_provider(req.map_provider)
        target_lat = req.target_lat
        target_lng = req.target_lng
        geocode_info = None
        if target_lat is None or target_lng is None:
            if not req.receive_address:
                raise HTTPException(status_code=400, detail="either coordinates or receive_address is required")
            geocode_info = geocode_address(
                address=req.receive_address,
                provider=provider,
                amap_key=_amap_key(),
                baidu_ak=_baidu_ak(),
            )
            target_lat = float(geocode_info["lat"])
            target_lng = float(geocode_info["lng"])

        candidates = []
        for wh in db.query(WarehouseNode).all():
            inv = (
                db.query(WarehouseInventory)
                .filter(WarehouseInventory.warehouse_id == wh.id, WarehouseInventory.sku_code == req.sku_code)
                .first()
            )
            available_stock = 0
            if inv is not None:
                available_stock = max(0, int(inv.quantity) - int(inv.reserved_qty))
            candidates.append(
                {
                    "id": wh.id,
                    "name": wh.name,
                    "lat": wh.lat,
                    "lng": wh.lng,
                    "stock": available_stock,
                    "vehicles": max(0, wh.available_vehicles),
                }
            )

        ranked = score_warehouse_candidates(
            candidates=candidates,
            target_lat=target_lat,
            target_lng=target_lng,
            demand_qty=req.demand_qty,
            sla_minutes=req.sla_minutes,
            traffic_index=req.traffic_index,
        )
        top = ranked[0] if ranked else None

        order = db.query(DispatchOrder).filter(DispatchOrder.order_no == req.order_no).first()
        if order is None:
            order = DispatchOrder(order_no=req.order_no, created_at=_utcnow())
            db.add(order)

        order.receive_address = req.receive_address
        order.target_lat = target_lat
        order.target_lng = target_lng
        order.demand_qty = req.demand_qty
        order.sla_minutes = req.sla_minutes
        order.sku_code = req.sku_code
        order.matched_warehouse_id = top["id"] if (top and top["feasible"]) else None
        order.status = "matched" if (top and top["feasible"]) else "unmatched"

        if top and top["feasible"]:
            inv = (
                db.query(WarehouseInventory)
                .filter(WarehouseInventory.warehouse_id == int(top["id"]), WarehouseInventory.sku_code == req.sku_code)
                .first()
            )
            if inv is not None:
                inv.reserved_qty = min(inv.quantity, inv.reserved_qty + req.demand_qty)
                inv.updated_at = _utcnow()

        db.commit()
        db.refresh(order)
        return {
            "code": 200,
            "data": {
                "order_no": order.order_no,
                "target": {"lat": target_lat, "lng": target_lng},
                "map_provider": provider,
                "geocode": geocode_info,
                "matched_warehouse_id": order.matched_warehouse_id,
                "status": order.status,
                "ranking": ranked,
            },
        }

    @app.post("/api/v2/routing/plan")
    def v2_plan_route(req: RoutePlanReq, db: Session = Depends(get_db)):
        vehicle = db.query(Vehicle).filter(Vehicle.id == req.vehicle_id).first()
        if vehicle is None:
            raise HTTPException(status_code=404, detail="vehicle not found")
        if not req.orders:
            raise HTTPException(status_code=400, detail="orders cannot be empty")

        orders = [
            {
                "order_no": o.order_no,
                "name": o.name or o.order_no,
                "lat": o.lat,
                "lng": o.lng,
                "demand_qty": o.demand_qty,
                "tw_start_min": o.tw_start_min,
                "tw_end_min": o.tw_end_min,
                "service_min": o.service_min,
            }
            for o in req.orders
        ]
        solver_mode = (req.solver or "ortools").lower()
        if solver_mode == "ortools":
            result = solve_vrp_with_constraints(
                depot={"lat": req.depot_lat, "lng": req.depot_lng},
                orders=orders,
                vehicle_count=req.vehicle_count,
                vehicle_capacity=req.vehicle_capacity,
                max_route_minutes=req.max_route_minutes,
                traffic_index=req.traffic_index,
                weather_index=req.weather_index,
                history_index=req.history_index,
                time_limit_sec=req.solver_time_limit_sec,
            )
            if result.get("status") != "success":
                greedy = build_greedy_vrp_plan(
                    depot={"lat": req.depot_lat, "lng": req.depot_lng},
                    orders=orders,
                    traffic_index=req.traffic_index,
                    weather_index=req.weather_index,
                    history_index=req.history_index,
                )
                greedy["solver"] = "greedy"
                greedy["solver_fallback_reason"] = result.get("message", "ortools_failed")
                result = greedy
        else:
            result = build_greedy_vrp_plan(
                depot={"lat": req.depot_lat, "lng": req.depot_lng},
                orders=orders,
                traffic_index=req.traffic_index,
                weather_index=req.weather_index,
                history_index=req.history_index,
            )
            result["solver"] = "greedy"

        provider = _map_provider(req.map_provider)
        vehicle_route_geometries = []
        if result.get("vehicle_routes"):
            total_distance = 0.0
            total_duration = 0
            for vr in result["vehicle_routes"]:
                geom = enrich_route_plan_with_provider(
                    route_nodes=vr["route_nodes"],
                    provider=provider,
                    amap_key=_amap_key(),
                    baidu_ak=_baidu_ak(),
                )
                vr["route_geometry"] = geom
                if geom["distance_km"] > 0:
                    vr["distance_km"] = geom["distance_km"]
                if geom["duration_min"] > 0:
                    vr["duration_min"] = geom["duration_min"]
                total_distance += float(vr.get("distance_km", 0.0) or 0.0)
                total_duration = max(total_duration, int(vr.get("duration_min", 0) or 0))
                vehicle_route_geometries.append(
                    {
                        "vehicle_index": vr.get("vehicle_index", 0),
                        "distance_km": vr.get("distance_km", 0.0),
                        "duration_min": vr.get("duration_min", 0),
                        "provider": geom.get("provider", provider),
                        "used_fallback": geom.get("used_fallback", True),
                    }
                )
            result["total_distance_km"] = round(total_distance, 2)
            result["total_time_minutes"] = int(total_duration)
            result["vehicle_route_geometries"] = vehicle_route_geometries

        route_geometry = enrich_route_plan_with_provider(
            route_nodes=result.get("route_nodes", []),
            provider=provider,
            amap_key=_amap_key(),
            baidu_ak=_baidu_ak(),
        )
        if route_geometry["distance_km"] > 0 and not result.get("vehicle_routes"):
            result["total_distance_km"] = route_geometry["distance_km"]
        if route_geometry["duration_min"] > 0 and not result.get("vehicle_routes"):
            result["total_time_minutes"] = route_geometry["duration_min"]
        result["route_geometry"] = route_geometry
        result["map_provider"] = provider

        plan = RoutePlan(
            vehicle_id=req.vehicle_id,
            status="planned",
            order_nos_json=json.dumps([o.order_no for o in req.orders], ensure_ascii=False),
            traffic_index=req.traffic_index,
            weather_index=req.weather_index,
            history_index=req.history_index,
            planned_distance_km=result["total_distance_km"],
            planned_duration_min=result["total_time_minutes"],
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(plan)
        db.flush()
        for node in result["route_nodes"]:
            db.add(
                RoutePlanNode(
                    route_plan_id=plan.id,
                    seq=node["seq"],
                    node_type=node["node_type"],
                    order_no=node.get("order_no"),
                    lat=float(node["coord"][0]),
                    lng=float(node["coord"][1]),
                    eta_minutes=int(node["eta_minutes_from_start"]),
                    leg_distance_km=float(node["leg_distance_km"]),
                )
            )
        db.commit()
        db.refresh(plan)
        return {
            "code": 200,
            "data": {
                "route_plan_id": plan.id,
                "vehicle_id": req.vehicle_id,
                "merged_orders": len(req.orders),
                "result": result,
            },
        }

    @app.post("/api/v2/routing/reroute")
    def v2_reroute(req: RerouteReq, db: Session = Depends(get_db)):
        plan = db.query(RoutePlan).filter(RoutePlan.id == req.route_plan_id).first()
        if plan is None:
            raise HTTPException(status_code=404, detail="route plan not found")

        all_nodes = (
            db.query(RoutePlanNode)
            .filter(RoutePlanNode.route_plan_id == plan.id, RoutePlanNode.node_type == "delivery")
            .order_by(RoutePlanNode.seq.asc())
            .all()
        )
        remaining_filter = set(req.remaining_order_nos or [])
        remaining_orders = []
        for node in all_nodes:
            if remaining_filter and node.order_no not in remaining_filter:
                continue
            remaining_orders.append({"order_no": node.order_no, "name": node.order_no, "lat": node.lat, "lng": node.lng})

        if not remaining_orders:
            return {"code": 200, "data": {"status": "done", "message": "no remaining orders"}}

        reroute = propose_reroute(
            current_lat=req.current_lat,
            current_lng=req.current_lng,
            remaining_orders=remaining_orders,
            traffic_index=req.traffic_index,
            weather_index=req.weather_index,
            history_index=req.history_index,
        )
        provider = _map_provider(req.map_provider)
        reroute_geometry = enrich_route_plan_with_provider(
            route_nodes=reroute["route_nodes"],
            provider=provider,
            amap_key=_amap_key(),
            baidu_ak=_baidu_ak(),
        )
        if reroute_geometry["distance_km"] > 0:
            reroute["total_distance_km"] = reroute_geometry["distance_km"]
        if reroute_geometry["duration_min"] > 0:
            reroute["total_time_minutes"] = reroute_geometry["duration_min"]
        reroute["route_geometry"] = reroute_geometry
        reroute["map_provider"] = provider
        return {"code": 200, "data": reroute}

    @app.post("/api/v2/gps/upload")
    def v2_upload_gps(req: GPSUploadReq, db: Session = Depends(get_db)):
        vehicle = db.query(Vehicle).filter(Vehicle.id == req.vehicle_id).first()
        if vehicle is None:
            raise HTTPException(status_code=404, detail="vehicle not found")

        ts = req.collected_at or _utcnow()
        device = _get_or_create_gps_device(db, req.vehicle_id, req.device_sn, ts)
        last = (
            db.query(GPSTrackPoint)
            .filter(GPSTrackPoint.vehicle_id == req.vehicle_id)
            .order_by(GPSTrackPoint.collected_at.desc())
            .first()
        )

        interval_sec = None
        warnings: List[str] = []
        if last is not None:
            interval_sec = max(0.0, (ts - last.collected_at).total_seconds())
            if interval_sec > 45:
                warnings.append("gps_interval_exceeded")

        row = GPSTrackPoint(
            vehicle_id=req.vehicle_id,
            device_sn=req.device_sn,
            lat=req.lat,
            lng=req.lng,
            speed_kmh=req.speed_kmh,
            temperature_c=req.temperature_c,
            humidity_pct=req.humidity_pct,
            door_open=1 if req.door_open else 0,
            collected_at=ts,
            created_at=_utcnow(),
        )
        db.add(row)

        device.vehicle_id = req.vehicle_id
        device.status = "online"
        device.last_active = ts
        device.last_data = json.dumps(
            {
                "lat": req.lat,
                "lng": req.lng,
                "speed_kmh": req.speed_kmh,
                "temperature_c": req.temperature_c,
                "humidity_pct": req.humidity_pct,
                "door_open": req.door_open,
                "collected_at": _iso(ts),
            },
            ensure_ascii=False,
        )

        def _create_alert(alert_type: str):
            db.add(Alert(device_id=device.id, alert_type=alert_type, status="unprocessed", created_at=_utcnow()))

        if req.temperature_c is not None and (req.temperature_c < 0.0 or req.temperature_c > 8.0):
            warnings.append("temperature_out_of_range")
            _create_alert("temperature_out_of_range")
        if req.humidity_pct is not None and (req.humidity_pct < 50.0 or req.humidity_pct > 95.0):
            warnings.append("humidity_out_of_range")
            _create_alert("humidity_out_of_range")
        if req.door_open and req.speed_kmh > 1.0:
            warnings.append("door_open_while_moving")
            _create_alert("door_open_while_moving")

        latest_plan = (
            db.query(RoutePlan)
            .filter(RoutePlan.vehicle_id == req.vehicle_id)
            .order_by(RoutePlan.created_at.desc())
            .first()
        )
        deviation_m = None
        eta_minutes = None
        if latest_plan is not None:
            nodes = (
                db.query(RoutePlanNode)
                .filter(RoutePlanNode.route_plan_id == latest_plan.id)
                .order_by(RoutePlanNode.seq.asc())
                .all()
            )
            planned_points = [[n.lat, n.lng] for n in nodes]
            deviation_m = route_deviation_meters(req.lat, req.lng, planned_points)
            if deviation_m > 3000:
                warnings.append("route_deviation")
                _create_alert("route_deviation")

            if nodes:
                destination = nodes[-1]
                provider = _map_provider(None)
                route_eta = fetch_driving_route(
                    origin_lat=req.lat,
                    origin_lng=req.lng,
                    dest_lat=destination.lat,
                    dest_lng=destination.lng,
                    provider=provider,
                    amap_key=_amap_key(),
                    baidu_ak=_baidu_ak(),
                )
                eta_minutes = int(route_eta["duration_min"]) if route_eta.get("duration_min") else estimate_eta_minutes(
                    req.lat, req.lng, destination.lat, destination.lng, traffic_index=1.0
                )

        db.commit()
        db.refresh(row)
        return {
            "code": 200,
            "data": {
                "track_id": row.id,
                "vehicle_id": row.vehicle_id,
                "interval_sec": interval_sec,
                "deviation_m": deviation_m,
                "eta_minutes": eta_minutes,
                "warnings": warnings,
            },
        }

    @app.get("/api/v2/vehicles/{vehicle_id}/gps/latest")
    def v2_gps_latest(vehicle_id: int, db: Session = Depends(get_db)):
        row = (
            db.query(GPSTrackPoint)
            .filter(GPSTrackPoint.vehicle_id == vehicle_id)
            .order_by(GPSTrackPoint.collected_at.desc())
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="no gps data")
        return {
            "code": 200,
            "data": {
                "track_id": row.id,
                "vehicle_id": row.vehicle_id,
                "device_sn": row.device_sn,
                "lat": row.lat,
                "lng": row.lng,
                "speed_kmh": row.speed_kmh,
                "temperature_c": row.temperature_c,
                "humidity_pct": row.humidity_pct,
                "door_open": bool(row.door_open),
                "collected_at": _iso(row.collected_at),
            },
        }

    @app.get("/api/v2/vehicles/{vehicle_id}/gps/history")
    def v2_gps_history(
        vehicle_id: int, limit: int = Query(120, ge=1, le=2000), db: Session = Depends(get_db)
    ):
        rows = (
            db.query(GPSTrackPoint)
            .filter(GPSTrackPoint.vehicle_id == vehicle_id)
            .order_by(GPSTrackPoint.collected_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "code": 200,
            "data": [
                {
                    "track_id": r.id,
                    "lat": r.lat,
                    "lng": r.lng,
                    "speed_kmh": r.speed_kmh,
                    "temperature_c": r.temperature_c,
                    "humidity_pct": r.humidity_pct,
                    "door_open": bool(r.door_open),
                    "collected_at": _iso(r.collected_at),
                }
                for r in rows
            ],
        }

    @app.post("/api/v2/trace/products")
    def v2_create_trace_product(req: TraceProductReq, db: Session = Depends(get_db)):
        existing = db.query(TraceProduct).filter(TraceProduct.product_uid == req.product_uid).first()
        if existing:
            raise HTTPException(status_code=400, detail="product_uid already exists")
        row = TraceProduct(
            product_uid=req.product_uid,
            batch_no=req.batch_no,
            quality_grade=req.quality_grade,
            weight_kg=req.weight_kg,
            sort_image_url=req.sort_image_url,
            created_at=_utcnow(),
        )
        db.add(row)
        _append_chain_proof(
            db,
            req.product_uid,
            {"event": "create_product", "product_uid": req.product_uid, "batch_no": req.batch_no, "ts": _iso(_utcnow())},
        )
        db.commit()
        db.refresh(row)
        return {"code": 200, "data": {"id": row.id, "product_uid": row.product_uid}}

    @app.post("/api/v2/trace/events/sorting")
    def v2_add_sorting_event(req: TraceSortingReq, db: Session = Depends(get_db)):
        product = db.query(TraceProduct).filter(TraceProduct.product_uid == req.product_uid).first()
        if product is None:
            raise HTTPException(status_code=404, detail="trace product not found")
        event_time = req.event_time or _utcnow()
        ev = TraceSortingEvent(
            product_uid=req.product_uid,
            quality_grade=req.quality_grade,
            weight_kg=req.weight_kg,
            anomaly_flag=req.anomaly_flag,
            image_url=req.image_url,
            event_time=event_time,
        )
        db.add(ev)
        product.quality_grade = req.quality_grade
        product.weight_kg = req.weight_kg
        _append_chain_proof(
            db,
            req.product_uid,
            {
                "event": "sorting",
                "product_uid": req.product_uid,
                "quality_grade": req.quality_grade,
                "weight_kg": req.weight_kg,
                "anomaly_flag": req.anomaly_flag,
                "event_time": _iso(event_time),
            },
        )
        db.commit()
        db.refresh(ev)
        return {"code": 200, "data": {"event_id": ev.id}}

    @app.post("/api/v2/trace/events/storage")
    def v2_add_storage_event(req: TraceStorageReq, db: Session = Depends(get_db)):
        product = db.query(TraceProduct).filter(TraceProduct.product_uid == req.product_uid).first()
        if product is None:
            raise HTTPException(status_code=404, detail="trace product not found")
        event_time = req.event_time or _utcnow()
        ev = TraceStorageEvent(
            product_uid=req.product_uid,
            warehouse_code=req.warehouse_code,
            event_type=req.event_type,
            location_code=req.location_code,
            temperature_c=req.temperature_c,
            humidity_pct=req.humidity_pct,
            event_time=event_time,
        )
        db.add(ev)
        _append_chain_proof(
            db,
            req.product_uid,
            {
                "event": "storage",
                "product_uid": req.product_uid,
                "warehouse_code": req.warehouse_code,
                "event_type": req.event_type,
                "location_code": req.location_code,
                "temperature_c": req.temperature_c,
                "humidity_pct": req.humidity_pct,
                "event_time": _iso(event_time),
            },
        )
        db.commit()
        db.refresh(ev)
        return {"code": 200, "data": {"event_id": ev.id}}

    @app.post("/api/v2/trace/events/delivery")
    def v2_add_delivery_event(req: TraceDeliveryReq, db: Session = Depends(get_db)):
        product = db.query(TraceProduct).filter(TraceProduct.product_uid == req.product_uid).first()
        if product is None:
            raise HTTPException(status_code=404, detail="trace product not found")
        event_time = req.event_time or _utcnow()
        ev = TraceDeliveryEvent(
            product_uid=req.product_uid,
            vehicle_id=req.vehicle_id,
            event_type=req.event_type,
            lat=req.lat,
            lng=req.lng,
            temperature_c=req.temperature_c,
            humidity_pct=req.humidity_pct,
            sign_time=req.sign_time,
            event_time=event_time,
        )
        db.add(ev)
        _append_chain_proof(
            db,
            req.product_uid,
            {
                "event": "delivery",
                "product_uid": req.product_uid,
                "vehicle_id": req.vehicle_id,
                "event_type": req.event_type,
                "lat": req.lat,
                "lng": req.lng,
                "temperature_c": req.temperature_c,
                "humidity_pct": req.humidity_pct,
                "sign_time": _iso(req.sign_time),
                "event_time": _iso(event_time),
            },
        )
        db.commit()
        db.refresh(ev)
        return {"code": 200, "data": {"event_id": ev.id}}

    @app.get("/api/v2/trace/{product_uid}")
    def v2_get_trace(product_uid: str, db: Session = Depends(get_db)):
        product = db.query(TraceProduct).filter(TraceProduct.product_uid == product_uid).first()
        if product is None:
            raise HTTPException(status_code=404, detail="trace product not found")

        sorting = (
            db.query(TraceSortingEvent)
            .filter(TraceSortingEvent.product_uid == product_uid)
            .order_by(TraceSortingEvent.event_time.asc())
            .all()
        )
        storage = (
            db.query(TraceStorageEvent)
            .filter(TraceStorageEvent.product_uid == product_uid)
            .order_by(TraceStorageEvent.event_time.asc())
            .all()
        )
        delivery = (
            db.query(TraceDeliveryEvent)
            .filter(TraceDeliveryEvent.product_uid == product_uid)
            .order_by(TraceDeliveryEvent.event_time.asc())
            .all()
        )
        proofs = (
            db.query(TraceChainProof)
            .filter(TraceChainProof.product_uid == product_uid)
            .order_by(TraceChainProof.created_at.asc())
            .all()
        )

        return {
            "code": 200,
            "data": {
                "product": {
                    "product_uid": product.product_uid,
                    "batch_no": product.batch_no,
                    "quality_grade": product.quality_grade,
                    "weight_kg": product.weight_kg,
                    "sort_image_url": product.sort_image_url,
                    "created_at": _iso(product.created_at),
                },
                "sorting_events": [
                    {
                        "id": e.id,
                        "quality_grade": e.quality_grade,
                        "weight_kg": e.weight_kg,
                        "anomaly_flag": e.anomaly_flag,
                        "image_url": e.image_url,
                        "event_time": _iso(e.event_time),
                    }
                    for e in sorting
                ],
                "storage_events": [
                    {
                        "id": e.id,
                        "warehouse_code": e.warehouse_code,
                        "event_type": e.event_type,
                        "location_code": e.location_code,
                        "temperature_c": e.temperature_c,
                        "humidity_pct": e.humidity_pct,
                        "event_time": _iso(e.event_time),
                    }
                    for e in storage
                ],
                "delivery_events": [
                    {
                        "id": e.id,
                        "vehicle_id": e.vehicle_id,
                        "event_type": e.event_type,
                        "lat": e.lat,
                        "lng": e.lng,
                        "temperature_c": e.temperature_c,
                        "humidity_pct": e.humidity_pct,
                        "sign_time": _iso(e.sign_time),
                        "event_time": _iso(e.event_time),
                    }
                    for e in delivery
                ],
                "chain_proofs": [
                    {
                        "id": p.id,
                        "hash_value": p.hash_value,
                        "chain_name": p.chain_name,
                        "tx_id": p.tx_id,
                        "created_at": _iso(p.created_at),
                    }
                    for p in proofs
                ],
            },
        }
