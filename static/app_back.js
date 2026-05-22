let currentPage = 1;
const limit = 5;
let currentSearch = "";
let modalMode = 'add';
let logisticsMap = null;
let routeLayer = null;
let markerLayer = null;
let allVehicles = []; // 缓存车辆列表供表单使用

async function fetchAPI(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error("API 请求失败");
    return await res.json();
}

// ==================== 路由与菜单控制 ====================
document.querySelectorAll('.sidebar-menu a').forEach(link => {
    link.addEventListener('click', function(e) {
        const hasSubmenu = this.nextElementSibling && this.nextElementSibling.classList.contains('submenu');
        const currentMenuItem = this.closest('.menu-item');

        if (hasSubmenu) { currentMenuItem.classList.toggle('active'); return; }

        document.querySelectorAll('.menu-item').forEach(mi => {
            if (mi !== currentMenuItem && mi !== this.closest('.submenu')?.closest('.menu-item')) mi.classList.remove('active');
        });
        currentMenuItem.classList.add('active');
        document.querySelectorAll('.page-section').forEach(sec => sec.classList.remove('active'));
        
        const text = this.innerText.trim();
        const parentUl = this.closest('.submenu');
        document.querySelector('.breadcrumb').innerText = parentUl ? `首页 > ${parentUl.previousElementSibling.innerText.trim()} > ${text}` : `首页 > ${text}`;

        if(text === '数据仪表盘') {
            document.getElementById('page-dashboard').classList.add('active');
            loadDashboardStats(); window.dispatchEvent(new Event('resize')); 
        } else if(text === '传感器列表' || text === '车载终端') {
            document.getElementById('page-devices').classList.add('active');
            loadTable();
        } else if(text === '运输监控') {
            document.getElementById('page-transport').classList.add('active');
            loadTransport();
            // 延迟重绘地图，防止容器不可见时加载导致的地图错位
            setTimeout(() => { if(logisticsMap) logisticsMap.invalidateSize(); }, 200);
        } else if(text === '告警管理') {
            document.getElementById('page-alerts').classList.add('active');
            loadAlerts();
        }
    });
});

// ==================== 业务逻辑 ====================

async function loadDashboardStats() {
    try {
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
        const data = await fetchAPI(`/api/devices?page=${currentPage}&limit=${limit}&search=${currentSearch}`);
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

// ---------------- 新增：成熟的地图路径规划 ----------------
async function loadTransport() {
    try {
        const data = await fetchAPI(`/api/vehicles`);
        allVehicles = data.items; // 存入全局供弹窗使用
        const container = document.getElementById('vehicleListContainer');
        container.innerHTML = '';
        
        // 初始化地图
        if (!logisticsMap) {
            logisticsMap = L.map('logisticsMap').setView([35.86166, 104.195397], 4); // 视角定在中国中心
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
            // 点击车辆，在地图上画线
            div.onclick = () => {
                document.querySelectorAll('.vehicle-item').forEach(el => el.classList.remove('active'));
                div.classList.add('active');
                // 这里多传了一个 v.location
                drawRouteOnMap(v.id, v.plate_no, v.location);
            };
            container.appendChild(div);

            // 默认选中第一辆车
            if(index === 0) div.click();
        });
    } catch (e) { console.error(e); }
}

// 在最顶部定义全局变量（如果已经有 routeLayer 只需要加上下面这三个）
let startMarker = null;
let endMarker = null;
let truckMarker = null;

// 注意参数里加了 currentLocationName
async function drawRouteOnMap(vehicleId, plateNo, currentLocationName) {
    try {
        const data = await fetchAPI(`/api/vehicles/${vehicleId}/route`);
        
        // 1. 清理旧路线和标记
        if(routeLayer) logisticsMap.removeLayer(routeLayer);
        if(startMarker) logisticsMap.removeLayer(startMarker);
        if(endMarker) logisticsMap.removeLayer(endMarker);
        if(truckMarker) logisticsMap.removeLayer(truckMarker);

        // 2. 绘制规划路径
        routeLayer = L.polyline(data.route, {color: '#4096ff', weight: 4, dashArray: '10, 10'}).addTo(logisticsMap);
        
        // 3. 标注起点与终点
        startMarker = L.marker(data.start_loc).addTo(logisticsMap).bindPopup(`<b style="color:#43b581;">起点 / 发货地</b>`);
        endMarker = L.marker(data.end_loc).addTo(logisticsMap).bindPopup(`<b style="color:#ff4d4f;">终点 / 目的地</b>`);

        // 4. 重点在这里！把传过来的正确地名展示在地图气泡里
        truckMarker = L.marker(data.current_loc)
            .addTo(logisticsMap)
            .bindPopup(`<b><i class="fa-solid fa-truck"></i> ${plateNo}</b><br>当前位置: ${currentLocationName}`)
            .openPopup();
        
        // 5. 缩放地图
        logisticsMap.fitBounds(routeLayer.getBounds(), {padding: [50, 50]});
    } catch(e) { console.error("加载路线规划失败", e); }
}
// ----------------------------------------------------

async function loadAlerts() {
    try {
        const data = await fetchAPI(`/api/alerts`);
        const tbody = document.querySelector('#alertsTable tbody');
        tbody.innerHTML = '';
        data.items.forEach(a => {
            const isUn = a.status === 'unprocessed';
            const sClass = isUn ? 'status-error' : 'status-closed';
            const sText = isUn ? '未处理' : '已关闭';
            const actionBtn = isUn ? `<button onclick="resolveAlert(${a.id})" style="color:#43b581; font-weight:bold;"><i class="fa-solid fa-check"></i> 处理并关闭</button>` : `<span style="color:#999;">无</span>`;
            tbody.innerHTML += `<tr><td>#${a.id}</td><td>${a.device_sn}</td><td>${a.alert_type}</td><td>${a.created_at}</td><td><span class="status-tag ${sClass}">${sText}</span></td><td class="operate-btn">${actionBtn}</td></tr>`;
        });
    } catch (e) { console.error(e); }
}

async function resolveAlert(id) {
    if(!confirm("确认要处理并关闭该告警吗？")) return;
    await fetchAPI(`/api/alerts/${id}/resolve`, { method: 'PUT' });
    loadAlerts(); loadDashboardStats();
}

function doSearch() { currentSearch = document.getElementById('searchInput').value; currentPage = 1; loadTable(); }
async function deleteDevice(id) { if(confirm("确定删除？")) { await fetchAPI(`/api/devices/${id}`, { method: 'DELETE' }); loadTable(); loadDashboardStats(); } }

async function openModal(mode, data = null) {
    modalMode = mode; 
    document.getElementById('deviceModal').classList.add('active');
    
    // 动态拉取并填充车辆下拉框
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
        
        // 匹配车辆下拉框
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

// 初始加载
window.onload = () => { loadDashboardStats(); loadCharts(); loadTable(); loadTransport(); };