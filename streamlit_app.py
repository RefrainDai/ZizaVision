from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


BASE_DIR = Path(__file__).parent


MOCK_API_JS = r'''
const mockState = {
  vehicles: [
    {id: 1, plate_no: '沪A·88990', driver: '张建', phone: '13800138000', location: '济南市 (京沪高速K102段)', status: 'in_transit'},
    {id: 2, plate_no: '京B·77889', driver: '李实', phone: '13912345678', location: '保定市 / 雄安新区', status: 'in_transit'},
    {id: 3, plate_no: '粤C·66778', driver: '王强', phone: '13700001111', location: '东莞市 (广深沿江高速)', status: 'in_transit'},
    {id: 4, plate_no: '苏D·55667', driver: '赵石', phone: '13688889999', location: '无锡市 (沪宁高速)', status: 'in_transit'},
    {id: 5, plate_no: '浙E·33445', driver: '刘时', phone: '13566667777', location: '黄山市 (杭瑞高速)', status: 'in_transit'}
  ],
  devices: [
    {id: 1, sn: 'SN2026001', type: '温湿度传感器', vehicle: '沪A·88990', install_pos: '冷藏箱A区', last_data: '温度4.2℃ / 湿度82%', status: 'online', last_active: '2026-05-22 09:18:20'},
    {id: 2, sn: 'SN2026002', type: 'GPS车载终端', vehicle: '京B·77889', install_pos: '驾驶舱', last_data: '定位正常', status: 'online', last_active: '2026-05-22 09:17:11'},
    {id: 3, sn: 'SN2026003', type: '震动传感器', vehicle: '粤C·66778', install_pos: '货箱底部', last_data: '震动阈值偏高', status: 'error', last_active: '2026-05-22 09:12:45'},
    {id: 4, sn: 'SN2026004', type: 'RFID读卡器', vehicle: '苏D·55667', install_pos: '出入口', last_data: '批次读取正常', status: 'online', last_active: '2026-05-22 09:16:07'},
    {id: 5, sn: 'SN2026005', type: '温湿度传感器', vehicle: '浙E·33445', install_pos: '冷藏箱B区', last_data: '温度9.8℃ / 湿度89%', status: 'error', last_active: '2026-05-22 09:10:31'},
    {id: 6, sn: 'SN2026006', type: '车载网关', vehicle: '-', install_pos: '仓库月台', last_data: '心跳离线', status: 'offline', last_active: '2026-05-22 08:55:02'}
  ],
  alerts: [
    {id: 1, device_sn: 'SN2026005', alert_type: '温湿度异常', status: 'unprocessed', created_at: '2026-05-22 09:10:31'},
    {id: 2, device_sn: 'SN2026003', alert_type: '震动异常', status: 'unprocessed', created_at: '2026-05-22 09:12:45'},
    {id: 3, device_sn: 'SN2026006', alert_type: '设备离线', status: 'closed', created_at: '2026-05-22 08:55:02'},
    {id: 4, device_sn: 'SN2026002', alert_type: '路线偏离', status: 'closed', created_at: '2026-05-21 20:25:10'}
  ]
};

function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const toRad = v => v * Math.PI / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

async function fetchAPI(url, options = {}) {
  await new Promise(resolve => setTimeout(resolve, 120));
  const method = (options.method || 'GET').toUpperCase();
  const parsed = new URL(url, window.location.origin);
  const path = parsed.pathname;

  if (path === '/api/dashboard/stats') {
    return {
      online_devices: mockState.devices.filter(d => d.status === 'online').length,
      in_transit_vehicles: mockState.vehicles.filter(v => v.status === 'in_transit').length,
      unhandled_alerts: mockState.alerts.filter(a => a.status === 'unprocessed').length,
      temp_abnormal: mockState.alerts.filter(a => a.alert_type.includes('温湿度') && a.status === 'unprocessed').length
    };
  }

  if (path === '/api/dashboard/charts') {
    const counts = {};
    mockState.alerts.forEach(a => { counts[a.alert_type] = (counts[a.alert_type] || 0) + 1; });
    return {line: {times: ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00', '18:00'], values: [92.1, 95.8, 98.5, 96.5, 98.8, 97.0, 99.5]}, pie: Object.entries(counts).map(([name, value]) => ({name, value}))};
  }

  if (path === '/api/devices' && method === 'GET') {
    const page = Number(parsed.searchParams.get('page') || 1);
    const limit = Number(parsed.searchParams.get('limit') || 5);
    const search = parsed.searchParams.get('search') || '';
    const category = parsed.searchParams.get('category') || '';
    let items = mockState.devices.filter(d => !search || d.sn.includes(search) || d.type.includes(search));
    if (category === '传感器') items = items.filter(d => d.type.includes('传感器'));
    if (category === '车载终端') items = items.filter(d => !d.type.includes('传感器'));
    return {total: items.length, items: items.slice((page - 1) * limit, page * limit), page, limit};
  }

  if (path === '/api/devices' && method === 'POST') {
    const payload = JSON.parse(options.body || '{}');
    if (mockState.devices.some(d => d.sn === payload.sn)) throw new Error('设备编号已存在');
    const vehicle = mockState.vehicles.find(v => v.id === payload.vehicle_id);
    mockState.devices.push({id: Date.now(), sn: payload.sn, type: payload.type, vehicle: vehicle ? vehicle.plate_no : '-', install_pos: payload.install_pos, last_data: '初始化成功', status: 'online', last_active: new Date().toLocaleString('zh-CN', {hour12: false})});
    return {success: true};
  }

  const deviceMatch = path.match(/^\/api\/devices\/(\d+)$/);
  if (deviceMatch && method === 'PUT') {
    const payload = JSON.parse(options.body || '{}');
    const device = mockState.devices.find(d => d.id === Number(deviceMatch[1]));
    const vehicle = mockState.vehicles.find(v => v.id === payload.vehicle_id);
    if (device) Object.assign(device, {type: payload.type, install_pos: payload.install_pos, status: payload.status, vehicle: vehicle ? vehicle.plate_no : '-'});
    return {success: true};
  }
  if (deviceMatch && method === 'DELETE') {
    mockState.devices = mockState.devices.filter(d => d.id !== Number(deviceMatch[1]));
    return {success: true};
  }

  if (path === '/api/vehicles') return {items: mockState.vehicles};
  const routeMatch = path.match(/^\/api\/vehicles\/(\d+)\/route$/);
  if (routeMatch) {
    const routes = {
      1: [[39.9042, 116.4074], [36.6512, 117.1201], [31.2304, 121.4737]],
      2: [[38.0428, 114.5149], [39.0836, 115.9749], [39.9561, 116.2871]],
      3: [[23.1291, 113.2644], [22.8236, 113.6389], [22.5431, 114.0579]],
      4: [[31.2989, 120.5853], [31.5689, 120.2990], [32.1122, 118.9634]],
      5: [[30.2741, 120.1551], [29.7150, 118.3375], [29.2926, 117.2053]]
    };
    const route = routes[Number(routeMatch[1])] || [[30, 120], [31, 121], [32, 122]];
    return {route, start_loc: route[0], end_loc: route[route.length - 1], current_loc: route[Math.floor(route.length / 2)]};
  }

  if (path === '/api/alerts') return {items: mockState.alerts};
  const alertMatch = path.match(/^\/api\/alerts\/(\d+)\/resolve$/);
  if (alertMatch && method === 'PUT') {
    const alert = mockState.alerts.find(a => a.id === Number(alertMatch[1]));
    if (alert) alert.status = 'closed';
    return {success: true};
  }

  if (path === '/api/dynamic_match' && method === 'POST') {
    const payload = JSON.parse(options.body || '{}');
    const valid = (payload.warehouses || []).filter(w => w.stock >= payload.demand);
    if (!valid.length) return {code: 400, message: `库存告急！当前没有任何仓库的库存能够满足 ${payload.demand} 件的订单需求。`};
    const maxStock = Math.max(...valid.map(w => w.stock), 1);
    const maxVehicles = Math.max(...valid.map(w => w.vehicles), 1);
    const data = valid.map(w => {
      const dist = haversine(payload.target_lat, payload.target_lng, w.lat, w.lng);
      const distScore = Math.max(0, (1000000 - dist) / 1000000);
      const invScore = w.stock / maxStock;
      const vehScore = w.vehicles / maxVehicles;
      const score = 0.5 * distScore + 0.3 * invScore + 0.2 * vehScore;
      return {id: w.id, name: w.name, lat: w.lat, lng: w.lng, distance_km: Math.round(dist / 100) / 10, score: Math.round(score * 1000) / 10, details: {dist: Math.round(distScore * 100), inv: Math.round(invScore * 100), veh: Math.round(vehScore * 100)}};
    }).sort((a, b) => b.score - a.score);
    return {code: 200, data};
  }

  throw new Error(`未实现的模拟接口: ${method} ${path}`);
}
'''


def build_page() -> str:
    html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    js = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    js = js.replace(
        "async function fetchAPI(url, options = {}) {\n    const res = await fetch(url, options);\n    if (!res.ok) throw new Error(\"API 请求失败\");\n    return await res.json();\n}",
        MOCK_API_JS,
    )
    js = js.replace(
        "window.onload = () => { loadDashboardStats(); loadCharts(); loadTable(); loadTransport(); };",
        """
function bootLogisticsApp() {
    if (window.__logisticsAppBooted) return;
    window.__logisticsAppBooted = true;
    loadDashboardStats();
    loadCharts();
    loadTable();
    loadTransport();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootLogisticsApp, { once: true });
} else {
    bootLogisticsApp();
}
window.addEventListener('load', bootLogisticsApp, { once: true });
        """.strip(),
    )
    html = html.replace('<script src="/static/app.js?v=20260307_fix1"></script>', f"<script>{js}</script>")
    return html


st.set_page_config(page_title="物流监控系统", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    """
    <style>
    .block-container {padding: 0; max-width: 100%;}
    header, footer, [data-testid="stSidebar"] {display: none;}
    iframe {display: block;}
    </style>
    """,
    unsafe_allow_html=True,
)

components.html(build_page(), height=920, scrolling=False)
