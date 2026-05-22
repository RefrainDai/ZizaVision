let currentPage = 1;
const limit = 5;
let currentSearch = "";
let currentCategory = ""; // 记录当前在看哪个分类
let modalMode = 'add';
let logisticsMap = null;
let routeLayer = null;

// 注意：这三个全局地图标记，全文件只能存在这一次！
let startMarker = null;
let endMarker = null;
let truckMarker = null;
//新加三个全局变量，分别用于智能调度模块的地图显示和数据缓存
let dispatchMap = null;
let dispatchRouteLayer = null;
let dispatchMarkers = [];
let dynamicWarehouses = [
    { id: 1, name: '贵州长顺总仓', lat: 26.03, lng: 106.45, stock: 2500, vehicles: 15 },
    { id: 2, name: '上海青浦前置仓', lat: 31.1505, lng: 121.1243, stock: 200, vehicles: 2 },
    { id: 3, name: '成都双流冷链仓', lat: 30.5744, lng: 103.9237, stock: 900, vehicles: 6 }
];
let targetDest = null;
let matchedBestWh = null;
let allVehicles = []; // 缓存车辆列表供表单使用
//告警处理
let allAlertsData = [];
let currentAlertFilter = 'all'; // 当前的筛选状态
let targetDemand = 0;   // 【新增】保存订单需求量
async function fetchAPI(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error("API 请求失败");
    return await res.json();
}
// ==================== 智能调度模块 (大屏可视化版) ====================
// ==================== 动态交互调度模块 (实时解析版) ====================

// 通用：地址转经纬度 (使用 OpenStreetMap 免费开源接口)
async function geocodeAddress(address) {
    try {
        const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(address)}`);
        const data = await res.json();
        if(data && data.length > 0) return { lat: parseFloat(data[0].lat), lng: parseFloat(data[0].lon), name: address };
    } catch(e) { console.error("Geocode error", e); }
    return null;
}

// 刷新地图上的静态仓库标记
function refreshMapMarkers() {
    if(!dispatchMap) return;
    dispatchMarkers.forEach(m => dispatchMap.removeLayer(m));
    dispatchMarkers = [];
    
    // 标记仓库
    dynamicWarehouses.forEach(w => {
        const m = L.marker([w.lat, w.lng]).addTo(dispatchMap).bindPopup(`<b><i class="fa-solid fa-warehouse"></i> ${w.name}</b><br>库存: ${w.stock} | 运力: ${w.vehicles}`);
        dispatchMarkers.push(m);
    });
    
    // 标记目的地
    if(targetDest) {
        const m = L.marker([targetDest.lat, targetDest.lng]).addTo(dispatchMap).bindPopup(`<b style="color:#ff4d4f;"><i class="fa-solid fa-flag-checkered"></i> 目的地: ${targetDest.name}</b>`).openPopup();
        dispatchMarkers.push(m);
    }
}

// 渲染左侧仓库列表
function renderWarehouses() {
    const list = document.getElementById('wh-list');
    list.innerHTML = dynamicWarehouses.map(w => `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 10px; background:#f0f4f8; margin-bottom:5px; border-radius:4px; font-size:0.85rem; border-left: 3px solid #4096ff;">
            <div><b>${w.name}</b> <span style="color:#666; margin-left:10px;">📦 ${w.stock} | 🚛 ${w.vehicles}</span></div>
            <button onclick="removeWh(${w.id})" style="color:#ff4d4f; border:none; background:none; cursor:pointer;"><i class="fa-solid fa-trash"></i></button>
        </div>
    `).join('');
    refreshMapMarkers();
}

// 添加自定义仓库
async function addWarehouse() {
    const name = document.getElementById('newWhName').value;
    const city = document.getElementById('newWhCity').value;
    const stock = parseInt(document.getElementById('newWhStock').value) || 0;
    const veh = parseInt(document.getElementById('newWhVeh').value) || 0;
    
    if(!name || !city) return alert("仓库名和所在城市必须填写！");
    
    const btn = event.target;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    
    const coords = await geocodeAddress(city);
    btn.innerHTML = '<i class="fa-solid fa-plus"></i>';
    
    if(!coords) return alert("地图引擎无法解析该城市/地址，请换个更精确的地名试试。");
    
    dynamicWarehouses.push({ id: Date.now(), name: name, lat: coords.lat, lng: coords.lng, stock: stock, vehicles: veh });
    document.getElementById('newWhName').value = ''; document.getElementById('newWhCity').value = '';
    renderWarehouses();
}
function removeWh(id) { dynamicWarehouses = dynamicWarehouses.filter(w => w.id !== id); renderWarehouses(); }

// 设定客户订单目的地与需求
async function setDestination() {
    const addr = document.getElementById('targetAddress').value;
    const demandVal = parseInt(document.getElementById('orderDemand').value); // 【新增】获取需求量
    
    if(!addr) return alert("请输入目的地地址");
    if(isNaN(demandVal) || demandVal <= 0) return alert("请输入有效的订单需求量（大于0的整数）");
    
    const btn = event.target;
    const oldHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 正在解析...';
    
    const coords = await geocodeAddress(addr);
    btn.innerHTML = oldHtml;
    
    if(!coords) return alert("地址解析失败，请检查输入是否准确。");
    
    targetDest = coords;
    targetDemand = demandVal; // 【新增】保存需求量供后续使用
    
    document.getElementById('target-info').innerHTML = `<span style="color:#43b581;"><i class="fa-solid fa-check-circle"></i> 设定成功！目标坐标: ${coords.lat.toFixed(4)}, ${coords.lng.toFixed(4)} | 需求量: <b>${targetDemand}</b> 件</span>`;
    
    refreshMapMarkers();
    dispatchMap.flyTo([coords.lat, coords.lng], 10);
}

// 执行动态匹配仓库 (调用 Python)
// 执行动态匹配仓库 (调用 Python)
async function runDynamicMatch() {
    if(!targetDest || !targetDemand) return alert("请先在上方设置客户订单目的地与需求量！");
    if(dynamicWarehouses.length === 0) return alert("仓库列表为空！");
    
    document.getElementById('dynamic-results').innerHTML = '<div style="padding:20px; text-align:center; color:#4096ff;"><i class="fa-solid fa-spinner fa-spin fa-2x"></i><br>后端大脑正在进行多维加权计算...</div>';
    
    // 【新增】在 payload 中带上 demand 参数
    const payload = { 
        target_lat: targetDest.lat, 
        target_lng: targetDest.lng, 
        demand: targetDemand,
        warehouses: dynamicWarehouses 
    };
    
    try {
        const res = await fetchAPI('/api/dynamic_match', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
        
        // 【新增】拦截库存不足的异常情况
        if(res.code === 400) {
            document.getElementById('dynamic-results').innerHTML = `
                <div style="padding:20px; text-align:center; color:#ff4d4f; border: 1px solid #ffccc7; background: #fff2f0; border-radius: 6px;">
                    <i class="fa-solid fa-triangle-exclamation fa-2x" style="margin-bottom:10px;"></i><br>
                    <b>匹配失败</b><br>${res.message}
                </div>`;
            matchedBestWh = null;
            return;
        }

        matchedBestWh = res.data[0]; // 得分最高的仓库
        
        let html = '<div style="font-size:0.85rem; color:#666; margin-bottom:10px;">已过滤库存不足仓库。余下结果按照[距离40%][库存40%][运力20%]权重排名：</div>';
        res.data.forEach((w, idx) => {
            const isBest = idx === 0;
            html += `
                <div style="border:1px solid ${isBest ? '#43b581' : '#eee'}; background:${isBest ? '#f6ffed' : '#fff'}; padding:10px; border-radius:6px; margin-bottom:10px; position:relative;">
                    ${isBest ? '<div style="position:absolute; right:10px; top:10px; color:#43b581; font-weight:bold;"><i class="fa-solid fa-crown"></i> 最优发货仓</div>' : ''}
                    <div style="font-weight:bold; font-size:1rem; color:#333; margin-bottom:5px;">${idx+1}. ${w.name}</div>
                    <div style="color:#666; font-size:0.85rem;">综合得分: <b style="font-size:1.2rem; color:${isBest ? '#43b581' : '#333'}">${w.score}</b> 分</div>
                    <div style="color:#999; font-size:0.8rem; margin-top:5px;">距离: ${w.distance_km} km | 剩余库存: ${w.details.inv}分 | 运力: ${w.details.veh}分</div>
                </div>
            `;
        });
        document.getElementById('dynamic-results').innerHTML = html;
        
        // 地图飞向最优仓
        dispatchMap.flyTo([matchedBestWh.lat, matchedBestWh.lng], 8);
    } catch(e) { alert("调用后端匹配接口失败，请检查 Python 控制台报错。"); }
}

// 动态规划真实路网 (调用 OSRM)
async function runDynamicRoute() {
    if(!matchedBestWh || !targetDest) return alert("请先完成 [定位目的地] 和 [智能选仓] 两步！");
    
    document.getElementById('dynamic-results').innerHTML = '<div style="padding:20px; text-align:center; color:#fbad14;"><i class="fa-solid fa-route fa-beat fa-2x"></i><br>正在调用云端路网 API 规划真实轨迹...</div>';
    
    if(dispatchRouteLayer) dispatchMap.removeLayer(dispatchRouteLayer);
    
    try {
        // 请求 OSRM 真实路网引擎
        const osrmUrl = `https://router.project-osrm.org/route/v1/driving/${matchedBestWh.lng},${matchedBestWh.lat};${targetDest.lng},${targetDest.lat}?overview=full&geometries=geojson`;
        const osrmRes = await fetch(osrmUrl);
        const osrmData = await osrmRes.json();
        
        if (osrmData.code === 'Ok') {
            const route = osrmData.routes[0];
            const routeCoords = route.geometry.coordinates.map(c => [c[1], c[0]]);
            
            // 地图画线
            dispatchRouteLayer = L.polyline(routeCoords, { color: '#fbad14', weight: 6, opacity: 0.8 }).addTo(dispatchMap);
            dispatchMap.fitBounds(dispatchRouteLayer.getBounds(), {padding: [50, 50]});
            
            // 结果展示
            const distKm = (route.distance / 1000).toFixed(1);
            const timeMin = Math.ceil(route.duration / 60);
            
            document.getElementById('dynamic-results').innerHTML = `
                <div style="text-align:center; padding:15px; background:#fffbf6; border:1px solid #ffe58f; border-radius:6px;">
                    <i class="fa-solid fa-truck-fast fa-3x" style="color:#fbad14; margin-bottom:10px;"></i>
                    <h3 style="color:#d48806;">最优运输路线生成完毕</h3>
                    <div style="margin-top:15px; font-size:0.95rem; color:#666; text-align:left;">
                        <p style="margin-bottom:8px;"><b><i class="fa-solid fa-circle-dot" style="color:#4096ff;"></i> 起点：</b>${matchedBestWh.name}</p>
                        <p style="margin-bottom:8px;"><b><i class="fa-solid fa-location-dot" style="color:#ff4d4f;"></i> 终点：</b>${targetDest.name}</p>
                        <hr style="border:none; border-top:1px dashed #ddd; margin:10px 0;">
                        <p style="margin-bottom:8px;"><b>📏 预计里程：</b>${distKm} 公里</p>
                        <p><b>⏱️ 预计耗时：</b>${timeMin} 分钟</p>
                    </div>
                </div>
            `;
        }
    } catch(e) { alert("获取真实路网失败！可能是网络原因拦截了海外 API。"); }
}
// ==================== 路由与菜单控制 ====================
document.querySelectorAll('.sidebar-menu a').forEach(link => {
    link.addEventListener('click', function(e) {
        const hasSubmenu = this.nextElementSibling && this.nextElementSibling.classList.contains('submenu');
        const currentMenuItem = this.closest('.menu-item');

        if (hasSubmenu) { currentMenuItem.classList.toggle('active'); return; }

        document.querySelectorAll('.menu-item').forEach(mi => {
            let parentMenu = null;
            const parentSubmenu = this.closest('.submenu');
            if (parentSubmenu) {
                parentMenu = parentSubmenu.closest('.menu-item');
            }
            if (mi !== currentMenuItem && mi !== parentMenu) mi.classList.remove('active');
        });
        currentMenuItem.classList.add('active');
        document.querySelectorAll('.page-section').forEach(sec => sec.classList.remove('active'));
        
        const text = this.innerText.trim();
        const parentUl = this.closest('.submenu');
        document.querySelector('.breadcrumb').innerText = parentUl ? `首页 > ${parentUl.previousElementSibling.innerText.trim()} > ${text}` : `首页 > ${text}`;

        if(text === '数据仪表盘') {
            document.getElementById('page-dashboard').classList.add('active');
            loadDashboardStats(); window.dispatchEvent(new Event('resize')); 
        } else if(text === '传感器列表') {
            document.getElementById('page-devices').classList.add('active');
            currentCategory = "传感器"; // 设置为传感器分类
            currentPage = 1;           
            currentSearch = "";        
            document.getElementById('searchInput').value = "";
            loadTable();
        } else if(text === '车载终端') {
            document.getElementById('page-devices').classList.add('active');
            currentCategory = "车载终端"; // 设置为终端分类
            currentPage = 1; 
            currentSearch = "";
            document.getElementById('searchInput').value = "";
            loadTable();
        } else if(text === '运输监控') {
            document.getElementById('page-transport').classList.add('active');
            loadTransport();
            setTimeout(() => { if(logisticsMap) logisticsMap.invalidateSize(); }, 200);
        } else if(text === '告警管理') {
            document.getElementById('page-alerts').classList.add('active');
            loadAlerts();
        } else if(text === '仓储与运力') {
        document.getElementById('page-warehouse').classList.add('active');
        renderWarehousePage(); // 点击时自动渲染最新数据
        } else if(text === '智能调度') {
            document.getElementById('page-dispatch').classList.add('active');
            // 初始化调度专用地图
            if (!dispatchMap) {
                dispatchMap = L.map('dispatchMap').setView([32.0603, 118.7969], 6);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(dispatchMap);
            }
            setTimeout(() => { 
                if(dispatchMap) dispatchMap.invalidateSize(); 
                renderWarehouses(); // 刷新本地仓池
            }, 200);
        }
    });
});

// ==================== 业务逻辑 ====================

async function loadDashboardStats() {
    try {
        // 【已修复】：这里恢复为请求 stats 接口
        const data = await fetchAPI('/api/dashboard/stats');
        document.getElementById('stat-online').innerText = data.online_devices;
        document.getElementById('stat-transit').innerText = data.in_transit_vehicles;
        document.getElementById('stat-alerts').innerText = data.unhandled_alerts;
        document.getElementById('stat-temp').innerText = data.temp_abnormal;
    } catch (e) { console.error(e); }
}

async function loadCharts() {
    try {
        const data = await fetchAPI('/api/dashboard/charts');
        var lineChart = echarts.init(document.getElementById('lineChart'));
        lineChart.setOption({ tooltip: { trigger: 'axis' }, grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true }, xAxis: { type: 'category', data: data.line.times }, yAxis: { type: 'value', max: 100, min: 80, axisLabel: { formatter: '{value}%' } }, series: [{ name: '在线率', type: 'line', data: data.line.values, smooth: true, itemStyle: { color: '#4096ff' }, lineStyle: { color: '#4096ff', width: 2 }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(64, 150, 255, 0.3)' }, { offset: 1, color: 'rgba(64, 150, 255, 0.0)' }]) } }] });
        var pieChart = echarts.init(document.getElementById('pieChart'));
        pieChart.setOption({ tooltip: { trigger: 'item' }, legend: { top: '0%', left: 'center' }, series: [{ name: '告警类型', type: 'pie', radius: ['40%', '70%'], data: data.pie, color: ['#ff4d4f', '#fbad14', '#43b581', '#4096ff', '#9254de'], itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 }, label: { show: false } }] });
        window.addEventListener('resize', () => { lineChart.resize(); pieChart.resize(); });
    } catch (e) { console.error(e); }
}

async function loadTable() {
    try {
        // 【已修复】：这里的请求地址加上了 &category=${currentCategory}
        const data = await fetchAPI(`/api/devices?page=${currentPage}&limit=${limit}&search=${currentSearch}&category=${currentCategory}`);
        
        const tbody = document.querySelector('#deviceTable tbody');
        tbody.innerHTML = '';
        data.items.forEach(item => {
            let sClass = item.status === 'online' ? 'status-normal' : (item.status === 'error' ? 'status-error' : 'status-warning');
            let sText = item.status === 'online' ? '正常' : (item.status === 'error' ? '异常' : '离线');
            const safeItemStr = JSON.stringify(item).replace(/"/g, '&quot;');
            tbody.innerHTML += `<tr><td>${item.sn}</td><td>${item.type}</td><td>${item.vehicle}</td><td>${item.install_pos}</td><td>${item.last_data}</td><td><span class="status-tag ${sClass}">${sText}</span></td><td>${item.last_active}</td><td class="operate-btn"><button onclick="openModal('edit', ${safeItemStr})"><i class="fa-solid fa-edit"></i> 编辑</button><button class="danger" onclick="deleteDevice(${item.id})"><i class="fa-solid fa-trash"></i> 删除</button></td></tr>`;
        });
        const pagination = document.getElementById('devicePagination');
        pagination.innerHTML = '';
        const totalPages = Math.ceil(data.total / limit) || 1;
        for(let i=1; i<=totalPages; i++) {
            const btn = document.createElement('button'); btn.innerText = i;
            if(i === currentPage) btn.className = 'active';
            btn.onclick = () => { currentPage = i; loadTable(); };
            pagination.appendChild(btn);
        }
    } catch (e) { console.error(e); }
}

async function loadTransport() {
    try {
        const data = await fetchAPI(`/api/vehicles`);
        allVehicles = data.items;
        const container = document.getElementById('vehicleListContainer');
        container.innerHTML = '';
        
        if (!logisticsMap) {
            logisticsMap = L.map('logisticsMap').setView([35.86166, 104.195397], 4); 
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap contributors'
            }).addTo(logisticsMap);
        }

        data.items.forEach((v, index) => {
            const div = document.createElement('div');
            div.className = 'vehicle-item';
            div.innerHTML = `
                <h4><span><i class="fa-solid fa-truck" style="color:#4096ff;"></i> ${v.plate_no}</span> <span class="status-tag status-normal">在途</span></h4>
                <p><i class="fa-solid fa-user"></i> 司机: ${v.driver} (${v.phone})</p>
                <p><i class="fa-solid fa-location-dot"></i> 当前: ${v.location}</p>
            `;
            div.onclick = () => {
                document.querySelectorAll('.vehicle-item').forEach(el => el.classList.remove('active'));
                div.classList.add('active');
                drawRouteOnMap(v.id, v.plate_no, v.location);
            };
            container.appendChild(div);

            if(index === 0) div.click();
        });
    } catch (e) { console.error(e); }
}

async function drawRouteOnMap(vehicleId, plateNo, currentLocationName) {
    try {
        const data = await fetchAPI(`/api/vehicles/${vehicleId}/route`);
        
        if(routeLayer) logisticsMap.removeLayer(routeLayer);
        if(startMarker) logisticsMap.removeLayer(startMarker);
        if(endMarker) logisticsMap.removeLayer(endMarker);
        if(truckMarker) logisticsMap.removeLayer(truckMarker);

        const coordsString = data.route.map(p => `${p[1]},${p[0]}`).join(';');
        const osrmUrl = `https://router.project-osrm.org/route/v1/driving/${coordsString}?overview=full&geometries=geojson`;
        
        let finalRouteCoords = data.route; 
        
        try {
            const osrmRes = await fetch(osrmUrl);
            const osrmData = await osrmRes.json();
            if (osrmData.code === 'Ok') {
                finalRouteCoords = osrmData.routes[0].geometry.coordinates.map(c => [c[1], c[0]]);
            }
        } catch (routeErr) {
            console.warn("真实路网请求失败，降级为直线连线", routeErr);
        }

        routeLayer = L.polyline(finalRouteCoords, {
            color: '#4096ff', 
            weight: 5, 
            opacity: 0.8,
            dashArray: '10, 10'
        }).addTo(logisticsMap);
        
        startMarker = L.marker(data.start_loc).addTo(logisticsMap).bindPopup(`<b style="color:#43b581;">起点 / 发货地</b>`);
        endMarker = L.marker(data.end_loc).addTo(logisticsMap).bindPopup(`<b style="color:#ff4d4f;">终点 / 目的地</b>`);

        truckMarker = L.marker(data.current_loc)
            .addTo(logisticsMap)
            .bindPopup(`<b><i class="fa-solid fa-truck"></i> ${plateNo}</b><br>当前位置: ${currentLocationName}`)
            .openPopup();
        
        logisticsMap.fitBounds(routeLayer.getBounds(), {padding: [50, 50]});
    } catch(e) { 
        console.error("加载路线规划失败", e); 
    }
}

// 从后端拉取告警数据
async function loadAlerts() {
    try {
        const data = await fetchAPI(`/api/alerts`);
        allAlertsData = data.items;
        
        // 渲染顶部统计卡片
        const unCount = allAlertsData.filter(a => a.status === 'unprocessed').length;
        const clCount = allAlertsData.filter(a => a.status === 'closed').length;
        document.getElementById('alert-unprocessed-count').innerText = unCount;
        document.getElementById('alert-closed-count').innerText = clCount;

        // 执行表格渲染
        renderAlerts(currentAlertFilter);
    } catch (e) { console.error(e); }
}

// 点击筛选按钮触发的函数
function filterAlerts(status) {
    currentAlertFilter = status;
    
    // 重置所有按钮样式
    const defaultStyle = "border:1px solid #ddd; background:#fff; color:#666;";
    const activeStyle = "border:1px solid #4096ff; background:#e6f4ff; color:#4096ff;";
    
    document.getElementById('btn-filter-all').style = defaultStyle;
    document.getElementById('btn-filter-un').style = defaultStyle;
    document.getElementById('btn-filter-cl').style = defaultStyle;
    
    // 高亮当前选中的按钮
    if(status === 'all') document.getElementById('btn-filter-all').style = activeStyle;
    if(status === 'unprocessed') document.getElementById('btn-filter-un').style = activeStyle;
    if(status === 'closed') document.getElementById('btn-filter-cl').style = activeStyle;
    
    renderAlerts(status);
}

// 将数据渲染到 HTML 表格中
function renderAlerts(filterStatus) {
    const tbody = document.querySelector('#alertsTable tbody');
    tbody.innerHTML = '';
    
    // 根据状态过滤数据
    const filteredData = filterStatus === 'all' ? allAlertsData : allAlertsData.filter(a => a.status === filterStatus);
    
    if(filteredData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:40px; color:#999;"><i class="fa-solid fa-face-smile-wink fa-2x" style="color:#43b581; margin-bottom:10px;"></i><br>太棒了！当前分类下暂无告警数据。</td></tr>`;
        return;
    }

    filteredData.forEach(a => {
        const isUn = a.status === 'unprocessed';
        const sClass = isUn ? 'status-error' : 'status-closed';
        const sText = isUn ? '🔴 待处理' : '🟢 已解决';
        
        // 匹配不同的精美图标
        let typeIcon = '<i class="fa-solid fa-triangle-exclamation" style="color:#fbad14;"></i>';
        if(a.alert_type.includes('温湿度')) typeIcon = '<i class="fa-solid fa-temperature-arrow-up" style="color:#ff4d4f;"></i>';
        else if(a.alert_type.includes('偏离')) typeIcon = '<i class="fa-solid fa-route" style="color:#fa8c16;"></i>';
        else if(a.alert_type.includes('离线')) typeIcon = '<i class="fa-solid fa-wifi" style="color:#999;"></i>';
        else if(a.alert_type.includes('震动')) typeIcon = '<i class="fa-solid fa-wave-square" style="color:#722ed1;"></i>';

        // 只有未处理的告警才显示“处理按钮”
        const actionBtn = isUn 
            ? `<button onclick="resolveAlert(${a.id})" class="btn-add" style="padding:6px 12px; font-size:0.85rem; background:#4096ff;"><i class="fa-solid fa-screwdriver-wrench"></i> 派单处理</button>` 
            : `<span style="color:#ccc; font-size:0.85rem;"><i class="fa-solid fa-check-double"></i> 已归档</span>`;
            
        tbody.innerHTML += `
            <tr style="transition: background 0.3s;" onmouseover="this.style.background='#f9f9f9'" onmouseout="this.style.background='transparent'">
                <td><b>#${a.id}</b></td>
                <td style="font-family:monospace; color:#4096ff; font-weight:bold;">${a.device_sn}</td>
                <td style="font-weight:500;">${typeIcon} ${a.alert_type}</td>
                <td style="color:#666; font-size:0.85rem;">${a.created_at}</td>
                <td><span class="status-tag ${sClass}">${sText}</span></td>
                <td class="operate-btn">${actionBtn}</td>
            </tr>
        `;
    });
}

// 处理告警工单
async function resolveAlert(id) {
    if(!confirm("确定已联系司机排查，并解决该异常告警吗？")) return;
    
    // 调用后端接口更新状态
    await fetchAPI(`/api/alerts/${id}/resolve`, { method: 'PUT' });
    
    // 重新加载数据并刷新界面
    loadAlerts(); 
    loadDashboardStats(); // 同步刷新首页仪表盘的数据
}

function doSearch() { currentSearch = document.getElementById('searchInput').value; currentPage = 1; loadTable(); }
async function deleteDevice(id) { if(confirm("确定删除？")) { await fetchAPI(`/api/devices/${id}`, { method: 'DELETE' }); loadTable(); loadDashboardStats(); } }

async function openModal(mode, data = null) {
    modalMode = mode; 
    document.getElementById('deviceModal').classList.add('active');
    
    if(allVehicles.length === 0) {
        const res = await fetchAPI(`/api/vehicles`);
        allVehicles = res.items;
    }
    const select = document.getElementById('formVehicle');
    select.innerHTML = '<option value="">-- 固定设备 (无车辆) --</option>';
    allVehicles.forEach(v => {
        select.innerHTML += `<option value="${v.id}">${v.plate_no} (${v.driver})</option>`;
    });

    if(mode === 'add') {
        document.getElementById('modalTitle').innerText = '添加设备';
        document.getElementById('deviceForm').reset(); document.getElementById('formSn').readOnly = false; document.getElementById('statusGroup').style.display = 'none';
    } else {
        document.getElementById('modalTitle').innerText = '编辑设备';
        document.getElementById('formId').value = data.id; document.getElementById('formSn').value = data.sn; document.getElementById('formSn').readOnly = true;
        document.getElementById('formType').value = data.type; document.getElementById('formPos').value = data.install_pos;
        document.getElementById('formStatus').value = data.status; document.getElementById('statusGroup').style.display = 'block';
        
        const matchedVehicle = allVehicles.find(v => v.plate_no === data.vehicle);
        if(matchedVehicle) select.value = matchedVehicle.id;
    }
}
function closeModal() { document.getElementById('deviceModal').classList.remove('active'); }

document.getElementById('deviceForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const payload = { 
        type: document.getElementById('formType').value, 
        install_pos: document.getElementById('formPos').value,
        vehicle_id: document.getElementById('formVehicle').value ? parseInt(document.getElementById('formVehicle').value) : null
    };
    try {
        if(modalMode === 'add') {
            payload.sn = document.getElementById('formSn').value;
            await fetchAPI('/api/devices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        } else {
            payload.status = document.getElementById('formStatus').value;
            await fetchAPI(`/api/devices/${document.getElementById('formId').value}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        }
        closeModal(); loadTable(); loadDashboardStats();
    } catch(err) { alert(err.message); }
});
// ==================== 仓储与运力大盘模块 ====================
function renderWarehousePage() {
    const overview = document.getElementById('warehouse-overview');
    const tbody = document.querySelector('#warehouseTable tbody');
    overview.innerHTML = '';
    tbody.innerHTML = '';
    
    // 我们直接读取系统中动态配置的仓库列表 dynamicWarehouses
    if (!dynamicWarehouses || dynamicWarehouses.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:40px; color:#999;">当前未配置任何仓库节点，请前往【智能调度】页面添加。</td></tr>';
        return;
    }

    dynamicWarehouses.forEach((w, index) => {
        // 假设每个仓库满仓是 2000 件，满编车队是 20 辆，计算健康度百分比
        const maxStock = 2000;
        const maxVehicles = 20;
        const stockPercent = Math.min(100, (w.stock / maxStock) * 100);
        const vehPercent = Math.min(100, (w.vehicles / maxVehicles) * 100);
        
        // 阈值判断：低于20%即标红警告
        const isStockLow = stockPercent < 20;
        const isVehLow = vehPercent < 20;
        const stockColor = isStockLow ? '#ff4d4f' : '#43b581';
        const vehColor = isVehLow ? '#ff4d4f' : '#4096ff';
        
        // 1. 渲染顶部仪表盘卡片
        overview.innerHTML += `
            <div class="stat-card" style="flex-direction: column; align-items: flex-start; gap: 15px; flex: 1; min-width: 250px; border-top: 4px solid ${isStockLow || isVehLow ? '#ff4d4f' : '#4096ff'}">
                <h4 style="margin:0; color:#333; font-size:1.05rem;"><i class="fa-solid fa-warehouse"></i> ${w.name}</h4>
                
                <div style="width: 100%; font-size: 0.85rem; color: #666;">
                    <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
                        <span><i class="fa-solid fa-box-open"></i> 库存: <b style="color:${stockColor}">${w.stock}</b> / ${maxStock}</span>
                        <span style="color:${stockColor}">${stockPercent.toFixed(1)}%</span>
                    </div>
                    <div class="progress-bar"><div class="progress-inner" style="width: ${stockPercent}%; background: ${stockColor};"></div></div>
                </div>
                
                <div style="width: 100%; font-size: 0.85rem; color: #666;">
                    <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
                        <span><i class="fa-solid fa-truck"></i> 运力: <b style="color:${vehColor}">${w.vehicles}</b> / ${maxVehicles}</span>
                        <span style="color:${vehColor}">${vehPercent.toFixed(1)}%</span>
                    </div>
                    <div class="progress-bar"><div class="progress-inner" style="width: ${vehPercent}%; background: ${vehColor};"></div></div>
                </div>
            </div>
        `;

        // 2. 渲染底部数据表格
        const statusHtml = (isStockLow || isVehLow) 
            ? '<span class="status-tag status-error"><i class="fa-solid fa-triangle-exclamation"></i> 资源告急</span>' 
            : '<span class="status-tag status-normal"><i class="fa-solid fa-check-circle"></i> 运行平稳</span>';
            
        // 给一个格式化的假ID
        const displayId = `WH-${String(w.id).slice(-4).padStart(4, '0')}`;

        tbody.innerHTML += `
            <tr style="transition: background 0.2s;" onmouseover="this.style.background='#f9f9f9'" onmouseout="this.style.background='transparent'">
                <td style="font-family: monospace; color: #999;">${displayId}</td>
                <td style="font-weight: 600; color: #333;">${w.name}</td>
                <td style="color: #666;">${w.lng.toFixed(4)}, ${w.lat.toFixed(4)}</td>
                <td style="font-weight: bold; color: ${stockColor};">${w.stock}</td>
                <td style="font-weight: bold; color: ${vehColor};">${w.vehicles}</td>
                <td>${statusHtml}</td>
            </tr>
        `;
    });
}
// 初始加载
window.onload = () => { loadDashboardStats(); loadCharts(); loadTable(); loadTransport(); };
