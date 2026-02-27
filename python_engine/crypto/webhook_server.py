"""
FastAPI 서버 — Dashboard + n8n Webhook 수신 + 텔레그램 알림
──────────────────────────────────────────────────────────
n8n에서 센티먼트 점수를 Webhook으로 전송하면 이 서버가 수신하여
SignalEnsemble에 반영한다. Dashboard는 WebSocket으로 실시간 상태 전송.
"""
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import DASHBOARD_PORT
from scalper_engine import CryptoScalperEngine
from utils import setup_logger

log = setup_logger("server")

app = FastAPI(title="Project Chronos — Crypto V2 Dashboard")
engine = CryptoScalperEngine()
active_ws: list[WebSocket] = []


# ── Pydantic 모델 ───────────────────────────────────
class SentimentPayload(BaseModel):
    score: float  # -1.0 ~ +1.0
    source: str = "n8n"
    detail: str = ""


class EmergencyPayload(BaseModel):
    action: str  # "stop" | "resume"


# ── Webhook 엔드포인트 ──────────────────────────────
@app.post("/webhook/sentiment")
async def receive_sentiment(payload: SentimentPayload):
    """n8n에서 센티먼트 점수 수신"""
    await engine.ensemble.update_sentiment(payload.score)
    log.info("센티먼트 수신: %.2f (%s) %s", payload.score, payload.source, payload.detail)
    return {"status": "ok", "score": payload.score}


@app.post("/webhook/emergency")
async def emergency_control(payload: EmergencyPayload):
    """비상 정지/재개"""
    if payload.action == "stop":
        engine._running = False
        # 전 포지션 시장가 청산
        for symbol, pos in list(engine.risk.positions.items()):
            price = engine.microstructure.get_last_price(symbol)
            await engine.gateway.place_order(
                symbol=symbol, side="ask", quantity=pos.quantity, order_type="market"
            )
            await engine.risk.close_position(symbol, price)
        log.warning("비상 정지 — 전 포지션 청산 완료")
        return {"status": "stopped", "positions_closed": True}
    elif payload.action == "resume":
        engine._running = True
        log.info("매매 재개")
        return {"status": "resumed"}
    return {"status": "unknown_action"}


# ── REST API ────────────────────────────────────────
@app.get("/api/status")
async def get_status():
    """엔진 상태 조회"""
    return engine.get_status()


# ── WebSocket (실시간 Dashboard) ────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_ws.append(ws)
    try:
        while True:
            # 1초마다 상태 전송
            status = engine.get_status()
            await ws.send_json(status)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        active_ws.remove(ws)
    except Exception:
        if ws in active_ws:
            active_ws.remove(ws)


# ── Dashboard HTML ──────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chronos Crypto V2</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#09090b;color:#fafafa;font-family:'SF Mono',monospace;padding:16px}
h1{font-size:18px;color:#3b82f6;margin-bottom:12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.card{background:#18181b;border:1px solid #27272a;border-radius:8px;padding:14px}
.card h2{font-size:13px;color:#a1a1aa;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}
.val{font-size:22px;font-weight:700}
.green{color:#10b981}.red{color:#ef4444}.blue{color:#3b82f6}.yellow{color:#eab308}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:#71717a;padding:6px 8px;border-bottom:1px solid #27272a}
td{padding:6px 8px;border-bottom:1px solid #1a1a1e}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-bull{background:#064e3b;color:#10b981}
.badge-bear{background:#450a0a;color:#ef4444}
.badge-side{background:#1c1917;color:#a8a29e}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.status-on{background:#10b981}.status-off{background:#ef4444}
#log{background:#0a0a0a;border:1px solid #27272a;border-radius:8px;padding:10px;
     height:200px;overflow-y:auto;font-size:11px;color:#a1a1aa;margin-top:12px}
</style>
</head>
<body>
<h1><span class="status-dot status-off" id="statusDot"></span>Project Chronos — Crypto V2</h1>

<div class="grid">
  <div class="card">
    <h2>Regime</h2>
    <div class="val" id="regime">-</div>
  </div>
  <div class="card">
    <h2>Realized Vol</h2>
    <div class="val" id="rv">-</div>
  </div>
  <div class="card">
    <h2>Daily P&L</h2>
    <div class="val" id="pnl">-</div>
  </div>
  <div class="card">
    <h2>Win Rate / Kelly</h2>
    <div class="val" id="stats">-</div>
  </div>
</div>

<div class="card" style="margin-bottom:12px">
  <h2>Positions</h2>
  <table>
    <thead><tr><th>Symbol</th><th>Entry</th><th>Current</th><th>Qty</th><th>P&L</th><th>Trailing</th></tr></thead>
    <tbody id="positions"><tr><td colspan="6" style="color:#52525b">No positions</td></tr></tbody>
  </table>
</div>

<div class="card">
  <h2>Surveillance</h2>
  <table>
    <thead><tr><th>Symbol</th><th>Price</th><th>OBI</th><th>OFI</th><th>VPIN</th></tr></thead>
    <tbody id="surveillance"><tr><td colspan="5" style="color:#52525b">Waiting...</td></tr></tbody>
  </table>
</div>

<div id="log"></div>

<script>
const ws = new WebSocket(`ws://${location.host}/ws`);
const dot = document.getElementById('statusDot');
const logEl = document.getElementById('log');

function fmt(n, d=0){ return n ? Number(n).toLocaleString('ko-KR',{minimumFractionDigits:d,maximumFractionDigits:d}) : '-'; }

ws.onopen = () => { dot.className = 'status-dot status-on'; addLog('WebSocket connected'); };
ws.onclose = () => { dot.className = 'status-dot status-off'; addLog('WebSocket disconnected'); };

ws.onmessage = (e) => {
  const d = JSON.parse(e.data);

  // Regime
  const regimeEl = document.getElementById('regime');
  const rn = d.regime || 'UNKNOWN';
  regimeEl.textContent = rn;
  regimeEl.className = 'val ' + (rn==='BULLISH'?'green':rn==='BEARISH'?'red':'yellow');

  // RV
  document.getElementById('rv').textContent = (d.realized_vol*100).toFixed(2) + '%';

  // Stats
  const s = d.risk_stats || {};
  document.getElementById('pnl').innerHTML =
    `<span class="${s.daily_pnl>=0?'green':'red'}">${fmt(s.daily_pnl)}&#8361;</span>`;
  document.getElementById('stats').textContent =
    `${(s.win_rate*100).toFixed(0)}% / Kelly ${(s.kelly_fraction*100).toFixed(1)}%`;

  // Positions
  const pEl = document.getElementById('positions');
  if(d.positions && d.positions.length){
    pEl.innerHTML = d.positions.map(p => `<tr>
      <td><b>${p.symbol}</b></td>
      <td>${fmt(p.entry_price)}</td>
      <td>${fmt(p.current_price)}</td>
      <td>${p.quantity.toFixed(6)}</td>
      <td class="${p.pnl_pct>=0?'green':'red'}">${(p.pnl_pct*100).toFixed(2)}%</td>
      <td>${p.trailing_active?'ON':'OFF'}</td>
    </tr>`).join('');
  } else {
    pEl.innerHTML = '<tr><td colspan="6" style="color:#52525b">No positions</td></tr>';
  }

  // Surveillance
  const sEl = document.getElementById('surveillance');
  if(d.surveillance && d.surveillance.length){
    sEl.innerHTML = d.surveillance.map(s => `<tr>
      <td>${s.symbol}</td>
      <td>${fmt(s.price)}</td>
      <td class="${s.obi>0.3?'green':s.obi<-0.3?'red':''}">${(s.obi*100).toFixed(1)}%</td>
      <td>${fmt(s.ofi,0)}</td>
      <td class="${s.vpin>0.8?'red':''}">${(s.vpin*100).toFixed(1)}%</td>
    </tr>`).join('');
  }
};

function addLog(msg){
  const t = new Date().toLocaleTimeString('ko-KR');
  logEl.innerHTML += `<div>[${t}] ${msg}</div>`;
  logEl.scrollTop = logEl.scrollHeight;
}
</script>
</body>
</html>"""


# ── 앱 시작 ─────────────────────────────────────────
@app.on_event("startup")
async def startup():
    asyncio.create_task(engine.run())
    log.info("Chronos Crypto V2 서버 시작 — port %d", DASHBOARD_PORT)


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=DASHBOARD_PORT)


if __name__ == "__main__":
    main()
