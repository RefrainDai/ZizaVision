import datetime
import random

from sqlalchemy import text

from backend.app import Alert, Base, Device, SessionLocal, User, Vehicle, engine


def seed_legacy_data(db):
    admin = User(username="admin", password="password123", role="admin")
    db.add(admin)

    vehicles_data = [
        {"plate": "CN-A88990", "driver": "Zhang Jian", "phone": "13800138000", "loc": "Jinghu Expressway K102"},
        {"plate": "CN-B77889", "driver": "Li Shi", "phone": "13912345678", "loc": "Xiongan New Area"},
        {"plate": "CN-C66778", "driver": "Wang Qiang", "phone": "13700001111", "loc": "Dongguan Service Area"},
        {"plate": "CN-D55667", "driver": "Zhao Shi", "phone": "13688889999", "loc": "Wuxi Hub"},
        {"plate": "CN-E33445", "driver": "Liu Shi", "phone": "13566667777", "loc": "Huangshan Service Area"},
    ]

    vehicles = []
    for item in vehicles_data:
        vehicle = Vehicle(
            plate_no=item["plate"],
            driver=item["driver"],
            phone=item["phone"],
            location=item["loc"],
            status="in_transit",
        )
        db.add(vehicle)
        vehicles.append(vehicle)
    db.commit()

    device_types = ["temperature_sensor", "gps_terminal", "vibration_sensor", "rfid_reader"]
    for i in range(1, 21):
        vehicle = random.choice(vehicles)
        d_type = random.choice(device_types)
        device = Device(
            sn=f"SN2026{str(i).zfill(3)}",
            type=d_type,
            vehicle_id=vehicle.id if random.random() > 0.2 else None,
            install_pos="cold_box" if d_type == "temperature_sensor" else "cabin",
            status=random.choices(["online", "offline", "error"], weights=[0.8, 0.1, 0.1])[0],
            last_data=f"temp={random.randint(1, 8)}C" if d_type == "temperature_sensor" else "ok",
            last_active=datetime.datetime.utcnow() - datetime.timedelta(minutes=random.randint(1, 60)),
        )
        db.add(device)
    db.commit()

    alert_types = ["temperature_out_of_range", "route_deviation", "vibration_high", "device_offline"]
    devices = db.query(Device).all()
    for _ in range(12):
        db.add(
            Alert(
                device_id=random.choice(devices).id,
                alert_type=random.choice(alert_types),
                status=random.choices(["unprocessed", "closed"], weights=[0.5, 0.5])[0],
                created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=random.randint(1, 48)),
            )
        )
    db.commit()


def seed_v2_data(db):
    # The rolled-back `backend.app` only manages legacy ORM tables.
    # Ensure V2 seed tables exist so this script can run from a clean DB file.
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS warehouse_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                available_vehicles INTEGER DEFAULT 0,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS warehouse_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER,
                sku_code TEXT,
                quantity INTEGER DEFAULT 0,
                reserved_qty INTEGER DEFAULT 0,
                updated_at DATETIME
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS trace_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_uid TEXT UNIQUE,
                batch_no TEXT,
                quality_grade TEXT,
                weight_kg REAL,
                sort_image_url TEXT,
                created_at DATETIME
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS trace_sorting_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_uid TEXT,
                quality_grade TEXT,
                weight_kg REAL,
                anomaly_flag TEXT,
                image_url TEXT,
                event_time DATETIME
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS trace_storage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_uid TEXT,
                warehouse_code TEXT,
                event_type TEXT,
                location_code TEXT,
                temperature_c REAL,
                humidity_pct REAL,
                event_time DATETIME
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS trace_chain_proofs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_uid TEXT,
                hash_value TEXT,
                chain_name TEXT,
                tx_id TEXT,
                created_at DATETIME
            )
            """
        )
    )

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    warehouses = [
        ("WH-GZ-001", "Guizhou Chagnshun Hub", "Guizhou Changshun", 26.03, 106.45, 15, 2600),
        ("WH-SH-001", "Shanghai Qingpu Hub", "Shanghai Qingpu", 31.1505, 121.1243, 6, 900),
        ("WH-CD-001", "Chengdu Shuangliu Hub", "Chengdu Shuangliu", 30.5744, 103.9237, 8, 1500),
    ]

    # Make seed idempotent: remove previous sample rows before re-inserting.
    sample_codes = [w[0] for w in warehouses]
    code_params = {f"c{i}": code for i, code in enumerate(sample_codes)}
    in_clause = ", ".join([f":c{i}" for i in range(len(sample_codes))])
    db.execute(
        text(
            f"""
            DELETE FROM warehouse_inventory
            WHERE warehouse_id IN (
                SELECT id FROM warehouse_nodes WHERE code IN ({in_clause})
            )
            """
        ),
        code_params,
    )
    db.execute(text(f"DELETE FROM warehouse_nodes WHERE code IN ({in_clause})"), code_params)

    db.execute(text("DELETE FROM trace_chain_proofs WHERE product_uid = :uid"), {"uid": "JB-2026-0001"})
    db.execute(text("DELETE FROM trace_sorting_events WHERE product_uid = :uid"), {"uid": "JB-2026-0001"})
    db.execute(text("DELETE FROM trace_storage_events WHERE product_uid = :uid"), {"uid": "JB-2026-0001"})
    db.execute(text("DELETE FROM trace_products WHERE product_uid = :uid"), {"uid": "JB-2026-0001"})

    for code, name, address, lat, lng, vehicles, qty in warehouses:
        db.execute(
            text(
                """
                INSERT INTO warehouse_nodes(code, name, address, lat, lng, available_vehicles, created_at, updated_at)
                VALUES (:code, :name, :address, :lat, :lng, :vehicles, :created_at, :updated_at)
                """
            ),
            {
                "code": code,
                "name": name,
                "address": address,
                "lat": lat,
                "lng": lng,
                "vehicles": vehicles,
                "created_at": now,
                "updated_at": now,
            },
        )
        wh_id = db.execute(text("SELECT id FROM warehouse_nodes WHERE code=:code"), {"code": code}).scalar_one()
        db.execute(
            text(
                """
                INSERT INTO warehouse_inventory(warehouse_id, sku_code, quantity, reserved_qty, updated_at)
                VALUES (:warehouse_id, 'JIAOBAI', :quantity, 0, :updated_at)
                """
            ),
            {"warehouse_id": wh_id, "quantity": qty, "updated_at": now},
        )

    db.execute(
        text(
            """
            INSERT INTO trace_products(product_uid, batch_no, quality_grade, weight_kg, sort_image_url, created_at)
            VALUES ('JB-2026-0001', 'BATCH-20260307', 'A', 2.35, 'https://example.com/sort/JB-2026-0001.jpg', :created_at)
            """
        ),
        {"created_at": now},
    )
    db.execute(
        text(
            """
            INSERT INTO trace_sorting_events(product_uid, quality_grade, weight_kg, anomaly_flag, image_url, event_time)
            VALUES ('JB-2026-0001', 'A', 2.35, 'none', 'https://example.com/sort/JB-2026-0001.jpg', :event_time)
            """
        ),
        {"event_time": now},
    )
    db.execute(
        text(
            """
            INSERT INTO trace_storage_events(product_uid, warehouse_code, event_type, location_code, temperature_c, humidity_pct, event_time)
            VALUES ('JB-2026-0001', 'WH-GZ-001', 'inbound', 'A1-03-02', 4.0, 78.0, :event_time)
            """
        ),
        {"event_time": now},
    )
    db.execute(
        text(
            """
            INSERT INTO trace_chain_proofs(product_uid, hash_value, chain_name, tx_id, created_at)
            VALUES ('JB-2026-0001', 'seed-hash-jb-2026-0001', 'mock-chain', 'mock-seed-0001', :created_at)
            """
        ),
        {"created_at": now},
    )
    db.commit()


def init_data():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        seed_legacy_data(db)
        seed_v2_data(db)
        print("Database initialized with legacy + v2 sample data.")
    finally:
        db.close()


if __name__ == "__main__":
    init_data()
