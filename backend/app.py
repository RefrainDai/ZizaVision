import math
from typing import List
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel
import datetime
from backend.algorithms import match_best_warehouse, solve_dynamic_routing# --- 数据库配置 ---
DATABASE_URL = "sqlite:///./backend/logistics.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 模型定义 ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    plate_no = Column(String, unique=True)
    driver = Column(String)       # 新增: 驾驶员
    phone = Column(String)        # 新增: 手机号
    location = Column(String)     # 新增: 当前位置
    status = Column(String)

class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True, index=True)
    sn = Column(String, unique=True, index=True)
    type = Column(String)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    install_pos = Column(String)
    status = Column(String)
    last_data = Column(Text)
    last_active = Column(DateTime)

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"))
    alert_type = Column(String)
    status = Column(String)
    created_at = Column(DateTime) # 新增: 告警时间

Base.metadata.create_all(bind=engine)

# --- FastAPI 实例 ---
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- 请求体定义 ---
class DeviceCreate(BaseModel):
    sn: str
    type: str
    install_pos: str

class DeviceUpdate(BaseModel):
    type: str
    install_pos: str
    status: str

# --- API 路由 ---
@app.get("/api/dashboard/stats")
def get_stats(db: Session = Depends(get_db)):
    online_devices = db.query(Device).filter(Device.status == "online").count()
    in_transit_vehicles = db.query(Vehicle).filter(Vehicle.status == "in_transit").count()
    unhandled_alerts = db.query(Alert).filter(Alert.status == "unprocessed").count()
    temp_abnormal = db.query(Alert).filter(Alert.alert_type == "温湿度异常", Alert.status == "unprocessed").count()
    return {
        "online_devices": online_devices, "in_transit_vehicles": in_transit_vehicles,
        "unhandled_alerts": unhandled_alerts, "temp_abnormal": temp_abnormal
    }

@app.get("/api/dashboard/charts")
def get_charts(db: Session = Depends(get_db)):
    line_data = {"times": ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00', '18:00'], "values": [92.1, 95.8, 98.5, 96.5, 98.8, 97.0, 99.5]}
    alerts = db.query(Alert.alert_type).all()
    type_counts = {}
    for a in alerts:
        type_counts[a.alert_type] = type_counts.get(a.alert_type, 0) + 1
    pie_data = [{"name": k, "value": v} for k, v in type_counts.items()]
    if not pie_data: pie_data = [{"name": "暂无告警", "value": 0}]
    return {"line": line_data, "pie": pie_data}

@app.get("/api/devices")
def get_devices(page: int = 1, limit: int = 5, search: str = "", category: str = "", db: Session = Depends(get_db)):
    query = db.query(Device)
    
    # 新增：根据菜单传来的类别进行过滤
    if category == "传感器":
        # 只查询类型中包含“传感器”的设备（如温湿度传感器、震动传感器）
        query = query.filter(Device.type.contains("传感器"))
    elif category == "车载终端":
        # 排除传感器，剩下的归为终端/外设（如GPS终端、RFID读卡器）
        query = query.filter(~Device.type.contains("传感器"))
        
    # 原有的搜索框逻辑
    if search: 
        query = query.filter(Device.sn.contains(search) | Device.type.contains(search))
        
    total = query.count()
    devices = query.offset((page - 1) * limit).limit(limit).all()
    
    res = []
    for d in devices:
        vehicle = db.query(Vehicle).filter(Vehicle.id == d.vehicle_id).first()
        res.append({
            "id": d.id, "sn": d.sn, "type": d.type, "vehicle": vehicle.plate_no if vehicle else "-",
            "install_pos": d.install_pos, "last_data": d.last_data, "status": d.status,
            "last_active": d.last_active.strftime("%Y-%m-%d %H:%M:%S") if d.last_active else "-"
        })
    return {"total": total, "items": res, "page": page, "limit": limit}

@app.post("/api/devices")
def create_device(req: DeviceCreate, db: Session = Depends(get_db)):
    if db.query(Device).filter(Device.sn == req.sn).first(): raise HTTPException(status_code=400, detail="设备编号已存在")
    new_dev = Device(sn=req.sn, type=req.type, install_pos=req.install_pos, status="online", last_data="初始化成功", last_active=datetime.datetime.now())
    db.add(new_dev)
    db.commit()
    return {"success": True}

@app.put("/api/devices/{device_id}")
def update_device(device_id: int, req: DeviceUpdate, db: Session = Depends(get_db)):
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev: raise HTTPException(status_code=404, detail="设备不存在")
    dev.type = req.type; dev.install_pos = req.install_pos; dev.status = req.status
    db.commit()
    return {"success": True}

@app.delete("/api/devices/{device_id}")
def delete_device(device_id: int, db: Session = Depends(get_db)):
    dev = db.query(Device).filter(Device.id == device_id).first()
    if dev: db.delete(dev); db.commit()
    return {"success": True}

# --- 新增：运输与告警API ---
@app.get("/api/vehicles")
@app.get("/api/vehicles")
def get_vehicles(db: Session = Depends(get_db)):
    vehicles = db.query(Vehicle).all()
    
    # 建立一个车牌号到“途径点地名”的映射，这和我们在路线规划里的坐标是一一对应的
    location_map = {
        "沪A·88990": "济南市 (京沪高速K102段)",
        "京B·77889": "保定市 / 雄安新区",
        "粤C·66778": "东莞市 (广深沿江高速)",
        "苏D·55667": "无锡市 (沪宁高速)",
        "浙E·33445": "黄山市 (杭瑞高速)"
    }
    
    res_items = []
    for v in vehicles:
        # 如果能在映射表里找到，就用新的正确地名，否则用数据库原来的
        real_location = location_map.get(v.plate_no, v.location)
        res_items.append({
            "id": v.id, "plate_no": v.plate_no, "driver": v.driver, 
            "phone": v.phone, "location": real_location, "status": v.status
        })
        
    return {"items": res_items}

@app.get("/api/alerts")
def get_alerts(db: Session = Depends(get_db)):
    alerts = db.query(Alert, Device).join(Device, Alert.device_id == Device.id).order_by(Alert.status.desc(), Alert.id.desc()).all()
    return {"items": [{"id": a.id, "device_sn": d.sn, "alert_type": a.alert_type, "status": a.status, "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S")} for a, d in alerts]}

@app.put("/api/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if alert:
        alert.status = "closed"
        db.commit()
    return {"success": True}
# --- 新增：智能调度 API ---
@app.get("/api/match_warehouse")
def api_match_warehouse():
    result = match_best_warehouse()
    return {"code": 200, "data": result}

@app.get("/api/plan_route")
def api_plan_route():
    result = solve_dynamic_routing()
    return {"code": 200, "data": result}

# --- 全新：支持前端动态传参的智能匹配接口 ---
# --- 全新：支持前端动态传参及库存拦截的智能匹配接口 ---
class DynWarehouse(BaseModel):
    id: int
    name: str
    lat: float
    lng: float
    stock: int
    vehicles: int

class DispatchRequest(BaseModel):
    target_lat: float
    target_lng: float
    demand: int      # 【新增】客户订单需求量
    warehouses: List[DynWarehouse]

def haversine(lat1, lon1, lat2, lon2):
    """计算地球上两经纬度点之间的直线距离(米)"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

@app.post("/api/dynamic_match")
def dynamic_match(req: DispatchRequest):
    if not req.warehouses: 
        return {"code": 400, "message": "无可用仓库"}
    
    # 【核心逻辑新增】：硬性过滤，剔除所有库存不足以满足订单需求的仓库
    valid_warehouses = [w for w in req.warehouses if w.stock >= req.demand]
    
    if not valid_warehouses:
        return {"code": 400, "message": f"库存告急！当前没有任何仓库的库存能够满足 {req.demand} 件的订单需求。"}
    
    # 只对库存充足的仓库进行打分
    max_inv = max((w.stock for w in valid_warehouses), default=1)
    max_veh = max((w.vehicles for w in valid_warehouses), default=1)
    max_dist = 1000000 # 参照基准距离 1000km
    
    results = []
    for w in valid_warehouses:
        dist_m = haversine(req.target_lat, req.target_lng, w.lat, w.lng)
        dist_score = max(0, (max_dist - dist_m) / max_dist)
        inv_score = w.stock / max_inv if max_inv > 0 else 0
        veh_score = w.vehicles / max_veh if max_veh > 0 else 0
        total_score = (0.5 * dist_score) + (0.3 * inv_score) + (0.2 * veh_score)
        
        results.append({
            "id": w.id, "name": w.name, "lat": w.lat, "lng": w.lng,
            "distance_km": round(dist_m / 1000, 1),
            "score": round(total_score * 100, 1),
            "details": {"dist": round(dist_score*100), "inv": round(inv_score*100), "veh": round(veh_score*100)}
        })
        
    results.sort(key=lambda x: x['score'], reverse=True)
    return {"code": 200, "data": results}
# --- 静态资源托管 ---
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_home():
    return FileResponse("static/index.html")

@app.get("/api/vehicles/{vehicle_id}/route")
def get_vehicle_route(vehicle_id: int, db: Session = Depends(get_db)):
    # 1. 查出这辆车的真实车牌号
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    plate_no = vehicle.plate_no if vehicle else ""

    # 2. 严格根据初始化的业务文本，匹配真实的经纬度轨迹
    # 坐标格式：[[纬度, 经度], [纬度, 经度]...]
    routes_map = {
        "沪A·88990": [
            [39.9042, 116.4074], # 起点：北京
            [36.6512, 117.1201], # 途径：济南 (京沪高速K102段附近)
            [31.2304, 121.4737]  # 终点：上海
        ],
        "京B·77889": [
            [38.0428, 114.5149], # 起点：石家庄
            [39.0836, 115.9749], # 途径：保定/雄安附近
            [39.9561, 116.2871]  # 终点：北京市海淀区物流园
        ],
        "粤C·66778": [
            [23.1291, 113.2644], # 起点：广州
            [22.8236, 113.6389], # 途径：东莞 (广深沿江高速)
            [22.5431, 114.0579]  # 终点：深圳
        ],
        "苏D·55667": [
            [31.2989, 120.5853], # 起点：苏州
            [31.5689, 120.2990], # 途径：无锡
            [32.1122, 118.9634]  # 终点：南京市栖霞区派送点
        ],
        "浙E·33445": [
            [30.2741, 120.1551], # 起点：杭州
            [29.7150, 118.3375], # 途径：黄山 (杭瑞高速服务区)
            [29.2926, 117.2053]  # 终点：景德镇
        ]
    }

    # 如果没匹配到，给一个默认的备用路线
    default_route = [[30.0, 120.0], [31.0, 121.0], [32.0, 122.0]]
    coords = routes_map.get(plate_no, default_route)
    
    # 将车辆当前位置设在路线中间（即途经点），完美对应文本状态
    mid_index = max(1, len(coords) // 2)
    
    return {
        "route": coords, 
        "start_loc": coords[0],            # 规划起点
        "end_loc": coords[-1],             # 规划终点
        "current_loc": coords[mid_index]   # 车辆当前真实所在位置
    }