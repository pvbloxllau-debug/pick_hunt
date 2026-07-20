import sqlite3
import datetime
import os
import base64
import socket
from fastapi import FastAPI, Request, Form, Depends, Cookie, Response, HTTPException, File, UploadFile, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────────────────────────────────────
DB_FILE = os.environ.get("DB_FILE", "picker_hunt_99.db")

QUANTITY_OPTIONS_HTML = "".join(f'<option value="{i}">{i} unidades</option>' if i > 1 else '<option value="1">1 unidad</option>' for i in range(1, 101))

def get_local_ips():
    ips = []
    try:
        hostname = socket.gethostname()
        addresses = socket.getaddrinfo(hostname, None)
        for addr in addresses:
            ip = addr[4][0]
            if "." in ip and not ip.startswith("127."):
                if ip not in ips:
                    ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            ips.append("192.168.1.100")
    return list(set(ips))


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL, -- 'picker' or 'supervisor'
        avatar TEXT,
        points INTEGER DEFAULT 0,
        hunts_completed INTEGER DEFAULT 0
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hunts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL,
        barcode TEXT NOT NULL,
        aisle TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        reported_by TEXT NOT NULL,
        reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        assigned_to TEXT,
        status TEXT DEFAULT 'Buscando', -- 'Buscando', 'Yendo', 'Encontrado', 'Sin Stock'
        photo TEXT -- Base64 encoded image or null
    )
    """)
    
    # Run automatic migrations
    for _col, _def in [
        ("photo",            "TEXT"),
        ("resolution_note",  "TEXT"),
        ("inventory_delta",  "INTEGER"),
        ("resolved_at",      "TIMESTAMP"),
        ("found_location",   "TEXT"),  # 'sala' | 'bodega' | NULL
        ("protocolo_at",     "TIMESTAMP"),  # cuando el picker aplico protocolo
        ("location_photo",        "TEXT"),  # foto del hunter al encontrar en sala
        ("picker_retrieved_at",    "TIMESTAMP"),  # cuando el picker confirmo retiro
    ]:
        try:
            cursor.execute(f"ALTER TABLE hunts ADD COLUMN {_col} {_def}")
        except sqlite3.OperationalError:
            pass
        
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feed (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Seed default data if users table is empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
        INSERT INTO users (username, password, full_name, role, avatar, points, hunts_completed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            ("picker_juan", "123", "Juan Perez", "picker", "", 350, 14),
            ("picker_camila", "123", "Camila Soto", "picker", "", 420, 17),
            ("picker_alex", "123", "Alex Munoz", "picker", "", 210, 8),
            ("hunter_mario", "123", "Mario Torres", "hunter", "", 180, 6),
            ("hunter_lucia", "123", "Lucia Vargas", "hunter", "", 290, 11),
            ("p0a005g", "123", "Pablo Alvarez", "supervisor", "", 0, 0)
        ])
        
        cursor.executemany("""
        INSERT INTO hunts (item_name, barcode, aisle, quantity, reported_by, status, assigned_to)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            ("Detergente Liquido Omo 3L", "7802230001234", "General", 2, "Camila Soto", "Buscando", None),
            ("Soprole Leche Entera 1L Tetra", "7802100004567", "General", 5, "Juan Perez", "Yendo", "Camila Soto"),
            ("Aceite Vegetal Natura 1.5L", "7801540009876", "General", 1, "Alex Munoz", "Encontrado", "Juan Perez"),
            ("Papel Higienico Elite Ultra 18 un", "7801234567890", "General", 3, "Diego Rojas", "Sin Stock", None)
        ])
        
        cursor.executemany("""
        INSERT INTO feed (message)
        VALUES (?)
        """, [
            ("Bienvenido al Picker Hunt de la Tienda 99!",),
            ("Camila Soto completo la busqueda de Aceite Natura y gano +50 pts!",),
            ("Juan Perez reporto faltante de Detergente Omo.",),
            ("Alex Munoz marco Papel Higienico Elite como Sin Stock.",)
        ])
        
    # Migration: ensure hunter users exist even on old DBs
    for uname, fname in [("hunter_mario", "Rodrigo Ibanez"), ("hunter_lucia", "Camila Bravo"), ("jose", "Andres Parra")]:
        exists = cursor.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone()
        if not exists:
            cursor.execute(
                "INSERT INTO users (username, password, full_name, role, avatar, points, hunts_completed) VALUES (?,?,?,?,?,?,?)",
                (uname, "123", fname, "hunter", "", 0, 0)
            )

    # Migration: ensure picker test users exist
    for uname, fname in [("picker_juan", "Juan Perez"), ("picker_camila", "Camila Soto"), ("picker_alex", "Alex Munoz")]:
        exists = cursor.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone()
        if not exists:
            cursor.execute(
                "INSERT INTO users (username, password, full_name, role, avatar, points, hunts_completed) VALUES (?,?,?,?,?,?,?)",
                (uname, "123", fname, "picker", "", 0, 0)
            )

    # Migration: ensure supervisor exists
    exists = cursor.execute("SELECT 1 FROM users WHERE role='supervisor'").fetchone()
    if not exists:
        cursor.execute(
            "INSERT INTO users (username, password, full_name, role, avatar, points, hunts_completed) VALUES (?,?,?,?,?,?,?)",
            ("p0a005g", "123", "Pablo Alvarez", "supervisor", "", 0, 0)
        )

    # Migration: add is_supervisor column to feed if missing
    try:
        cursor.execute("ALTER TABLE feed ADD COLUMN is_supervisor INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Migration: add is_hunter column to feed if missing
    try:
        cursor.execute("ALTER TABLE feed ADD COLUMN is_hunter INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

init_db()

# Lee app.js para incrustarlo inline en el HTML (elimina dependencia de carga externa)
import pathlib as _pathlib, re as _re
def _load_app_js():
    p = _pathlib.Path("static/app.js")
    if not p.exists():
        return ""
    txt = p.read_bytes().decode("ascii", errors="replace")
    # Eliminar lineas en blanco dobles del proceso de extraccion
    txt = _re.sub(r'\n{2,}', '\n', txt)
    return txt
_APP_JS_CONTENT = _load_app_js()

# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Picker Hunt - Tienda 99")
app.mount("/js", StaticFiles(directory="static"), name="js")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.middleware("http")
async def no_cache_html(request, call_next):
    response = await call_next(request)
    if "text/html" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Real-time WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._by_user: dict[str, list[WebSocket]] = {}  # username -> sockets

    async def connect(self, websocket: WebSocket, username: str = ""):
        await websocket.accept()
        self.active_connections.append(websocket)
        if username:
            self._by_user.setdefault(username, []).append(websocket)

    def disconnect(self, websocket: WebSocket, username: str = ""):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if username in self._by_user:
            try:
                self._by_user[username].remove(websocket)
            except ValueError:
                pass

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection)

    async def notify_user(self, username: str, message: str):
        """Send a targeted message to all sockets owned by `username`."""
        for ws in list(self._by_user.get(username, [])):
            try:
                await ws.send_text(message)
            except Exception:
                self.disconnect(ws, username)

    def online_usernames(self) -> set:
        """Usernames con al menos 1 conexion WebSocket activa."""
        return {u for u, socks in self._by_user.items() if socks}

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user: str = Query("")):
    await manager.connect(websocket, user)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user)
    except Exception:
        manager.disconnect(websocket, user)

# Helper to get logged-in user from Cookie
def get_current_user(request: Request):
    username = request.cookies.get("session_user")
    if not username:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return user

# Spark Walmart: 6 petalos redondeados, amarillo oficial #ffc220
_WALMART_SPARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
    'width="16" height="16" style="display:inline-block;vertical-align:middle;flex-shrink:0;">'
    '<g transform="translate(50,50)">'
    '<rect x="-7" y="-45" width="14" height="30" rx="7" fill="#ffc220"/>'
    '<rect x="-7" y="-45" width="14" height="30" rx="7" fill="#ffc220" transform="rotate(60)"/>'
    '<rect x="-7" y="-45" width="14" height="30" rx="7" fill="#ffc220" transform="rotate(120)"/>'
    '<rect x="-7" y="-45" width="14" height="30" rx="7" fill="#ffc220" transform="rotate(180)"/>'
    '<rect x="-7" y="-45" width="14" height="30" rx="7" fill="#ffc220" transform="rotate(240)"/>'
    '<rect x="-7" y="-45" width="14" height="30" rx="7" fill="#ffc220" transform="rotate(300)"/>'
    '</g></svg>'
)

# Badge completo estilo logo oficial Walmart (marino oscuro, spark, divisor, texto blanco)
_WALMART_BADGE_HTML = (
    '<span style="background:#0a1628;color:white;font-weight:800;font-size:11px;'
    'padding:4px 10px 4px 8px;border-radius:999px;'
    'display:inline-flex;align-items:center;gap:6px;'
    'box-shadow:0 1px 4px rgba(0,0,0,.35);">'
    + _WALMART_SPARK_SVG
    + '<span style="display:inline-block;width:1px;height:13px;'
    'background:rgba(255,255,255,.35);flex-shrink:0;"></span>'
    '<span style="letter-spacing:.02em;">Walmart</span>'
    '</span>'
)

def _pts_badge(user: dict) -> str:
    """Badge de puntos: logo Walmart para supervisores, estrella+pts para otros."""
    if user['role'] == 'supervisor':
        return _WALMART_BADGE_HTML
    # Hunters y pickers: badge amarillo con estrella y puntos
    return (
        f'<span style="background:#ffc220;color:#111827;font-weight:900;font-size:11px;'
        f'padding:3px 8px;border-radius:999px;display:inline-flex;align-items:center;gap:4px;">'
        f'&#11088; {user["points"]} pts</span>'
    )


# Helper template renderer to keep the code fully responsive & gorgeous
def render_template(content_html: str, user=None, active_tab: str = "dashboard"):
    user_nav = ""
    if user:
        user_nav = f"""
        <div class="flex items-center gap-3 bg-white/10 px-3 py-1.5 rounded-full text-white">
            <span class="text-xl">{user['avatar']}</span>
            <div class="text-left leading-none hidden sm:block">
                <p class="font-bold text-xs text-white">{user['full_name']}</p>
                <p class="text-[9px] text-gray-300 uppercase tracking-widest">{user['role']}</p>
            </div>
            <span class="flex items-center gap-1">
                {_pts_badge(user)}
            </span>
            <a href="/profile" class="hover:bg-white/20 p-1 rounded-full transition" title="Cambiar Contrasena">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                </svg>
            </a>
            <a href="/logout" class="hover:bg-white/20 p-1 rounded-full transition" title="Cerrar Sesion">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
            </a>
        </div>
        """
    else:
        user_nav = """
        <span class="bg-[#ffc220] text-gray-900 font-bold px-3 py-1 rounded-full text-[10px] shadow">
             Tienda 99
        </span>
        """

    # Navigation tabs (responsive)
    tabs_html = ""
    bottom_nav = ""
    if user:
        dashboard_class = "bg-white text-[#0053e2] shadow-sm" if active_tab == "dashboard" else "text-white/85 hover:bg-white/10"
        leaderboard_class = "bg-white text-[#0053e2] shadow-sm" if active_tab == "leaderboard" else "text-white/85 hover:bg-white/10"
        stats_class = "bg-white text-[#0053e2] shadow-sm" if active_tab == "stats" else "text-white/85 hover:bg-white/10"
        admin_class = "bg-white text-[#0053e2] shadow-sm" if active_tab == "admin" else "text-white/85 hover:bg-white/10"
        is_supervisor = user['role'] == 'supervisor'

        # Desktop Nav
        admin_tab_desktop = f'<a href="/admin/users" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition {admin_class}"> Usuarios</a>' if is_supervisor else ''
        tabs_html = f"""
        <div class="hidden md:flex gap-1 bg-blue-900/30 p-1 rounded-xl">
            <a href="/dashboard" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition {dashboard_class}">
                 Busquedas
            </a>
            <a href="/leaderboard" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition {leaderboard_class}">
                 Ranking
            </a>
            <a href="/stats" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition {stats_class}">
                 Metricas
            </a>
            {admin_tab_desktop}
        </div>
        """

        # Mobile Bottom Navigation Bar (App style)
        admin_tab_mobile = f"""
            <a href="/admin/users" class="flex flex-col items-center gap-1 {'text-[#0053e2]' if active_tab == 'admin' else 'text-gray-400'}">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                <span class="text-[10px] font-bold">Usuarios</span>
            </a>""" if is_supervisor else ''

        can_report = user['role'] in ('picker', 'supervisor')

        bottom_nav = f"""
        <div class="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 shadow-lg px-4 py-2 flex justify-around items-center z-40 pb-safe">
            <a href="/dashboard" class="flex flex-col items-center gap-1 {'text-[#0053e2]' if active_tab == 'dashboard' else 'text-gray-400'}">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <span class="text-[10px] font-bold">Busquedas</span>
            </a>
            {admin_tab_mobile}
            <a href="/leaderboard" class="flex flex-col items-center gap-1 {'text-[#0053e2]' if active_tab == 'leaderboard' else 'text-gray-400'}">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />
                </svg>
                <span class="text-[10px] font-bold">Ranking</span>
            </a>
            <a href="/stats" class="flex flex-col items-center gap-1 {'text-[#0053e2]' if active_tab == 'stats' else 'text-gray-400'}">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
                <span class="text-[10px] font-bold">Metricas</span>
            </a>
            {admin_tab_mobile}
        </div>
        """

    base_template = """
    <!DOCTYPE html>
    <html lang="es" class="h-full bg-gray-50">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no, viewport-fit=cover">
        <title>Picker Hunt — Tienda 99</title>
        
        <!-- PWA / App conversion tags -->
        <link rel="manifest" href="/manifest.json">
        <meta name="theme-color" content="#0053e2">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <meta name="apple-mobile-web-app-title" content="Picker Hunt">
        <link rel="apple-touch-icon" href="/static/icon.svg">
        
        <link rel="stylesheet" href="/js/tailwind.css">
        <script src="/js/htmx.min.js"></script>
        <script src="/js/chart.umd.min.js"></script>
        <style>
            .custom-scrollbar::-webkit-scrollbar {
                width: 6px;
            }
            .custom-scrollbar::-webkit-scrollbar-track {
                background: transparent;
            }
            .custom-scrollbar::-webkit-scrollbar-thumb {
                background-color: rgba(156, 163, 175, 0.5);
                border-radius: 20px;
            }
            body {
                padding-bottom: 70px; /* Space for mobile navigation bar */
            }
            @media (min-width: 768px) {
                body {
                    padding-bottom: 0px;
                }
            }
            @keyframes toastIn {
                from { opacity:0; transform:translateX(60px) scale(.9); }
                to   { opacity:1; transform:translateX(0)    scale(1);  }
            }
            @keyframes fadeIn {
                from { opacity:0; } to { opacity:1; }
            }
            @keyframes pulse-border {
                0%,100% { box-shadow:0 0 0 0 rgba(252,165,165,.6); }
                50%     { box-shadow:0 0 0 12px rgba(252,165,165,0); }
            }
            @keyframes feedPulse {
                0%, 100% { background:rgba(0,83,226,0.12); box-shadow:0 0 0 0 rgba(0,83,226,0.25); }
                50%       { background:rgba(0,83,226,0.22); box-shadow:0 0 0 6px rgba(0,83,226,0);  }
            }
            @keyframes feedPulseHunter {
                0%, 100% { background:rgba(234,179,8,0.10); }
                50%       { background:rgba(234,179,8,0.20); }
            }
        </style>
    </head>
    <body class="h-full flex flex-col font-sans text-gray-800" data-ws-user="{WS_USER_VAL}">
        <div class="bg-[#ffc220] h-1.5 w-full"></div>

        <header class="bg-[#0053e2] text-white shadow-lg sticky top-0 z-30">
            <div class="max-w-7xl mx-auto px-4 py-2 flex items-center justify-between gap-2">
                <a href="/" class="flex items-center gap-2">
                    <div>
                        <h1 class="text-base font-black tracking-tight leading-none flex items-center gap-1.5 flex-wrap">
                            Picker Hunt 
                            <span class="bg-red-500 text-white text-[8px] font-black px-1.5 py-0.5 rounded uppercase tracking-wider">LIVE</span>
                            <span id="ws-status-badge" class="bg-gray-500 text-white text-[8px] font-black px-1.5 py-0.5 rounded uppercase tracking-wider">Desconectado</span>
                        </h1>
                        <p class="text-[10px] text-blue-200">Tienda 99</p>
                    </div>
                </a>
                
                <!-- Install App Native Button -->
                <button id="pwa-install-btn" onclick="triggerPWAInstall()" class="hidden bg-[#ffc220] hover:bg-yellow-500 text-gray-900 font-bold text-[10px] px-3 py-1.5 rounded-full flex items-center gap-1 shadow animate-pulse transition">
                     Instalar App
                </button>
                
                {TABS_HTML}

                <div class="flex items-center gap-2">
                    {USER_NAV}
                </div>
            </div>
        </header>

        <main class="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 lg:p-8">
            {CONTENT_HTML}
        </main>

        <!-- REPORT MODAL INTERFACE (MOBILE FULLSCREEN) -->
        <!-- MODAL: REPORTAR FALTANTE -->
        <div id="mobile-report-modal"
             style="position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;
                    align-items:flex-end;justify-content:center;
                    padding-bottom:env(safe-area-inset-bottom,0);"
             class="hidden opacity-0 transition-opacity duration-300">
            <div id="report-modal-sheet"
                 style="background:#fff;width:100%;max-width:480px;
                        border-radius:20px 20px 0 0;padding:0;
                        max-height:62dvh;max-height:62vh;
                        display:flex;flex-direction:column;
                        box-shadow:0 -8px 40px rgba(0,0,0,.18);
                        overscroll-behavior:contain;"
                 class="transform translate-y-full transition-transform duration-300">

                <!-- Handle + Header (fijo, no scrollea) -->
                <div style="flex-shrink:0;padding:8px 16px 0;">
                    <div style="width:32px;height:3px;background:#e5e7eb;border-radius:2px;margin:0 auto 8px;"></div>
                    <div style="display:flex;align-items:center;justify-content:space-between;
                                padding-bottom:8px;border-bottom:1px solid #f3f4f6;">
                        <div style="display:flex;align-items:center;gap:8px;">
                            <span style="width:7px;height:7px;border-radius:50%;background:#d13438;
                                         animation:pulse 1.5s infinite;flex-shrink:0;"></span>
                            <h3 style="font-size:13px;font-weight:800;color:#111827;margin:0;">Reportar Faltante</h3>
                        </div>
                        <button onclick="closeReportModal()"
                                style="background:#f9fafb;border:none;border-radius:8px;
                                       width:28px;height:28px;cursor:pointer;
                                       display:flex;align-items:center;justify-content:center;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none"
                                 viewBox="0 0 24 24" stroke="#6b7280" stroke-width="2.5">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- FORM (scrolleable) -->
                <form id="mobile-report-form" action="/api/hunts" method="post"
                      enctype="multipart/form-data"
                      style="flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;
                             padding:10px 16px 16px;display:flex;flex-direction:column;gap:8px;"
                      onsubmit="return submitReportModal(event)">

                    <!-- ITEM / CODIGO -->
                    <div>
                        <label style="display:block;font-size:9.5px;font-weight:700;color:#6b7280;
                                      text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Codigo / Item</label>
                        <div id="barcode-wrap"
                             style="display:flex;align-items:center;gap:8px;
                                    border:1.5px solid #e5e7eb;border-radius:10px;
                                    background:#f9fafb;padding:0 10px;
                                    transition:border-color .2s,box-shadow .2s,background .2s;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" fill="none"
                                 viewBox="0 0 24 24" stroke="#9ca3af" stroke-width="2" style="flex-shrink:0;">
                                <path stroke-linecap="round" stroke-linejoin="round"
                                    d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01
                                       M16 20h4M4 12h4m12 0h.01M5 8H2a2 2 0 00-2 2v4a2 2 0 002 2h3
                                       m14-8h2a2 2 0 012 2v4a2 2 0 01-2 2h-3
                                       M9 20H5a2 2 0 01-2-2v-4a2 2 0 012-2h4"/>
                            </svg>
                            <input type="text" name="barcode" required placeholder="Ej: 7802100004567"
                                   style="flex:1;border:none;outline:none;background:transparent;
                                          padding:7px 0;font-size:12px;color:#111827;min-width:0;"
                                   onfocus="var w=document.getElementById('barcode-wrap');
                                            w.style.borderColor='#d13438';
                                            w.style.boxShadow='0 0 0 3px rgba(209,52,56,.10)';
                                            w.style.background='#fff';"
                                   onblur="var w=document.getElementById('barcode-wrap');
                                           w.style.borderColor='#e5e7eb';
                                           w.style.boxShadow='none';
                                           w.style.background='#f9fafb';" />
                        </div>
                    </div>

                    <!-- DESCRIPCION -->
                    <div>
                        <label style="display:block;font-size:9.5px;font-weight:700;color:#6b7280;
                                      text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Descripcion</label>
                        <div id="desc-wrap"
                             style="display:flex;align-items:center;gap:8px;
                                    border:1.5px solid #e5e7eb;border-radius:10px;
                                    background:#f9fafb;padding:0 10px;
                                    transition:border-color .2s,box-shadow .2s,background .2s;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" fill="none"
                                 viewBox="0 0 24 24" stroke="#9ca3af" stroke-width="2" style="flex-shrink:0;">
                                <path stroke-linecap="round" stroke-linejoin="round"
                                    d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7
                                       a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7
                                       A1.994 1.994 0 013 12V7a4 4 0 014-4z"/>
                            </svg>
                            <input type="text" name="item_name" required placeholder="Ej: Soprole Leche Entera 1L"
                                   style="flex:1;border:none;outline:none;background:transparent;
                                          padding:7px 0;font-size:12px;color:#111827;min-width:0;"
                                   onfocus="var w=document.getElementById('desc-wrap');
                                            w.style.borderColor='#d13438';
                                            w.style.boxShadow='0 0 0 3px rgba(209,52,56,.10)';
                                            w.style.background='#fff';"
                                   onblur="var w=document.getElementById('desc-wrap');
                                           w.style.borderColor='#e5e7eb';
                                           w.style.boxShadow='none';
                                           w.style.background='#f9fafb';" />
                        </div>
                    </div>

                    <!-- CANTIDAD + FOTO en fila -->
                    <div style="display:flex;gap:10px;align-items:flex-start;">
                        <!-- Cantidad -->
                        <div style="flex-shrink:0;">
                            <label style="display:block;font-size:9.5px;font-weight:700;color:#6b7280;
                                          text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Cant.</label>
                            <div id="qty-wrap"
                                 style="display:flex;align-items:center;gap:6px;
                                        border:1.5px solid #e5e7eb;border-radius:10px;
                                        background:#f9fafb;padding:0 10px;width:90px;
                                        transition:border-color .2s,box-shadow .2s,background .2s;">
                                <input type="number" name="quantity" min="1" max="100" value="1" required
                                       style="flex:1;border:none;outline:none;background:transparent;
                                              padding:7px 0;font-size:12px;color:#111827;min-width:0;
                                              -moz-appearance:textfield;"
                                       onfocus="var w=document.getElementById('qty-wrap');
                                                w.style.borderColor='#d13438';
                                                w.style.boxShadow='0 0 0 3px rgba(209,52,56,.10)';
                                                w.style.background='#fff';"
                                       onblur="var w=document.getElementById('qty-wrap');
                                               w.style.borderColor='#e5e7eb';
                                               w.style.boxShadow='none';
                                               w.style.background='#f9fafb';" />
                            </div>
                        </div>

                        <!-- Foto (botones compactos en fila) -->
                        <div style="flex:1;">
                            <label style="display:block;font-size:9.5px;font-weight:700;color:#6b7280;
                                          text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Foto <span style="font-weight:400;text-transform:none;color:#9ca3af;font-size:9px;">(opcional)</span></label>
                            <div style="display:flex;gap:6px;">
                                <button type="button" onclick="triggerCamera()"
                                        style="flex:1;background:#f9fafb;border:1.5px solid #e5e7eb;
                                               border-radius:10px;padding:7px 4px;
                                               display:flex;align-items:center;justify-content:center;gap:5px;
                                               cursor:pointer;transition:background .15s,border-color .15s;"
                                        onmouseover="this.style.background='#eff6ff';this.style.borderColor='#0053e2';"
                                        onmouseout="this.style.background='#f9fafb';this.style.borderColor='#e5e7eb';">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none"
                                         viewBox="0 0 24 24" stroke="#0053e2" stroke-width="1.8">
                                        <path stroke-linecap="round" stroke-linejoin="round"
                                            d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22
                                               A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22
                                               A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5
                                               a2 2 0 01-2-2V9z"/>
                                        <path stroke-linecap="round" stroke-linejoin="round" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"/>
                                    </svg>
                                    <span style="font-size:10px;font-weight:700;color:#374151;">Camara</span>
                                </button>
                                <button type="button" onclick="triggerGallery()"
                                        style="flex:1;background:#f9fafb;border:1.5px solid #e5e7eb;
                                               border-radius:10px;padding:7px 4px;
                                               display:flex;align-items:center;justify-content:center;gap:5px;
                                               cursor:pointer;transition:background .15s,border-color .15s;"
                                        onmouseover="this.style.background='#f0fdf4';this.style.borderColor='#16a34a';"
                                        onmouseout="this.style.background='#f9fafb';this.style.borderColor='#e5e7eb';">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none"
                                         viewBox="0 0 24 24" stroke="#16a34a" stroke-width="1.8">
                                        <path stroke-linecap="round" stroke-linejoin="round"
                                            d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16
                                               m-2-2l1.586-1.586a2 2 0 012.828 0L20 14
                                               m-6-6h.01M6 20h12a2 2 0 002-2V6
                                               a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                                    </svg>
                                    <span style="font-size:10px;font-weight:700;color:#374151;">Galeria</span>
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Preview foto (compacto, max 80px alto) -->
                    <div id="photo-preview-container" class="hidden"
                         style="position:relative;width:100%;max-height:80px;
                                background:#000;border-radius:10px;overflow:hidden;
                                border:1px solid #e5e7eb;align-items:center;justify-content:center;">
                        <img id="photo-preview" src="#" alt="Preview"
                             style="max-width:100%;max-height:80px;object-fit:contain;" />
                        <button type="button" onclick="clearPhotoInputs(event)"
                                style="position:absolute;top:4px;right:4px;
                                       background:rgba(0,0,0,.6);border:none;
                                       border-radius:6px;padding:4px;
                                       cursor:pointer;display:flex;align-items:center;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="none"
                                 viewBox="0 0 24 24" stroke="white" stroke-width="2.5">
                                <path stroke-linecap="round" stroke-linejoin="round"
                                    d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862
                                       a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4
                                       a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                            </svg>
                        </button>
                    </div>

                    <!-- Inputs ocultos -->
                    <input id="camera-only-input" name="photo_camera" type="file"
                           accept="image/*" capture="environment"
                           style="display:none;" onchange="previewPhotoSelected(this)" />
                    <input id="gallery-only-input" name="photo_gallery" type="file"
                           accept="image/*" style="display:none;" onchange="previewPhotoSelected(this)" />

                    <!-- SUBMIT -->
                    <button type="submit"
                            style="width:100%;display:flex;align-items:center;justify-content:center;gap:8px;
                                   background:linear-gradient(145deg,#d13438 0%,#a52b2e 100%);
                                   color:white;border:none;border-radius:12px;
                                   padding:12px;font-size:13px;font-weight:700;
                                   letter-spacing:.04em;
                                   box-shadow:0 4px 14px rgba(209,52,56,.32);
                                   cursor:pointer;transition:filter .15s,transform .15s;"
                            onmouseover="this.style.filter='brightness(.92)'"
                            onmouseout="this.style.filter=''"
                            onmousedown="this.style.transform='scale(.98)'"
                            onmouseup="this.style.transform=''">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none"
                             viewBox="0 0 24 24" stroke="white" stroke-width="2.5">
                            <path stroke-linecap="round" stroke-linejoin="round"
                                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11
                                   a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341
                                   C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436
                                   L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
                        </svg>
                        ENVIAR ALERTA
                    </button>
                </form>
            </div>
        </div>

        <footer class="bg-white border-t border-gray-200 py-4 mt-12 text-center text-[10px] text-gray-500 hidden md:block">
            <div class="max-w-7xl mx-auto px-4 flex flex-col md:flex-row justify-between items-center gap-2">
                <p>&copy; {YEAR} · Picker Hunt · Tienda 99 (Walmart Chile)</p>
            </div>
        </footer>

        <!-- Modal: No Encontrado (hunter) -->
        <div id="not-found-modal" class="fixed inset-0 bg-black/60 z-50 flex items-center justify-center hidden">
            <div class="bg-white rounded-2xl p-6 mx-4 w-full max-w-sm shadow-2xl">
                <h3 class="font-black text-lg text-red-700 mb-1">No Encontrado</h3>
                <p id="nf-item-name" class="text-sm text-gray-600 mb-4 font-semibold"></p>
                <form id="not-found-form" action="/api/hunts/__HID__/no-stock"
                      onsubmit="return submitResolutionForm('not-found-form', closeNotFoundModal)">
                    <label class="text-xs font-bold text-gray-500 uppercase mb-1 block">Notas (opcional)</label>
                    <textarea name="notes" rows="3" placeholder="Ej: Producto descontinuado..."
                              class="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-red-500"></textarea>
                    <div class="flex gap-3">
                        <button type="button" onclick="closeNotFoundModal()"
                                class="flex-1 bg-gray-100 text-gray-600 font-bold py-3 rounded-xl text-sm">Cancelar</button>
                        <button type="submit"
                                class="flex-1 bg-red-600 text-white font-bold py-3 rounded-xl text-sm">Confirmar</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Modal: Ajustar Inventario (hunter) -->
        <div id="adjust-modal" class="fixed inset-0 bg-black/60 z-50 flex items-center justify-center hidden">
            <div class="bg-white rounded-2xl p-6 mx-4 w-full max-w-sm shadow-2xl">
                <h3 class="font-black text-lg text-teal-700 mb-1">Ajustar Inventario</h3>
                <p id="adj-item-name" class="text-sm text-gray-600 mb-4 font-semibold"></p>
                <form id="adjust-form" action="/api/hunts/__HID__/adjust-inventory"
                      onsubmit="return submitResolutionForm('adjust-form', closeAdjustModal)">
                    <label class="text-xs font-bold text-gray-500 uppercase mb-1 block">Delta de inventario (+/-)</label>
                    <input type="number" name="inventory_delta" id="adj-qty" min="-999" max="999" required
                           class="w-full border border-gray-300 rounded-xl px-3 py-3 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-teal-500">
                    <label class="text-xs font-bold text-gray-500 uppercase mb-1 block">Notas</label>
                    <textarea name="resolution_note" rows="2" placeholder="Ej: Encontrado en bodega..."
                              class="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-teal-500"></textarea>
                    <div class="flex gap-3">
                        <button type="button" onclick="closeAdjustModal()"
                                class="flex-1 bg-gray-100 text-gray-600 font-bold py-3 rounded-xl text-sm">Cancelar</button>
                        <button type="submit"
                                class="flex-1 bg-teal-600 text-white font-bold py-3 rounded-xl text-sm">Confirmar</button>
                    </div>
                </form>
            </div>
        </div>

        {BOTTOM_NAV}

        <!-- Modal: En Sala con Foto (hunter) - auto-envia al elegir foto -->
        <div id="sala-photo-modal" class="fixed inset-0 bg-black/60 z-50 flex items-end justify-center hidden">
            <div class="bg-white rounded-t-2xl p-5 w-full max-w-sm shadow-2xl">
                <div class="flex items-center justify-between mb-2">
                    <h3 class="font-black text-base text-green-700">En Sala</h3>
                    <button type="button" onclick="closeSalaPhotoModal()" class="text-gray-400 hover:text-gray-600 text-xl font-bold leading-none">&times;</button>
                </div>
                <p id="sala-photo-item-name" class="text-xs text-gray-500 mb-4 font-semibold truncate"></p>
                <!-- Botones camara/galeria: al elegir foto se envia automaticamente -->
                <div id="sala-photo-buttons" class="flex gap-3 mb-3">
                    <button type="button" onclick="triggerCamera('sala-')" class="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-bold py-4 rounded-xl flex flex-col items-center gap-1 transition">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
                        Camara
                    </button>
                    <button type="button" onclick="triggerGallery('sala-')" class="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-bold py-4 rounded-xl flex flex-col items-center gap-1 transition">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                        Galeria
                    </button>
                </div>
                <input id="sala-camera-only-input" type="file" accept="image/*" capture="environment" class="hidden" onchange="previewSalaPhoto(this)" />
                <input id="sala-gallery-only-input" type="file" accept="image/*" class="hidden" onchange="previewSalaPhoto(this)" />
                <!-- Estado cargando (se muestra al elegir foto) -->
                <div id="sala-upload-state" class="hidden text-center py-5">
                    <p class="text-green-600 font-black text-sm">Enviando...</p>
                    <p class="text-gray-400 text-xs mt-1">Subiendo foto de ubicacion</p>
                </div>
                <button type="button" onclick="submitSalaFound(false)" id="sala-no-photo-btn" class="w-full mt-1 text-gray-400 text-xs font-semibold py-2 hover:text-gray-600 transition">Enviar sin foto</button>
            </div>
        </div>

        <!-- Banner notificacion (picker): desliza desde arriba, no bloquea pantalla -->
        <!-- Banner generico: sala (verde) o bodega (azul) - desliza desde arriba -->
        <div id="found-notif-banner"
             style="position:fixed;top:0;left:0;right:0;z-index:9998;
                    transform:translateY(-110%);transition:transform .35s cubic-bezier(.2,.8,.4,1);
                    padding:env(safe-area-inset-top,0) 0 0;pointer-events:auto;">
            <div id="found-notif-inner"
                 style="background:#15803d;color:white;padding:12px 14px;
                        display:flex;align-items:center;gap:10px;
                        box-shadow:0 4px 24px rgba(0,0,0,.3);">
                <!-- Icono intercambiable segun tipo -->
                <div style="flex-shrink:0;width:38px;height:38px;background:rgba(255,255,255,.18);
                            border-radius:10px;display:flex;align-items:center;justify-content:center;">
                    <!-- sala: pin ubicacion -->
                    <svg id="found-notif-icon-sala" xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="white" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                    <!-- bodega: caja -->
                    <svg id="found-notif-icon-bodega" xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="white" stroke-width="2" style="display:none;"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"/></svg>
                </div>
                <!-- Texto -->
                <div style="flex:1;min-width:0;">
                    <p id="found-notif-label" style="font-size:10px;font-weight:900;margin:0;letter-spacing:.04em;opacity:.85;">PRODUCTO EN SALA</p>
                    <p id="found-notif-item" style="font-size:13px;font-weight:900;margin:1px 0 0;
                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></p>
                    <p id="found-notif-hunter" style="font-size:10px;margin:1px 0 0;opacity:.75;"></p>
                </div>
                <!-- Boton ver foto (solo sala, cuando foto lista) -->
                <button id="found-notif-ver-btn" onclick="openSalaPhotoSheet()"
                        style="background:white;color:#15803d;border:none;border-radius:8px;
                               padding:7px 13px;font-size:11px;font-weight:900;cursor:pointer;
                               white-space:nowrap;flex-shrink:0;display:none;">Ver foto</button>
                <!-- Cerrar -->
                <button onclick="closeFoundNotif()"
                        style="background:none;border:none;color:rgba(255,255,255,.6);
                               font-size:22px;font-weight:900;cursor:pointer;
                               padding:0 0 0 2px;line-height:1;flex-shrink:0;">&times;</button>
            </div>
        </div>

        <!-- Sheet foto ubicacion (picker): sube desde abajo al tocar "Ver foto" -->
        <div id="sala-photo-sheet-overlay"
             style="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0);
                    pointer-events:none;transition:background .3s;"
             onclick="if(event.target===this)closeSalaPhotoSheet();">
            <div id="sala-photo-sheet"
                 style="position:absolute;bottom:0;left:0;right:0;
                        background:white;border-radius:20px 20px 0 0;
                        padding:12px 16px 24px;
                        transform:translateY(100%);transition:transform .35s cubic-bezier(.2,.8,.4,1);
                        max-width:500px;margin:0 auto;">
                <!-- Handle indicator -->
                <div style="width:40px;height:4px;background:#e5e7eb;border-radius:2px;margin:0 auto 14px;"></div>
                <!-- Header: texto + cerrar -->
                <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px;">
                    <div style="flex:1;min-width:0;">
                        <p style="font-size:14px;font-weight:900;color:#15803d;margin:0;">Esta en sala</p>
                        <p id="sala-sheet-item" style="font-size:13px;font-weight:700;color:#111827;
                            margin:3px 0 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></p>
                        <p id="sala-sheet-hunter" style="font-size:11px;color:#9ca3af;margin:2px 0 0;"></p>
                    </div>
                    <button onclick="closeSalaPhotoSheet()"
                            style="background:none;border:none;color:#d1d5db;font-size:24px;
                                   font-weight:900;cursor:pointer;line-height:1;flex-shrink:0;margin-left:12px;">&times;</button>
                </div>
                <!-- Imagen 4:3 — formato natural de camara mobile -->
                <div style="width:100%;aspect-ratio:4/3;background:#f3f4f6;border-radius:14px;
                            overflow:hidden;position:relative;margin-bottom:16px;">
                    <div id="sala-sheet-loading"
                         style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;">
                        <p style="color:#9ca3af;font-size:12px;margin:0;">Cargando foto...</p>
                    </div>
                    <img id="sala-sheet-photo" src="#" alt="Ubicacion del producto"
                         style="width:100%;height:100%;object-fit:contain;display:none;"
                         onload="this.style.display='block';
                                 var ld=document.getElementById('sala-sheet-loading');
                                 if(ld)ld.style.display='none';" />
                </div>
                <button onclick="closeSalaPhotoSheet()"
                        style="width:100%;background:#15803d;color:white;border:none;
                               border-radius:12px;padding:14px;font-size:14px;
                               font-weight:900;cursor:pointer;">Entendido</button>
            </div>
        </div>

        <script src="/js/app.js?v=23" defer></script>
    </body>
    </html>
    """
    
    return (base_template
            .replace("{TABS_HTML}", tabs_html)
            .replace("{USER_NAV}", user_nav)
            .replace("{CONTENT_HTML}", content_html)
            .replace("{BOTTOM_NAV}", bottom_nav)
            .replace("{YEAR}", str(datetime.datetime.now().year))
            .replace("{QUANTITY_OPTIONS_HTML}", QUANTITY_OPTIONS_HTML)
            .replace("{WS_USER_VAL}", user['username'] if user else '')
            .replace("{JS_VERSION}", "inline")
            .replace("{APP_JS_CONTENT}", _APP_JS_CONTENT))

# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")

# LOGIN PAGE REPLICATION
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")

    conn = get_db()
    all_users = conn.execute(
        "SELECT username, full_name, role, avatar FROM users ORDER BY role, full_name"
    ).fetchall()
    conn.close()

    local_ips = get_local_ips()
    ip_buttons = []
    for ip in local_ips:
        url = f"http://{ip}:8099/login"
        ip_buttons.append(f"""
        <div class="flex items-center justify-between gap-1 bg-white p-2 rounded-lg border border-gray-100 shadow-sm">
          <span class="font-mono text-[10px] font-bold text-gray-700 select-all break-all">{url}</span>
          <button type="button" onclick="showQR('{url}')" class="bg-[#0053e2] hover:bg-blue-700 text-white text-[9px] font-black px-2 py-1 rounded transition shrink-0">
            QR 
          </button>
        </div>
        """)
    if not ip_buttons:
        ip_buttons.append("<p class='text-[10px] text-gray-400'>No se detectaron IPs locales de red.</p>")
    ip_buttons_html = "".join(ip_buttons)



    login_html = f"""
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <div class="min-h-[70vh] flex flex-col items-center justify-center p-4">
      <div class="bg-white rounded-2xl border border-gray-200 shadow-xl w-full max-w-sm p-6 sm:p-8 transition-transform hover:scale-[1.01]">
        <!-- Logo -->
        <div class="text-center mb-6">
          <div class="flex justify-center mb-2 animate-bounce">
            <svg class="h-12 w-12 text-[#ffc220]" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 0l2 8 8 2-8 2-2 8-2-8-8-2 8-2z" />
            </svg>
          </div>
          <h1 class="text-xl font-black text-[#0053e2] flex items-center justify-center gap-1">
            Picker Hunt
            <span class="bg-[#ffc220] text-gray-900 text-[9px] px-1.5 py-0.5 rounded font-extrabold uppercase">T99</span>
          </h1>
          <p class="text-gray-500 text-xs mt-1">Plataforma colaborativa en tiempo real</p>
        </div>

        <form method="post" action="/login" class="space-y-4">
          <div>
            <label for="username" class="block text-[10px] font-semibold text-gray-700 mb-1 uppercase tracking-wider">Usuario</label>
            <input
              id="username" name="username" type="text" required autocomplete="username"
              class="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"
              placeholder="Ej: picker_juan"
            />
          </div>
          <div>
            <label for="password" class="block text-[10px] font-semibold text-gray-700 mb-1 uppercase tracking-wider">Contrasena</label>
            <input
              id="password" name="password" type="password" required autocomplete="current-password"
              class="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"
              placeholder="••••••••"
              value="123"
            />
          </div>
          <button
            type="submit"
            class="w-full bg-[#0053e2] text-white py-3.5 rounded-xl font-bold text-sm hover:bg-blue-700 active:bg-blue-800 transition focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[#0053e2] shadow-md shadow-blue-500/20"
          >
            Ingresar
          </button>
        </form>


        <!-- CONEXIÓN MÓVIL DISPOSITIVOS (DE LA MEJOR FORMA) -->
        <div class="mt-6 pt-4 border-t border-gray-100 text-left">
          <details class="group bg-blue-50/50 rounded-xl p-3 border border-blue-100/60" open>
            <summary class="flex justify-between items-center font-bold text-xs text-[#0053e2] cursor-pointer select-none">
              <span class="flex items-center gap-1.5">
                 Pruebas Celular (Multi-cuenta)
              </span>
              <span class="transition-transform group-open:rotate-180 text-gray-400">
                ▼
              </span>
            </summary>
            
            <div class="mt-3 space-y-3 text-xs text-gray-600 leading-relaxed">
              <p class="text-[11px]">¡Conecta múltiples celulares para simular pickers en tiempo real!</p>
              
              <div class="bg-white border border-gray-100 rounded-xl p-2.5 shadow-sm">
                <p class="font-bold text-[#0053e2] text-[10px] mb-1">1. Red Wi-Fi / Hotspot</p>
                <p class="text-[10px] text-gray-500">Conecta tu celular al mismo Wi-Fi de esta PC, o usa la <strong>Zona Wi-Fi Móvil</strong> de Windows (cobertura inalámbrica móvil) para esquivar restricciones.</p>
              </div>

              <div class="bg-white border border-gray-100 rounded-xl p-2.5 shadow-sm">
                <p class="font-bold text-[#0053e2] text-[10px] mb-1">2. Escanear o Entrar</p>
                <p class="text-[10px] text-gray-500 mb-2">Escanea el QR o entra a cualquiera de estas direcciones:</p>
                
                <div class="space-y-1.5">
                  {ip_buttons_html}
                </div>
              </div>

              <div id="qrcode-container" class="hidden flex flex-col items-center justify-center p-3 bg-white border border-gray-200 rounded-xl shadow-sm">
                <div id="qrcode" class="mb-2"></div>
                <p id="qrcode-url" class="text-[9px] text-gray-400 font-mono text-center break-all select-all"></p>
              </div>
            </div>
          </details>
        </div>

        <p class="text-center text-[9px] text-gray-400 mt-6">Picker Hunt &copy; 2026 · Walmart Chile</p>
      </div>
    </div>
    """
    return HTMLResponse(content=render_template(login_html, user=None))

@app.post("/login")
def login_post(username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password)).fetchone()
    conn.close()
    if not user:
        return RedirectResponse(url="/login?error=1", status_code=303)
    
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session_user", 
        value=username, 
        max_age=86400,
        path="/",
        samesite="lax",
        secure=False,
        httponly=False
    )
    return response

@app.get("/logout")
def logout_post():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_user")
    return response

# DASHBOARD PAGE
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_get(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    conn = get_db()
    total_pickers = conn.execute("SELECT COUNT(*) FROM users WHERE role='picker'").fetchone()[0]
    total_hunters  = conn.execute("SELECT COUNT(*) FROM users WHERE role='hunter'").fetchone()[0]
    team_members   = conn.execute(
        "SELECT full_name, role FROM users WHERE role IN ('picker','hunter') ORDER BY role, full_name"
    ).fetchall()
    # Cumplimiento = solo hunts de HOY; Protocolo Confirmado cuenta como resuelto
    _TODAY = "DATE(reported_at) = DATE('now')"
    _RESOLVED_STATUSES = "('Encontrado','Ajustado','Sin Stock','Protocolo Confirmado')"
    total_hunts    = conn.execute(f"SELECT COUNT(*) FROM hunts WHERE {_TODAY}").fetchone()[0]
    resolved_hunts = conn.execute(
        f"SELECT COUNT(*) FROM hunts WHERE {_TODAY} AND status IN {_RESOLVED_STATUSES}"
    ).fetchone()[0]
    conn.close()

    completitud_pct   = round(resolved_hunts / total_hunts * 100, 1) if total_hunts else 0.0
    completitud_color = (
        "#16a34a" if completitud_pct >= 96
        else "#ca8a04" if completitud_pct >= 80
        else "#dc2626"
    )
    completitud_bar_w = min(100, int(completitud_pct))

    # Lista de pickers y hunters para el recuadro Equipo
    _ROLE_STYLE = {
        'picker': ('background:#eff6ff;color:#1d4ed8;', 'Picker'),
        'hunter': ('background:#f0fdf4;color:#15803d;', 'Hunter'),
    }
    team_members_html = ''.join(
        f'<div style="display:flex;align-items:center;justify-content:space-between;gap:6px;">'
        f'<span style="font-size:11px;font-weight:600;color:#111827;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{m["full_name"]}</span>'
        f'<span style="{_ROLE_STYLE[m["role"]][0]}font-size:9px;font-weight:800;'
        f'padding:2px 6px;border-radius:999px;flex-shrink:0;">{_ROLE_STYLE[m["role"]][1]}</span>'
        f'</div>'
        for m in team_members
    ) or '<span style="font-size:10px;color:#9ca3af;">Sin integrantes</span>'

    can_report = user['role'] in ('picker', 'supervisor')
    can_hunt   = user['role'] in ('hunter', 'supervisor')

    # Panel de aviso general — solo visible para supervisor
    if user['role'] == 'supervisor':
        broadcast_panel = """
    <div style="background:#ffffff;border:1px solid #f0f0f0;border-radius:18px;
                padding:8px 12px;box-shadow:0 1px 6px rgba(0,0,0,.06);
                display:flex;flex-direction:column;gap:8px;">
        <!-- Titulo compact -->
        <div style="display:flex;align-items:center;gap:7px;">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none"
                 viewBox="0 0 24 24" stroke="#0053e2" stroke-width="2.2">
                <path stroke-linecap="round" stroke-linejoin="round"
                    d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15
                       M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832
                       c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7
                       a3.928 3.928 0 01-1.564-.317z"/>
            </svg>
            <span style="font-size:12px;font-weight:600;color:#374151;">Aviso a todos</span>
        </div>
        <!-- Input + boton en fila -->
        <div style="display:flex;align-items:center;gap:8px;">
            <!-- Wrapper con borde — icono + input como flex siblings -->
            <div id="sup-input-wrap"
                 style="flex:1;display:flex;align-items:center;gap:6px;
                        border:1.5px solid #e5e7eb;border-radius:12px;
                        background:#f9fafb;padding:0 10px;
                        transition:border-color .2s,box-shadow .2s,background .2s;"
                 onclick="document.getElementById('broadcast-text').focus()">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none"
                     viewBox="0 0 24 24" stroke="#9ca3af" stroke-width="2" style="flex-shrink:0;">
                    <path stroke-linecap="round" stroke-linejoin="round"
                        d="M8 10h.01M12 10h.01M16 10h.01
                           M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949
                           L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12
                           c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                </svg>
                <input id="broadcast-text" type="text" maxlength="120"
                    placeholder="Que necesita saber el equipo? Ej: Llego pallet, quedar en zona transito."
                    style="flex:1;border:none;outline:none;background:transparent;
                           padding:8px 0;font-size:12px;color:#111827;min-width:0;"
                    onfocus="var w=document.getElementById('sup-input-wrap');
                             w.style.borderColor='#0053e2';
                             w.style.boxShadow='0 0 0 3px rgba(0,83,226,.10)';
                             w.style.background='#fff';"
                    onblur="var w=document.getElementById('sup-input-wrap');
                            w.style.borderColor='#e5e7eb';
                            w.style.boxShadow='none';
                            w.style.background='#f9fafb';"
                    onkeydown="if(event.key==='Enter') sendBroadcast()" />
            </div>
            <button onclick="sendBroadcast()"
                style="flex-shrink:0;display:flex;align-items:center;gap:6px;
                       background:#0053e2;color:white;font-weight:600;font-size:12px;
                       padding:8px 14px;border-radius:12px;border:none;cursor:pointer;
                       box-shadow:0 2px 8px rgba(0,83,226,.25);
                       transition:opacity .15s,transform .1s;"
                onmouseover="this.style.opacity='.88'"
                onmouseout="this.style.opacity='1'"
                onmousedown="this.style.transform='scale(.97)'"
                onmouseup="this.style.transform='scale(1)'">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none"
                     viewBox="0 0 24 24" stroke="white" stroke-width="2.5">
                    <path stroke-linecap="round" stroke-linejoin="round"
                        d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
                </svg>
                Enviar
            </button>
        </div>
    </div>"""
    elif user['role'] == 'hunter':
        broadcast_panel = """
    <div style="background:#ffffff;border:1px solid #f0f0f0;border-radius:18px;
                padding:8px 12px;box-shadow:0 1px 6px rgba(0,0,0,.06);
                display:flex;flex-direction:column;gap:8px;">
        <!-- Titulo compact -->
        <div style="display:flex;align-items:center;gap:7px;">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none"
                 viewBox="0 0 24 24" stroke="#E67E00" stroke-width="2.2">
                <path stroke-linecap="round" stroke-linejoin="round"
                    d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2
                       c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857
                       M7 20v-2c0-.656.126-1.283.356-1.857
                       m0 0a5.002 5.002 0 019.288 0
                       M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
            </svg>
            <span style="font-size:12px;font-weight:600;color:#374151;">
                Notificar al equipo
            </span>
        </div>
        <!-- Input + boton en fila -->
        <div style="display:flex;align-items:center;gap:8px;">
            <!-- Wrapper con borde — icono + input como flex siblings -->
            <div id="hun-input-wrap"
                 style="flex:1;display:flex;align-items:center;gap:6px;
                        border:1.5px solid #e5e7eb;border-radius:12px;
                        background:#f9fafb;padding:0 10px;
                        transition:border-color .2s,box-shadow .2s,background .2s;"
                 onclick="document.getElementById('hunter-broadcast-text').focus()">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none"
                     viewBox="0 0 24 24" stroke="#9ca3af" stroke-width="2" style="flex-shrink:0;">
                    <path stroke-linecap="round" stroke-linejoin="round"
                        d="M8 10h.01M12 10h.01M16 10h.01
                           M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949
                           L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12
                           c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                </svg>
                <input id="hunter-broadcast-text" type="text" maxlength="100"
                    placeholder="Que necesita el equipo? Ej: Gondola A3 sin stock de leche."
                    style="flex:1;border:none;outline:none;background:transparent;
                           padding:8px 0;font-size:12px;color:#111827;min-width:0;"
                    onfocus="var w=document.getElementById('hun-input-wrap');
                             w.style.borderColor='#E67E00';
                             w.style.boxShadow='0 0 0 3px rgba(230,126,0,.12)';
                             w.style.background='#fff';"
                    onblur="var w=document.getElementById('hun-input-wrap');
                            w.style.borderColor='#e5e7eb';
                            w.style.boxShadow='none';
                            w.style.background='#f9fafb';"
                    onkeydown="if(event.key==='Enter') sendHunterBroadcast()" />
            </div>
            <!-- Boton naranja -->
            <button onclick="sendHunterBroadcast()"
                style="flex-shrink:0;display:flex;align-items:center;gap:6px;
                       background:#E67E00;color:white;font-weight:600;font-size:12px;
                       padding:8px 14px;border-radius:12px;border:none;cursor:pointer;
                       box-shadow:0 2px 8px rgba(230,126,0,.30);
                       transition:opacity .15s,transform .1s;"
                onmouseover="this.style.opacity='.88'"
                onmouseout="this.style.opacity='1'"
                onmousedown="this.style.transform='scale(.97)'"
                onmouseup="this.style.transform='scale(1)'">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none"
                     viewBox="0 0 24 24" stroke="white" stroke-width="2.5">
                    <path stroke-linecap="round" stroke-linejoin="round"
                        d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
                </svg>
                Notificar
            </button>
        </div>
    </div>"""
    else:
        broadcast_panel = ""
    alerta_btn = """
        <button onclick="openReportModal()"
            style="width:100%;display:flex;align-items:center;gap:10px;
                   background:linear-gradient(145deg,#d13438 0%,#a52b2e 100%);
                   color:white;border:none;border-radius:18px;
                   padding:10px 20px;
                   cursor:pointer;position:relative;
                   box-shadow:0 8px 24px rgba(209,52,56,.38),0 2px 6px rgba(0,0,0,.12);
                   outline:1.5px solid rgba(255,255,255,.13);outline-offset:-1.5px;
                   transition:transform .15s,box-shadow .15s,filter .15s;"
            onmouseover="this.style.filter='brightness(.9)';this.style.transform='translateY(-2px)';
                         this.style.boxShadow='0 14px 32px rgba(209,52,56,.48),0 4px 12px rgba(0,0,0,.18)';"
            onmouseout="this.style.filter='';this.style.transform='';
                        this.style.boxShadow='0 8px 24px rgba(209,52,56,.38),0 2px 6px rgba(0,0,0,.12)';"
            onmousedown="this.style.transform='scale(.97)';
                         this.style.boxShadow='0 3px 10px rgba(209,52,56,.30)';"
            onmouseup="this.style.transform='';
                       this.style.boxShadow='0 8px 24px rgba(209,52,56,.38),0 2px 6px rgba(0,0,0,.12)';">
            <!-- Dot pulsante -->
            <span style="width:9px;height:9px;border-radius:50%;background:white;
                         opacity:.9;flex-shrink:0;animation:pulse 1.5s infinite;"></span>
            <!-- Icono campana con pulso suave -->
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none"
                 viewBox="0 0 24 24" stroke="white" stroke-width="2"
                 style="flex-shrink:0;animation:pulse 3s ease-in-out infinite;">
                <path stroke-linecap="round" stroke-linejoin="round"
                    d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11
                       a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341
                       C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436
                       L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
            </svg>
            <!-- Texto -->
            <span style="flex:1;font-size:14px;font-weight:700;letter-spacing:.06em;
                         text-align:left;line-height:1.2;">ALERTA DE QUIEBRE</span>
            <!-- Flecha sutil derecha -->
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" fill="none"
                 viewBox="0 0 24 24" stroke="white" stroke-width="2.5" opacity=".5" style="flex-shrink:0;">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/>
            </svg>
        </button>""" if can_report else f"""
        <div class="flex w-full bg-[#0053e2]/10 border-2 border-dashed border-[#0053e2]/30 text-[#0053e2] font-extrabold text-sm py-4 rounded-2xl items-center justify-center gap-2 uppercase tracking-wider">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            Toma un Hunt del listado!
        </div>"""

    # Pre-render hunts server-side — no depende de JS ni HTMX para el primer render
    initial_hunts_html = _build_hunts_html(user)

    # Render dashboard shell
    dashboard_html = f"""
    <!-- TOP METRICS ROW -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-2">

        <!-- CARD 1: ACTIVAS (azul) -->
        <div style="background:#fff;border-radius:16px;border:1px solid #f0f0f0;
                    box-shadow:0 2px 8px rgba(0,0,0,.05);padding:10px;
                    border-left:4px solid #0053e2;display:flex;flex-direction:column;gap:4px;">
            <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:10px;font-weight:600;color:#6b7280;">Activas</span>
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="#0053e2" stroke-width="1.8" opacity=".7">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
                </svg>
            </div>
            <div>
                <h3 id="metric-active-hunts"
                    style="font-size:36px;font-weight:900;color:#0053e2;line-height:1;margin:0;
                           transition:transform .2s;">...</h3>
                <p style="font-size:9.5px;color:#9ca3af;margin:1px 0 0;font-weight:500;">Alertas pendientes</p>
            </div>
            <div style="display:flex;align-items:center;gap:4px;">
                <span style="width:6px;height:6px;border-radius:50%;background:#16a34a;
                             animation:pulse 1.5s infinite;flex-shrink:0;"></span>
                <span style="font-size:9px;color:#16a34a;font-weight:600;">En vivo</span>
                <span id="kpi-last-update" style="font-size:8.5px;color:#9ca3af;margin-left:2px;"></span>
            </div>
        </div>

        <!-- CARD 2: EQUIPO (verde) -->
        <div style="background:#fff;border-radius:16px;border:1px solid #f0f0f0;
                    box-shadow:0 2px 8px rgba(0,0,0,.05);padding:10px;
                    border-left:4px solid #16a34a;"
             hx-get="/api/equipo-online"
             hx-trigger="load, every 10s"
             hx-swap="innerHTML">
            <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:10px;font-weight:600;color:#6b7280;">Equipo</span>
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="#16a34a" stroke-width="1.8" opacity=".7">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/>
                </svg>
            </div>
            <p style="font-size:9.5px;color:#9ca3af;margin:4px 0 0;">Cargando...</p>
        </div>

        <!-- CARD 3: CUMPLIMIENTO (dinamico) -->
        <div style="background:#fff;border-radius:16px;border:1px solid #f0f0f0;
                    box-shadow:0 2px 8px rgba(0,0,0,.05);padding:10px;
                    border-left:4px solid {completitud_color};display:flex;flex-direction:column;gap:4px;">
            <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:10px;font-weight:600;color:#6b7280;">Cumplimiento</span>
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="{completitud_color}" stroke-width="1.8" opacity=".7">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                </svg>
            </div>
            <div>
                <h3 style="font-size:36px;font-weight:900;color:{completitud_color};line-height:1;margin:0;
                           transition:color .4s;">{completitud_pct}%</h3>
                <p style="font-size:9.5px;color:#9ca3af;margin:1px 0 0;font-weight:500;">Meta 96% &nbsp;&middot;&nbsp; {resolved_hunts}/{total_hunts} hoy</p>
            </div>
            <div style="background:#f3f4f6;border-radius:999px;height:4px;overflow:hidden;">
                <div style="height:100%;width:{completitud_bar_w}%;background:{completitud_color};
                            border-radius:999px;transition:width .6s ease;"></div>
            </div>
        </div>

        <!-- CARD 4: CATEGORIAS FOCO (naranja) -->
        <div style="background:#fff;border-radius:16px;border:1px solid #f0f0f0;
                    box-shadow:0 2px 8px rgba(0,0,0,.05);padding:10px;
                    border-left:4px solid #E67E00;display:flex;flex-direction:column;gap:4px;">
            <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:10px;font-weight:600;color:#6b7280;">Categorias Foco</span>
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="#E67E00" stroke-width="1.8" opacity=".7">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"/>
                </svg>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:4px;">
                <span style="background:#fff7ed;color:#c2410c;font-size:9px;font-weight:700;
                             padding:3px 8px;border-radius:10px;border:1px solid #fed7aa;">Cafe</span>
                <span style="background:#faf5ff;color:#7e22ce;font-size:9px;font-weight:700;
                             padding:3px 8px;border-radius:10px;border:1px solid #e9d5ff;">Vinos</span>
                <span style="background:#eff6ff;color:#1d4ed8;font-size:9px;font-weight:700;
                             padding:3px 8px;border-radius:10px;border:1px solid #bfdbfe;">Checkout</span>
                <span style="background:#f0fdf4;color:#15803d;font-size:9px;font-weight:700;
                             padding:3px 8px;border-radius:10px;border:1px solid #bbf7d0;">Mascota</span>
            </div>
        </div>

    </div>

    {broadcast_panel}

    <!-- MAIN INTERACTIVE SECTION -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
        
        <!-- COLUMN 1: REPORT & LIVE FEEDS -->
        <div class="space-y-3 lg:col-span-1">
            {alerta_btn}

            <!-- LIVE ACTIVITY WALL -->
            <div class="bg-white rounded-2xl border border-gray-200 p-3 shadow-sm flex flex-col h-[240px] md:h-[360px]">
                <div class="flex items-center justify-between mb-2 pb-1.5 border-b border-gray-100">
                    <div class="flex items-center gap-2">
                        <span class="text-lg"></span>
                        <h2 class="text-xs font-black text-gray-900">Muro de Actividad</h2>
                    </div>
                    <span class="bg-blue-50 text-[#0053e2] font-black text-[8px] px-1.5 py-0.5 rounded uppercase tracking-wider animate-pulse">En Vivo</span>
                </div>
                
                <div id="feed-container" class="flex-1 overflow-y-auto custom-scrollbar space-y-1 pr-1"
                     hx-get="/api/feed" hx-trigger="load, reload-feed" hx-swap="innerHTML">
                    <div class="text-center py-4 text-gray-400 text-xs">Cargando actividad...</div>
                </div>
            </div>
        </div>

        <!-- COLUMN 2 & 3: ACTIVE HUNTS LIST -->
        <div class="lg:col-span-2 space-y-3">
            <div class="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm">
                <!-- Search & Filter bar -->
                <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-4">
                    <div>
                        <h2 class="text-sm font-black text-gray-900">Busquedas del Dia <span id="js-alive" style="font-size:9px;font-weight:400;color:#9ca3af;">JS...</span></h2>
                        <p class="text-[10px] text-gray-500 mt-0.5">Reportes activos que requieren stock en gondola</p>
                    </div>
                    
                    <div class="w-full sm:w-auto">
                        <input id="search-input" type="text" name="search" placeholder=" Buscar por item o descripcion..."
                               class="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-[#0053e2]"
                               oninput="loadHunts(this.value)" />
                    </div>
                </div>

                <!-- Hunts Container -->
                <div id="hunts-container" class="space-y-3">
                    {initial_hunts_html}
                </div>
            </div>
        </div>
    </div>
    """
    return HTMLResponse(content=render_template(dashboard_html, user=user, active_tab="dashboard"))

# LEADERBOARD PAGE
@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard_get(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    conn = get_db()

    # ── HUNTERS: ranking por puntos ────────────────────────────────────────
    hunters = conn.execute(
        "SELECT * FROM users WHERE role='hunter' ORDER BY points DESC"
    ).fetchall()

    # ── PICKERS: metricas de calidad desde hunts cerrados ─────────────────
    pickers = conn.execute(
        "SELECT * FROM users WHERE role='picker' ORDER BY full_name"
    ).fetchall()

    def picker_metrics(picker_name):
        """
        Logica de negocio:
          Encontrado + sala   = estaba en gondola, picker se EQUIVOCO
          Encontrado + bodega = hunter fue a buscar a bodega, picker ACERTO
          Sin Stock           = no habia en ninguna parte, picker ACERTO
          Ajustado            = hunter repuso desde bodega, picker ACERTO
          No Encontrado       = no se localizo nada, ambiguo
        """
        closed = ('Encontrado', 'No Encontrado', 'Sin Stock', 'Ajustado', 'Protocolo Confirmado', 'Protocolo Aplicado')
        ph = conn.execute(
            f"SELECT status, found_location FROM hunts WHERE reported_by=? AND status IN ({','.join('?'*len(closed))})",
            (picker_name, *closed)
        ).fetchall()
        total = len(ph)
        if total == 0:
            return None
        en_sala     = sum(1 for r in ph if r['status'] == 'Encontrado' and r['found_location'] == 'sala')
        en_bodega_f = sum(1 for r in ph if r['status'] == 'Encontrado' and r['found_location'] == 'bodega')
        en_gondola_legacy = sum(1 for r in ph if r['status'] == 'Encontrado' and r['found_location'] is None)
        a_bodega    = sum(1 for r in ph if r['status'] == 'Ajustado')
        sin_stock   = sum(1 for r in ph if r['status'] == 'Sin Stock')
        # No Encontrado, Protocolo Confirmado y Protocolo Aplicado son neutros
        no_enc = sum(1 for r in ph if r['status'] in ('No Encontrado', 'Protocolo Confirmado', 'Protocolo Aplicado'))
        # Error = encontrado en sala (el picker debia haberlo visto)
        errores     = en_sala + en_gondola_legacy
        # Acierto = encontrado en bodega + ajustado + sin stock
        aciertos    = en_bodega_f + a_bodega + sin_stock
        precision   = round(aciertos / total * 100) if total else 0
        pct_sala    = round(en_sala    / total * 100) if total else 0
        pct_bodega  = round((en_bodega_f + a_bodega) / total * 100) if total else 0
        pct_sinst   = round(sin_stock  / total * 100) if total else 0
        pct_noenc   = round(no_enc     / total * 100) if total else 0
        return dict(
            total=total,       precision=precision,
            en_sala=en_sala,   pct_sala=pct_sala,
            en_bodega_f=en_bodega_f, a_bodega=a_bodega, pct_bodega=pct_bodega,
            sin_stock=sin_stock, pct_sinst=pct_sinst,
            no_enc=no_enc,     pct_noenc=pct_noenc,
        )

    def pct_bar(pct, color):
        return f'<div style="background:#f3f4f6;border-radius:999px;height:5px;overflow:hidden;margin-top:3px;"><div style="width:{pct}%;background:{color};height:100%;border-radius:999px;"></div></div>'

    # ── HTML HUNTERS ───────────────────────────────────────────────────────
    medals = [' ', ' ', ' ']
    hunter_rows = ''
    for idx, u in enumerate(hunters):
        medal   = medals[idx] if idx < 3 else f'<span style="color:#9ca3af;font-weight:700;font-size:11px;">{idx+1}</span>'
        is_me   = 'background:#eff6ff;border-left:3px solid #0053e2;' if u['username'] == user['username'] else ''
        hunter_rows += f"""
        <tr style="border-bottom:1px solid #f3f4f6;{is_me}">
            <td style="padding:10px 12px;text-align:center;font-size:18px;">{medal}</td>
            <td style="padding:10px 12px;">
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:20px;">{u['avatar']}</span>
                    <div>
                        <p style="font-weight:700;font-size:12px;color:#111827;">{u['full_name']}</p>
                        <p style="font-size:9px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;">Hunter</p>
                    </div>
                </div>
            </td>
            <td style="padding:10px 12px;text-align:center;font-size:11px;color:#6b7280;font-weight:600;">{u['hunts_completed']} recuperaciones</td>
            <td style="padding:10px 12px;text-align:right;">
                <span style="background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:999px;font-size:10px;font-weight:800;"> {u['points']} pts</span>
            </td>
        </tr>"""

    if not hunter_rows:
        hunter_rows = '<tr><td colspan="4" style="text-align:center;padding:24px;color:#9ca3af;font-size:12px;">Sin datos aun</td></tr>'

    # ── HTML PICKERS ───────────────────────────────────────────────────────
    # Calcular metricas y ordenar por precision DESC
    picker_data = []
    for u in pickers:
        m = picker_metrics(u['full_name'])
        picker_data.append((u, m))
    picker_data.sort(key=lambda x: x[1]['precision'] if x[1] else -1, reverse=True)

    def mini_bar(pct, color):
        return f'<div style="background:#f3f4f6;border-radius:999px;height:4px;overflow:hidden;margin-top:2px;"><div style="width:{pct}%;background:{color};height:100%;border-radius:999px;"></div></div>'

    picker_rows = ''
    medals_p = [' ', ' ', ' ']
    for idx, (u, m) in enumerate(picker_data):
        medal  = medals_p[idx] if idx < 3 else f'<span style="color:#9ca3af;font-weight:700;font-size:11px;">{idx+1}</span>'
        is_me  = 'background:#fff7ed;border-left:3px solid #ea580c;' if u['username'] == user['username'] else ''

        if not m:
            picker_rows += f"""
            <tr style="border-bottom:1px solid #f3f4f6;{is_me}">
                <td style="padding:10px 12px;text-align:center;font-size:18px;">{medal}</td>
                <td style="padding:10px 12px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="font-size:20px;">{u['avatar']}</span>
                        <div>
                            <p style="font-weight:700;font-size:12px;color:#111827;">{u['full_name']}</p>
                            <p style="font-size:9px;color:#9ca3af;text-transform:uppercase;">Picker &middot; sin historial</p>
                        </div>
                    </div>
                </td>
                <td colspan="2" style="padding:10px 12px;font-size:10px;color:#d1d5db;text-align:center;">Sin datos aun</td>
            </tr>"""
            continue

        prec = m['precision']
        badge_bg  = '#eff6ff' if prec >= 80 else '#fef9c3' if prec >= 60 else '#fef2f2'
        badge_col = '#1d4ed8' if prec >= 80 else '#854d0e' if prec >= 60 else '#991b1b'
        badge_brd = '#bfdbfe' if prec >= 80 else '#fde68a' if prec >= 60 else '#fecaca'

        picker_rows += f"""
        <tr style="border-bottom:1px solid #f3f4f6;{is_me}">
            <td style="padding:12px;text-align:center;font-size:18px;">{medal}</td>
            <td style="padding:12px;min-width:140px;">
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:20px;">{u['avatar']}</span>
                    <div>
                        <p style="font-weight:700;font-size:12px;color:#111827;">{u['full_name']}</p>
                        <p style="font-size:9px;color:#9ca3af;text-transform:uppercase;">Picker &middot; {m['total']} solicitudes</p>
                    </div>
                </div>
            </td>
            <td style="padding:12px;min-width:180px;">
                <div style="margin-bottom:6px;">
                    <div style="display:flex;justify-content:space-between;">
                        <span style="font-size:9px;color:#6b7280;">Encontrado en bodega</span>
                        <span style="font-size:10px;font-weight:800;color:#1d4ed8;">{m['pct_bodega']}% <span style="font-size:8px;font-weight:400;color:#9ca3af;">({m['en_bodega_f'] + m['a_bodega']})</span></span>
                    </div>
                    {mini_bar(m['pct_bodega'], '#60a5fa')}
                </div>
                <div>
                    <div style="display:flex;justify-content:space-between;">
                        <span style="font-size:9px;color:#6b7280;">Encontrado en sala</span>
                        <span style="font-size:10px;font-weight:800;color:#92400e;">{m['pct_sala']}% <span style="font-size:8px;font-weight:400;color:#9ca3af;">({m['en_sala']})</span></span>
                    </div>
                    {mini_bar(m['pct_sala'], '#fbbf24')}
                </div>
            </td>
            <td style="padding:12px;text-align:right;">
                <span style="background:{badge_bg};color:{badge_col};border:1px solid {badge_brd};font-size:11px;font-weight:900;padding:5px 12px;border-radius:999px;">{prec}%</span>
            </td>
        </tr>"""

    if not picker_rows:
        picker_rows = '<tr><td colspan="4" style="text-align:center;padding:24px;color:#9ca3af;font-size:12px;">Sin datos aun</td></tr>'

    leaderboard_html = f"""
    <div class="max-w-4xl mx-auto space-y-6">

        <!-- HUNTERS -->
        <div style="background:white;border-radius:20px;border:1px solid #e5e7eb;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);">
            <div style="background:#0053e2;padding:20px 24px;display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <h2 style="color:white;font-size:16px;font-weight:900;"> Ranking Hunters</h2>
                    <p style="color:rgba(255,255,255,.65);font-size:11px;margin-top:2px;">Puntos por recuperaciones exitosas</p>
                </div>
                <span style="background:rgba(255,255,255,.15);color:white;font-size:10px;font-weight:700;padding:4px 12px;border-radius:999px;">1 pto por Encontrado</span>
            </div>
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#f9fafb;border-bottom:1px solid #f3f4f6;">
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:center;">#</th>
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:left;">Hunter</th>
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:center;">Recuperaciones</th>
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:right;">Puntos</th>
                    </tr>
                </thead>
                <tbody>{hunter_rows}</tbody>
            </table>
        </div>

        <!-- PICKERS -->
        <div style="background:white;border-radius:20px;border:1px solid #e5e7eb;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);">
            <div style="background:#ea580c;padding:20px 24px;">
                <h2 style="color:white;font-size:16px;font-weight:900;">&#128269; Desempeno Pickers</h2>
                <p style="color:rgba(255,255,255,.65);font-size:11px;margin-top:2px;">Evaluacion basada en el resultado que reporta el Hunter &mdash; no en volumen de casos</p>
            </div>
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#f9fafb;border-bottom:1px solid #f3f4f6;">
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:center;">#</th>
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:left;">Picker</th>
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:left;">KPIs</th>
                        <th style="padding:8px 12px;font-size:9px;color:#9ca3af;font-weight:700;text-transform:uppercase;text-align:right;">Acierto</th>
                    </tr>
                </thead>
                <tbody>{picker_rows}</tbody>
            </table>
        </div>

    </div>
    """
    conn.close()
    return HTMLResponse(content=render_template(leaderboard_html, user=user, active_tab="leaderboard"))

# STATS & CHARTS PAGE
@app.get("/stats", response_class=HTMLResponse)
def stats_get(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
        
    stats_html = """
    <div class="space-y-6">
        <div class="bg-white rounded-2xl border border-gray-200 p-4 shadow-sm">
            <h2 class="text-base font-black text-gray-900 mb-0.5">Desempeno Operacional - Tienda 99</h2>
            <p class="text-[10px] text-gray-500">Metricas clave de On-Shelf Availability (OSA) y Picker Hunt en tiempo real.</p>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <!-- CHART 1 -->
            <div class="bg-white rounded-2xl border border-gray-200 p-4 shadow-sm">
                <h3 class="text-xs font-bold text-gray-800 mb-3">Hunts Resueltos por Dia</h3>
                <div class="h-[240px]">
                    <canvas id="chart-daily"></canvas>
                </div>
            </div>

            <!-- CHART 2 -->
            <div class="bg-white rounded-2xl border border-gray-200 p-4 shadow-sm">
                <h3 class="text-xs font-bold text-gray-800 mb-3">Faltantes por Categoria</h3>
                <div class="h-[240px]">
                    <canvas id="chart-categories"></canvas>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Setup Chart 1
        const ctx1 = document.getElementById('chart-daily').getContext('2d');
        new Chart(ctx1, {
            type: 'line',
            data: {
                labels: ['Lunes', 'Martes', 'Miercoles', 'Jueves', 'Viernes', 'Sabado', 'Domingo'],
                datasets: [{
                    label: 'Productos Encontrados',
                    data: [12, 19, 15, 24, 22, 30, 28],
                    borderColor: '#0053e2',
                    backgroundColor: 'rgba(0, 83, 226, 0.1)',
                    borderWidth: 3,
                    fill: true,
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: { beginAtZero: true }
                }
            }
        });

        // Setup Chart 2
        const ctx2 = document.getElementById('chart-categories').getContext('2d');
        new Chart(ctx2, {
            type: 'doughnut',
            data: {
                labels: ['Lacteos', 'Abarrotes', 'Limpieza', 'Congelados', 'Perfumeria'],
                datasets: [{
                    data: [35, 25, 20, 12, 8],
                    backgroundColor: ['#0053e2', '#ffc220', '#2a8703', '#ea1100', '#9c27b0'],
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'right', labels: { boxWidth: 10, font: { size: 9 } } }
                }
            }
        });
    </script>
    """
    return HTMLResponse(content=render_template(stats_html, user=user, active_tab="stats"))

# ─────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS FOR HTMX & DYNAMIC RELOAD
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/stats-json")
def api_stats_json():
    conn = get_db()
    active = conn.execute("SELECT COUNT(*) FROM hunts WHERE status IN ('Buscando', 'Yendo')").fetchone()[0]
    found = conn.execute("SELECT COUNT(*) FROM hunts WHERE status = 'Encontrado'").fetchone()[0]
    conn.close()
    return {"active": active, "found": found}

@app.get("/api/feed")
def api_feed(request: Request):
    viewer = get_current_user(request)
    is_supervisor_viewing = viewer and viewer['role'] == 'supervisor'

    conn = get_db()
    items = conn.execute("SELECT * FROM feed ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()

    html = ""
    for item in items:
        msg     = item['message'].replace('"', '&quot;').replace("'", "&#39;")
        feed_id = item['id']
        is_sup  = item['is_supervisor']

        if is_sup:
            card_style = 'style="background:rgba(0,83,226,0.12);border:1px solid transparent;border-radius:12px;padding:10px;animation:feedPulse 2.5s ease-in-out infinite;"'
            text_style = 'style="font-size:11px;color:#003fa3;font-weight:700;line-height:1.5;"'
            label_html = '<span style="font-size:8px;font-weight:900;color:#0053e2;text-transform:uppercase;letter-spacing:0.1em;"> Supervisor</span>'
        elif item['is_hunter']:
            card_style = 'style="background:rgba(234,179,8,0.10);border:1px solid transparent;border-radius:12px;padding:10px;animation:feedPulseHunter 2.5s ease-in-out infinite;"'
            text_style = 'style="font-size:11px;color:#78350f;font-weight:700;line-height:1.5;"'
            label_html = '<span style="font-size:8px;font-weight:900;color:#d97706;text-transform:uppercase;letter-spacing:0.1em;"> Hunter</span>'
        else:
            card_style = 'style="background:#f9fafb;border:1px solid #f3f4f6;border-radius:12px;padding:10px;"'
            text_style = 'style="font-size:11px;color:#374151;font-weight:500;line-height:1.5;"'
            label_html = '<span style="font-size:8px;color:#9ca3af;">Hace unos instantes</span>'

        # Boton editar solo para supervisor viendo sus propios mensajes
        edit_btn = ''
        if is_sup and is_supervisor_viewing:
            edit_btn = f'''
            <button onclick="feedEditStart({feed_id}, this)"
                title="Editar"
                style="flex-shrink:0;background:none;border:none;cursor:pointer;padding:2px 4px;opacity:.5;font-size:13px;line-height:1;"
                onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=.5">
                &#9998;
            </button>'''

        html += f"""
        <div id="feed-item-{feed_id}" class="flex items-start gap-2 transition duration-150 hover:opacity-90" {card_style}>
            <div style="flex:1;min-width:0;">
                <p id="feed-text-{feed_id}" {text_style}>{msg}</p>
                <span style="display:block;margin-top:2px;">{label_html}</span>
            </div>
            {edit_btn}
        </div>
        """
    return HTMLResponse(content=html)


@app.get("/api/equipo-online")
def api_equipo_online(request: Request):
    """Retorna el HTML del recuadro Equipo con usuarios picker/hunter conectados en vivo."""
    if not get_current_user(request):
        return HTMLResponse(content="")

    online = manager.online_usernames()
    conn = get_db()
    members = conn.execute(
        "SELECT username, full_name, role FROM users "
        "WHERE role IN ('picker','hunter') ORDER BY role, full_name"
    ).fetchall()
    conn.close()

    _ROLE_STYLE = {
        'picker': ('background:#eff6ff;color:#1d4ed8;', 'Picker'),
        'hunter': ('background:#f0fdf4;color:#15803d;', 'Hunter'),
    }
    online_count = 0
    rows_html = ''
    for m in members:
        is_online = m['username'] in online
        if is_online:
            online_count += 1
        dot = ('<span style="width:7px;height:7px;border-radius:50%;'
               'background:#16a34a;display:inline-block;flex-shrink:0;animation:pulse 2s infinite;"></span>'
               if is_online else
               '<span style="width:7px;height:7px;border-radius:50%;'
               'background:#d1d5db;display:inline-block;flex-shrink:0;"></span>')
        opacity = '1.0' if is_online else '0.4'
        sty, label = _ROLE_STYLE[m['role']]
        rows_html += (
            f'<div style="display:flex;align-items:center;gap:6px;opacity:{opacity};'
            f'padding:3px 0;">'
            f'{dot}'
            f'<span style="font-size:10.5px;font-weight:600;color:#111827;flex:1;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{m["full_name"]}</span>'
            f'<span style="{sty}font-size:8.5px;font-weight:700;padding:2px 7px;border-radius:8px;flex-shrink:0;">'
            f'{label}</span></div>'
        )
    if not rows_html:
        rows_html = '<span style="font-size:10px;color:#9ca3af;">Sin integrantes</span>'

    total_members = len(members)
    html = (
        f'<div style="display:flex;align-items:center;justify-content:space-between;">'
        f'<span style="font-size:10px;font-weight:600;color:#6b7280;">Equipo</span>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="#16a34a" stroke-width="1.8" opacity=".7">'
        f'<path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/>'
        f'</svg></div>'
        f'<div style="margin:4px 0 2px;">'
        f'<h3 style="font-size:36px;font-weight:900;color:#16a34a;line-height:1;margin:0;">'
        f'{online_count}</h3>'
        f'<p style="font-size:9.5px;color:#9ca3af;margin:1px 0 0;font-weight:500;">'
        f'de {total_members} conectados</p>'
        f'</div>'
    )
    return HTMLResponse(content=html)

@app.get("/api/hunts-list")
def api_hunts_list(request: Request, search: str = ""):
    user = get_current_user(request)
    if not user:
        return HTMLResponse(content="<p class='text-xs text-red-500'>Sesion expirada</p>")
    return HTMLResponse(content=_build_hunts_html(user, search))


def _build_historial_html(hist_rows: list) -> str:
    """HTML del bloque 'Mis ultimas alertas' para el picker (ultimas 3 resueltas)."""
    STATUS_STYLE = {
        'Encontrado':           ('background:#dcfce7;color:#166534;', 'Encontrado'),
        'Ajustado':             ('background:#ccfbf1;color:#0f766e;', 'Ajustado'),
        'Sin Stock':            ('background:#fee2e2;color:#991b1b;', 'Sin Stock'),
        'No Encontrado':        ('background:#fee2e2;color:#991b1b;', 'No Encontrado'),
        'Protocolo Confirmado': ('background:#dcfce7;color:#166534;', 'Protocolo OK'),
        'Protocolo Aplicado':   ('background:#f3f4f6;color:#374151;', 'Protocolo Aplicado'),
    }
    hist_cards = ''
    for h in hist_rows:
        sty, label = STATUS_STYLE.get(h['status'], ('background:#f3f4f6;color:#374151;', h['status']))
        hunter_line = (f"<span style='font-size:10px;color:#6b7280;'>Hunter: {h['assigned_to']}</span>"
                       if h['assigned_to'] else '')
        delta_line = ''
        if h['status'] == 'Ajustado' and h['inventory_delta'] is not None:
            sign = '+' if h['inventory_delta'] >= 0 else ''
            delta_line = (f"<span style='font-size:10px;color:#0f766e;font-weight:700;'>"
                          f"{sign}{h['inventory_delta']} un.</span>")
        # Thumbnail foto de ubicacion (solo si encontrado en sala con foto)
        loc_photo = h['location_photo'] if 'location_photo' in h.keys() else None
        photo_thumb = ''
        if h['status'] == 'Encontrado' and loc_photo:
            photo_thumb = (
                f"<img src='{loc_photo}' onclick=\"showPhotoLightBox('{loc_photo}')\""
                f" style='width:38px;height:38px;object-fit:cover;border-radius:8px;"
                f"border:2px solid #16a34a;cursor:zoom-in;flex-shrink:0;' title='Ver foto de ubicacion' />"
            )
        # Boton check retiro (solo Encontrado/Ajustado, badge si ya confirmo)
        retrieved = h['picker_retrieved_at'] if 'picker_retrieved_at' in h.keys() else None
        check_btn = ''
        if h['status'] in ('Encontrado', 'Ajustado'):
            if retrieved:
                check_btn = "<span style='flex-shrink:0;background:#dcfce7;color:#166534;font-size:9px;font-weight:900;padding:3px 8px;border-radius:999px;border:1px solid #bbf7d0;'>Retirado</span>"
            else:
                check_btn = (
                    f"<button onclick=\"pickerConfirm({h['id']})\""
                    f" style='flex-shrink:0;width:34px;height:34px;border-radius:50%;border:2px solid #16a34a;"
                    f"background:white;cursor:pointer;display:flex;align-items:center;justify-content:center;"
                    f"animation:pulse 1.5s infinite;' title='Confirmar retiro'>"
                    f"<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none' viewBox='0 0 24 24' stroke='#16a34a' stroke-width='3'>"
                    f"<path stroke-linecap='round' stroke-linejoin='round' d='M5 13l4 4L19 7'/></svg>"
                    f"</button>"
                )
        hist_cards += f"""
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;
                    padding:10px 12px;background:white;border-radius:10px;border:1px solid #e5e7eb;">
            <div style="flex:1;min-width:0;">
                <p style="font-size:11px;font-weight:700;color:#111827;
                          white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:0;">{h['item_name']}</p>
                <div style="display:flex;gap:6px;align-items:center;margin-top:2px;">
                    <span style="font-size:10px;color:#6b7280;">{h['quantity']} un.</span>
                    {hunter_line}
                    {delta_line}
                </div>
            </div>
            {photo_thumb}
            {check_btn}
            <span style="flex-shrink:0;{sty}font-size:9px;font-weight:900;
                         padding:3px 8px;border-radius:999px;white-space:nowrap;">{label}</span>
        </div>"""
    return f"""
    <div style="margin-top:16px;padding:0 2px;">
        <p style="font-size:10px;font-weight:900;color:#6b7280;text-transform:uppercase;
                  letter-spacing:.08em;margin:0 0 8px;">Mis ultimas 3 alertas</p>
        <div style="display:flex;flex-direction:column;gap:6px;">
            {hist_cards}
        </div>
    </div>"""


def _build_hunts_html(user: dict, search: str = "") -> str:
    """Construye el HTML de la lista de hunts. Llamado por el route y por el dashboard."""
    can_hunt   = user['role'] in ('hunter', 'supervisor')
    can_report = user['role'] in ('picker', 'supervisor')

    conn = get_db()
    # Solo hunts activos (no resueltos) + los del dia actual del usuario
    today_start = int(__import__('datetime').datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
    ACTIVE = "status IN ('Buscando','Yendo','Protocolo')"
    # Pickers solo ven sus propios reportes
    if user['role'] == 'picker':
        if search:
            query = f"SELECT * FROM hunts WHERE reported_by=? AND ({ACTIVE}) AND (item_name LIKE ? OR barcode LIKE ?)"
            params = [user['full_name'], f"%{search}%", f"%{search}%"]
        else:
            query = f"SELECT * FROM hunts WHERE reported_by=? AND ({ACTIVE})"
            params = [user['full_name']]
    else:
        query = f"SELECT * FROM hunts WHERE {ACTIVE}"
        params = []
        if search:
            query += " AND (item_name LIKE ? OR barcode LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
    query += (" ORDER BY CASE"
              " WHEN status='Buscando' THEN 1"
              " WHEN status='Yendo' THEN 2"
              " WHEN status='No Encontrado' THEN 3"
              " WHEN status='Ajustado' THEN 4"
              " ELSE 5 END,"
              " CASE WHEN status IN ('Buscando','Yendo') THEN quantity ELSE 0 END DESC,"
              " id DESC")
    
    hunts = conn.execute(query, params).fetchall()

    # Historial picker: ultimas 3 resueltas (mientras conn esta abierta)
    hist_rows = []
    if user['role'] == 'picker':
        RESOLVED = "status NOT IN ('Buscando','Yendo','Protocolo')"
        hist_rows = conn.execute(
            f"SELECT * FROM hunts WHERE reported_by=? AND {RESOLVED} ORDER BY id DESC LIMIT 3",
            (user['full_name'],)
        ).fetchall()

    conn.close()

    if not hunts:
        empty_html = """
        <div class="text-center py-8 text-gray-400">
            <p class="text-xs font-bold">Todo despejado por aqui!</p>
            <p class="text-[10px] mt-0.5">No hay busquedas activas con ese criterio.</p>
        </div>
        """
        # Picker: aunque no haya activos, mostrar historial de las 3 ultimas
        if user['role'] == 'picker' and hist_rows:
            return empty_html + _build_historial_html(hist_rows)
        return empty_html
        
    html = ""
    for hunt in hunts:
        status = hunt['status']
        photo_html = ""
        
        if hunt['photo']:
            photo_html = f"""
            <div class="w-12 h-12 rounded-lg bg-gray-100 overflow-hidden flex-shrink-0 border border-gray-200 cursor-pointer self-start md:self-center" onclick="showPhotoLightBox('{hunt['id']}')">
                <img id="hunt-img-{hunt['id']}" src="{hunt['photo']}" class="w-full h-full object-cover" />
            </div>
            """
        
        if status == 'Buscando':
            status_badge = '<span class="bg-blue-50 text-[#0053e2] text-[10px] font-black px-2 py-0.5 rounded-full border border-blue-100"> Pendiente</span>'
        elif status == 'Yendo':
            status_badge = f'<span class="bg-yellow-50 text-[#995213] text-[10px] font-black px-2 py-0.5 rounded-full border border-yellow-100"> Buscando ({hunt["assigned_to"]})</span>'
        elif status == 'Protocolo':
            assignee = hunt['assigned_to'] or 'Sin hunter'
            status_badge = f'<span style="background:#fff7ed;color:#c2410c;font-size:10px;font-weight:900;padding:2px 8px;border-radius:999px;border:1px solid #fed7aa;">Protocolo ({assignee})</span>'
        elif status == 'Protocolo Confirmado':
            status_badge = '<span style="background:#f0fdf4;color:#166534;font-size:10px;font-weight:900;padding:2px 8px;border-radius:999px;border:1px solid #bbf7d0;">Protocolo Confirmado</span>'
        elif status == 'Protocolo Aplicado':
            status_badge = '<span style="background:#fafafa;color:#525252;font-size:10px;font-weight:900;padding:2px 8px;border-radius:999px;border:1px solid #d4d4d4;">Protocolo Aplicado</span>'
        elif status == 'Encontrado':
            status_badge = '<span class="bg-green-50 text-[#2a8703] text-[10px] font-black px-2 py-0.5 rounded-full border border-green-100"> Encontrado</span>'
        elif status == 'No Encontrado':
            status_badge = '<span class="bg-orange-50 text-orange-700 text-[10px] font-black px-2 py-0.5 rounded-full border border-orange-200"> No Encontrado</span>'
        elif status == 'Ajustado':
            delta = hunt['inventory_delta']
            delta_str = (f'+{delta}' if delta and delta >= 0 else str(delta)) if delta is not None else ''
            status_badge = f'<span class="bg-teal-50 text-teal-700 text-[10px] font-black px-2 py-0.5 rounded-full border border-teal-200"> Ajustado {delta_str} un.</span>'
        else:
            status_badge = '<span class="bg-red-50 text-[#ea1100] text-[10px] font-black px-2 py-0.5 rounded-full border border-red-100"> Sin Stock</span>'

        is_my_report = hunt['reported_by'] == user['full_name']
        is_assigned_to_me = hunt['assigned_to'] == user['full_name']

        # ── Role-based actions ──────────────────────────────────────────────
        item_name_safe = hunt['item_name'].replace("'", "\\'")
        actions = ""
        if status == 'Buscando':
            if can_hunt:
                actions = f"""
                <button hx-post="/api/hunts/{hunt['id']}/claim" hx-target="#hunts-container"
                        class="bg-[#0053e2] hover:bg-blue-700 text-white text-[10px] font-bold px-3 py-1.5 rounded-lg transition flex items-center gap-1 w-full md:w-auto justify-center">
                     Tomar Hunt
                </button>"""
            elif is_my_report:
                actions = "<span class='text-[10px] text-[#0053e2] font-bold bg-blue-50 px-2 py-1 rounded-lg'>Tu reporte</span>"
            else:
                actions = "<span class='text-[10px] text-gray-400 italic'>Esperando hunter...</span>"

        elif status == 'Protocolo':
            if can_hunt or user['role'] == 'supervisor':
                actions = f"""
                <button hx-post="/api/hunts/{hunt['id']}/confirmar-protocolo"
                        hx-target="#hunts-container"
                        style="background:#c2410c;color:white;font-size:10px;font-weight:700;padding:8px 12px;border-radius:8px;border:none;cursor:pointer;width:100%;text-align:center;">
                    Confirmar Protocolo
                </button>"""
            elif is_my_report:
                actions = "<span style='font-size:10px;color:#c2410c;font-weight:700;background:#fff7ed;padding:4px 8px;border-radius:6px;border:1px solid #fed7aa;'>Protocolo aplicado — esperando confirmacion</span>"

        elif status == 'Yendo':
            if can_hunt and is_assigned_to_me:
                actions = f"""
                <div style="display:flex;gap:6px;width:100%;">
                    <button onclick="openSalaPhotoModal({hunt['id']}, '{item_name_safe}')"
                            style="background:#2a8703;color:white;font-size:10px;font-weight:700;padding:8px 6px;border-radius:8px;border:none;cursor:pointer;flex:1;text-align:center;line-height:1.2;">
                         En sala
                    </button>
                    <button hx-post="/api/hunts/{hunt['id']}/found"
                            hx-target="#hunts-container"
                            hx-vals='{{"found_location":"bodega"}}'
                            style="background:#2563eb;color:white;font-size:10px;font-weight:700;padding:8px 6px;border-radius:8px;border:none;cursor:pointer;flex:1;text-align:center;line-height:1.2;">
                        En bodega
                    </button>
                    <button hx-post="/api/hunts/{hunt['id']}/no-stock"
                            hx-target="#hunts-container"
                            style="background:#ea1100;color:white;font-size:10px;font-weight:700;padding:8px 6px;border-radius:8px;border:none;cursor:pointer;flex:1;text-align:center;line-height:1.2;">
                        No hay
                    </button>
                </div>"""
            else:
                if is_my_report and user['role'] == 'picker':
                    actions = f"<span class='text-[10px] text-orange-700 font-bold bg-orange-50 px-2 py-1 rounded-lg'>Buscando: {hunt['assigned_to']}</span>"
                else:
                    actions = f"<span class='text-[10px] text-gray-400 italic text-right block w-full'>Asignado a {hunt['assigned_to']}</span>"
        
        is_active = status in ('Buscando', 'Yendo', 'Protocolo')

        # Unix epoch entero - timezone-agnostic, el JS usa Date.now() que tambien es UTC ms
        import calendar
        reported_unix = ''
        if is_active and hunt['reported_at']:
            import datetime as _dt
            reported_dt  = _dt.datetime.strptime(hunt['reported_at'], '%Y-%m-%d %H:%M:%S')
            reported_unix = str(int(calendar.timegm(reported_dt.timetuple())))

        data_attrs = f'data-reported-unix="{reported_unix}" data-hunt-id="{hunt["id"]}" data-user-role="{user["role"]}"' if is_active else ''

        # ── Phase-3 resolution buttons (hunter/supervisor, active hunts only) ──
        phase3_html = ''
        if is_active and can_hunt:
            phase3_html = f"""
            <div class="hunt-phase3-zone hidden border-t border-red-100 pt-2.5 mt-0.5">
                <p class="text-[9px] text-red-600 font-bold uppercase tracking-wider mb-2"> Resolución requerida</p>
                <div class="flex gap-2">
                    <button onclick="openNotFoundModal({hunt['id']}, '{item_name_safe}')"
                            class="flex-1 bg-red-50 hover:bg-red-100 text-red-700 text-[10px] font-bold px-3 py-2 rounded-lg transition border border-red-200 flex items-center justify-center gap-1">
                         No Encontrado
                    </button>
                    <button onclick="openAdjustModal({hunt['id']}, '{item_name_safe}', {hunt['quantity']})"
                            class="flex-1 bg-teal-50 hover:bg-teal-100 text-teal-700 text-[10px] font-bold px-3 py-2 rounded-lg transition border border-teal-200 flex items-center justify-center gap-1">
                         Ajustar Inventario
                    </button>
                </div>
            </div>"""

        # ── Phase-3 picker: boton "Aplico Protocolo" (Buscando o Yendo, timer vencido) ──
        picker_phase3_html = ''
        if is_active and is_my_report and status in ('Buscando', 'Yendo') and user['role'] == 'picker':
            picker_phase3_html = f"""
            <div class="hunt-picker-phase3-zone hidden border-t border-orange-100 pt-2.5 mt-0.5">
                <p class="text-[9px] text-orange-600 font-bold uppercase tracking-wider mb-1.5">Tiempo vencido sin resolucion</p>
                <button onclick="aplicoProtocolo({hunt['id']}, '{hunt['item_name'].replace("'", "")}')"
                        style="width:100%;background:#ea580c;color:white;font-size:10px;font-weight:800;padding:9px 12px;border-radius:8px;border:none;cursor:pointer;text-align:center;">
                    Aplico Protocolo
                </button>
            </div>"""

        # ── Phase-2 warn button: solo picker que reporto el hunt Yendo ──────
        picker_phase2_warn = ''
        if is_active and status == 'Yendo' and is_my_report and user['role'] == 'picker':
            picker_phase2_warn = f"""
                <div class="mt-1.5 pt-1.5 border-t border-orange-100">
                    <p class="text-[9px] text-orange-700 font-bold mb-1">Hunter: {hunt['assigned_to']}</p>
                    <button onclick="warnHunter({hunt['id']})"
                            style="width:100%;background:#ea580c;color:white;font-size:10px;font-weight:800;padding:8px 12px;border-radius:8px;border:none;cursor:pointer;text-align:center;">
                        Avisar Hunter
                    </button>
                </div>"""

        # ── Timer zone (only for active hunts) ────────────────────────────────
        timer_inline_html = ''
        timer_zone_html   = ''
        if is_active:
            # badge inline junto a "Reportado por" - inline style para bypasear CDN cache
            timer_inline_html = '<span class="hunt-timer-label" style="font-size:10px;font-family:monospace;font-weight:700;background:#16a34a;color:white;padding:2px 8px;border-radius:9999px;white-space:nowrap;">15:00</span>'
            timer_zone_html = f"""
            <div class="px-3.5 pb-3 flex flex-col gap-1.5">
                <div class="hunt-phase2-zone hidden">
                    <div class="h-2 bg-orange-100 rounded-full overflow-hidden">
                        <div class="hunt-progress-bar h-full bg-gradient-to-r from-red-500 to-red-700 rounded-full" style="width:100%;"></div>
                    </div>
                    <p class="text-[9px] text-orange-600 mt-0.5 font-semibold">Tiempo adicional activo — resolucion pronto</p>
                    {picker_phase2_warn}
                </div>
                {phase3_html}
                {picker_phase3_html}
            </div>"""

        # -- Prioridad por cantidad (solo hunts activos) ------------------
        qty = hunt['quantity'] or 0
        if is_active and qty >= 10:
            priority_level = 'high'
            card_border    = 'border-orange-300 shadow-orange-100'
            card_bg        = 'background:linear-gradient(135deg,#fff7ed 0%,#ffffff 60%);'
            priority_badge = ('<span style="background:#ea580c;color:white;font-size:9px;'
                             'font-weight:900;padding:2px 7px;border-radius:9999px;'
                             'letter-spacing:.05em;white-space:nowrap;">'
                             ' ALTA PRIORIDAD</span>')
            qty_style      = 'font-weight:900;color:#ea580c;'
        elif is_active and qty >= 5:
            priority_level = 'mid'
            card_border    = 'border-yellow-200 shadow-yellow-50'
            card_bg        = 'background:linear-gradient(135deg,#fefce8 0%,#ffffff 50%);'
            priority_badge = ('<span style="background:#ca8a04;color:white;font-size:9px;'
                             'font-weight:900;padding:2px 7px;border-radius:9999px;'
                             'letter-spacing:.05em;white-space:nowrap;">'
                             ' PRIORIDAD</span>')
            qty_style      = 'font-weight:800;color:#ca8a04;'
        else:
            priority_level = 'normal'
            card_border    = 'border-gray-100 hover:border-gray-200'
            card_bg        = ''
            priority_badge = ''
            qty_style      = ''

        html += f"""
        <div class="bg-white rounded-2xl border hover:shadow-sm overflow-hidden {card_border}" style="transition:border-color .15s,box-shadow .15s;contain:layout style;{card_bg}" {data_attrs}>
            <div class="p-3.5 flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3">
                <div class="flex items-center gap-3 min-w-0">
                    {photo_html}
                    <div class="min-w-0">
                        <div class="flex items-center gap-2 flex-wrap mb-1">
                            <h4 class="text-xs font-black text-gray-900 leading-tight truncate">{hunt['item_name']}</h4>
                            {status_badge}
                            {priority_badge}
                            {timer_inline_html}
                        </div>
                        <div class="flex items-center gap-x-2 gap-y-0.5 flex-wrap text-[10px] text-gray-500">
                            <span class="font-bold text-[#0053e2]"> Item: {hunt['barcode']}</span>
                            <span>•</span>
                            <span style="{qty_style}">Cant: {hunt['quantity']} un.</span>
                            <span>•</span>
                            <span>Reportado por: {hunt['reported_by']}</span>
                        </div>
                    </div>
                </div>
                <div class="flex flex-col items-end gap-2 w-full md:w-auto justify-end border-t md:border-t-0 pt-2.5 md:pt-0">
                    <div class="flex items-center gap-2 w-full md:w-auto justify-end">
                        {actions}
                    </div>
                </div>
            </div>
            {timer_zone_html}
        </div>
        """
        
    # ── Historial ultimas 3 alertas (solo picker) ───────────────────────────
    if user['role'] == 'picker' and hist_rows:
        html += _build_historial_html(hist_rows)

    html += """
    <div id="image-lightbox" class="fixed inset-0 bg-black/90 z-50 hidden flex items-center justify-center p-4" onclick="this.classList.add('hidden')">
        <img id="lightbox-img" class="max-w-full max-h-[85vh] rounded-xl object-contain shadow-2xl" />
    </div>
    """
    return html


@app.post("/api/hunts")
async def api_create_hunt(
    request: Request,
    item_name: str = Form(...),
    barcode: str = Form(...),
    quantity: int = Form(...),
    photo_camera: UploadFile = File(None),
    photo_gallery: UploadFile = File(None)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'hunter':
        raise HTTPException(status_code=403, detail="Hunters no pueden reportar faltantes")
        
    # Pick whichever photo was uploaded
    photo = photo_camera if (photo_camera and photo_camera.filename) else photo_gallery
    photo_base64 = None
    
    if photo and photo.filename:
        file_bytes = await photo.read()
        if len(file_bytes) > 0:
            content_type = photo.content_type or "image/jpeg"
            encoded = base64.b64encode(file_bytes).decode("utf-8")
            photo_base64 = f"data:{content_type};base64,{encoded}"
        
    conn = get_db()
    cursor = conn.cursor()

    # ── Detectar hunt activo con mismo barcode ──────────────────────────
    existing = cursor.execute("""
        SELECT * FROM hunts
        WHERE barcode = ? AND status IN ('Buscando','Yendo')
        ORDER BY id DESC LIMIT 1
    """, (barcode,)).fetchone()

    if existing:
        # Merge: sumar unidades al hunt existente
        new_qty = existing['quantity'] + quantity
        cursor.execute(
            "UPDATE hunts SET quantity = ? WHERE id = ?",
            (new_qty, existing['id'])
        )
        cursor.execute(
            "INSERT INTO feed (message) VALUES (?)",
            (f" {user['full_name']} sumo {quantity} un. adicionales a '{item_name}' "
             f"(total: {new_qty} un.).",)
        )
        conn.commit()

        # Notificar al hunter si ya esta asignado
        hunter_username = None
        if existing['status'] == 'Yendo' and existing['assigned_to']:
            hunter_row = cursor.execute(
                "SELECT username FROM users WHERE full_name = ?",
                (existing['assigned_to'],)
            ).fetchone()
            hunter_username = hunter_row['username'] if hunter_row else None

        conn.close()
        await manager.broadcast("refresh")

        if hunter_username:
            await manager.notify_user(
                hunter_username,
                f"mas-unidades:{item_name}|{quantity}|{new_qty}|{user['full_name']}"
            )

        return api_hunts_list(request)

    # ── Sin duplicado: crear hunt nuevo ─────────────────────────────────
    # Save the new hunt (defaulting aisle to 'General')
    cursor.execute("""
    INSERT INTO hunts (item_name, barcode, aisle, quantity, reported_by, photo)
    VALUES (?, ?, 'General', ?, ?, ?)
    """, (item_name, barcode, quantity, user['full_name'], photo_base64))
    
    # Add to feed
    cursor.execute("""
    INSERT INTO feed (message)
    VALUES (?)
    """, (f" {user['full_name']} reporto faltante de {item_name}.",))
    
    conn.commit()
    conn.close()
    
    # Broadcast refresh signal to all connected clients
    await manager.broadcast("refresh")
    
    # Trigger refresh of hunts list
    return api_hunts_list(request)

@app.post("/api/hunts/{hunt_id}/claim")
async def api_claim_hunt(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'picker':
        raise HTTPException(status_code=403, detail="Pickers no pueden tomar hunts")

    conn = get_db()
    cursor = conn.cursor()

    # Solo se puede tomar si sigue en estado Buscando (evita double-claim)
    hunt = cursor.execute(
        "SELECT * FROM hunts WHERE id = ? AND status = 'Buscando'", (hunt_id,)
    ).fetchone()
    if hunt:
        cursor.execute(
            "UPDATE hunts SET status = 'Yendo', assigned_to = ? WHERE id = ? AND status = 'Buscando'",
            (user['full_name'], hunt_id),
        )
        if cursor.rowcount > 0:
            cursor.execute(
                "INSERT INTO feed (message) VALUES (?)",
                (f" {user['full_name']} tomo el hunt de {hunt['item_name']}!",),
            )

    conn.commit()
    conn.close()
    await manager.broadcast("refresh")
    return api_hunts_list(request)

@app.post("/api/hunts/{hunt_id}/found")
async def api_found_hunt(
    request: Request,
    hunt_id: int,
    found_location: str = Form("sala"),  # 'sala' | 'bodega'
    photo_camera: UploadFile = File(None),
    photo_gallery: UploadFile = File(None),
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'picker':
        raise HTTPException(status_code=403, detail="Solo hunters/supervisores pueden resolver hunts")

    loc = found_location if found_location in ('sala', 'bodega') else 'sala'
    loc_label = 'en sala (góndola)' if loc == 'sala' else 'en bodega'
    loc_emoji = '' if loc == 'sala' else ''

    # Procesar foto de ubicacion (solo para sala)
    location_photo_b64 = None
    if loc == 'sala':
        photo_file = photo_camera if (photo_camera and photo_camera.filename) else photo_gallery
        if photo_file and photo_file.filename:
            file_bytes = await photo_file.read()
            if file_bytes:
                encoded = base64.b64encode(file_bytes).decode('utf-8')
                content_type = photo_file.content_type or 'image/jpeg'
                location_photo_b64 = f"data:{content_type};base64,{encoded}"

    conn = get_db()
    cursor = conn.cursor()
    hunt = cursor.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()
    if hunt:
        cursor.execute(
            "UPDATE hunts SET status='Encontrado', found_location=?, resolved_at=CURRENT_TIMESTAMP, location_photo=? WHERE id=?",
            (loc, location_photo_b64, hunt_id),
        )
        cursor.execute(
            "UPDATE users SET points=points+50, hunts_completed=hunts_completed+1 WHERE id=?",
            (user['id'],),
        )
        cursor.execute(
            "INSERT INTO feed (message) VALUES (?)",
            (f"{loc_emoji} {user['full_name']} encontró '{hunt['item_name']}' {loc_label} — +50 pts!",),
        )
        reporter = cursor.execute(
            "SELECT username FROM users WHERE full_name=?", (hunt['reported_by'],)
        ).fetchone()
        reporter_username = reporter['username'] if reporter else None
    else:
        reporter_username = None

    conn.commit()
    conn.close()
    await manager.broadcast("refresh")

    if hunt and reporter_username:
        if loc == 'sala' and location_photo_b64:
            # Notificacion sala con foto
            await manager.notify_user(
                reporter_username,
                f"sala-photo:{hunt_id}|{hunt['item_name']}|{user['full_name']}"
            )
        elif loc == 'sala':
            # Sala sin foto — banner generico sala
            await manager.notify_user(
                reporter_username,
                f"sala-photo:{hunt_id}|{hunt['item_name']}|{user['full_name']}"
            )
        else:
            # Bodega — banner generico bodega
            await manager.notify_user(
                reporter_username,
                f"bodega-found:{hunt_id}|{hunt['item_name']}|{user['full_name']}"
            )

    return api_hunts_list(request)


@app.get("/api/hunts/{hunt_id}/location-photo")
def api_get_location_photo(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    conn = get_db()
    row = conn.execute("SELECT location_photo FROM hunts WHERE id=?", (hunt_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Hunt no encontrado")
    return {"photo": row["location_photo"]}


@app.post("/api/hunts/{hunt_id}/picker-confirm")
async def api_picker_confirm(request: Request, hunt_id: int):
    """Picker confirma que ya retiro el producto. Notifica al hunter."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] not in ('picker', 'supervisor'):
        raise HTTPException(status_code=403, detail="Solo pickers pueden confirmar retiro")

    conn = get_db()
    hunt = conn.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()
    if not hunt:
        conn.close()
        raise HTTPException(status_code=404, detail="Hunt no encontrado")

    conn.execute(
        "UPDATE hunts SET picker_retrieved_at=CURRENT_TIMESTAMP WHERE id=?",
        (hunt_id,)
    )
    conn.commit()

    # Notificar al hunter que resolvio este hunt
    hunter_row = conn.execute(
        "SELECT username FROM users WHERE full_name=?", (hunt['assigned_to'],)
    ).fetchone() if hunt['assigned_to'] else None
    conn.close()

    if hunter_row:
        await manager.notify_user(
            hunter_row['username'],
            f"toast:green|Retiro confirmado: '{hunt['item_name']}' ya fue retirado por {user['full_name']}"
        )

    return {"ok": True}


class BannerPayload(BaseModel):
    message: str

@app.post("/api/broadcast-banner")
async def broadcast_banner(request: Request, payload: BannerPayload):
    user = get_current_user(request)
    if not user or user['role'] != 'supervisor':
        raise HTTPException(status_code=403, detail="Solo supervisores")
    msg = payload.message.strip()[:120]
    if not msg:
        raise HTTPException(status_code=400, detail="Mensaje vacio")
    conn = get_db()
    conn.execute(
        "INSERT INTO feed (message, is_supervisor) VALUES (?, 1)",
        (f"{user['full_name']}: {msg}",)
    )
    conn.commit()
    conn.close()
    await manager.broadcast("refresh")
    return {"ok": True}


@app.post("/api/broadcast-hunter")
async def broadcast_hunter(request: Request, payload: BannerPayload):
    user = get_current_user(request)
    if not user or user['role'] != 'hunter':
        raise HTTPException(status_code=403, detail="Solo hunters")
    msg = payload.message.strip()[:100]
    if not msg:
        raise HTTPException(status_code=400, detail="Mensaje vacio")
    conn = get_db()
    conn.execute(
        "INSERT INTO feed (message, is_hunter) VALUES (?, 1)",
        (f"{user['full_name']}: {msg}",)
    )
    conn.commit()
    conn.close()
    await manager.broadcast("refresh")
    return {"ok": True}


class FeedEditPayload(BaseModel):
    message: str

@app.patch("/api/feed/{feed_id}")
async def feed_edit(feed_id: int, request: Request, payload: FeedEditPayload):
    user = get_current_user(request)
    if not user or user['role'] != 'supervisor':
        raise HTTPException(status_code=403, detail="Solo supervisores")
    new_msg = payload.message.strip()[:120]
    if not new_msg:
        raise HTTPException(status_code=400, detail="Mensaje vacio")
    conn = get_db()
    row = conn.execute("SELECT is_supervisor FROM feed WHERE id=?", (feed_id,)).fetchone()
    if not row or not row['is_supervisor']:
        conn.close()
        raise HTTPException(status_code=404, detail="Mensaje no encontrado o no es de supervisor")
    conn.execute("UPDATE feed SET message=? WHERE id=?", (new_msg, feed_id))
    conn.commit()
    conn.close()
    await manager.broadcast("refresh")
    return {"ok": True}


@app.post("/api/hunts/{hunt_id}/no-stock")
async def api_no_stock_hunt(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'picker':
        raise HTTPException(status_code=403, detail="Solo hunters/supervisores pueden resolver hunts")

    conn = get_db()
    cursor = conn.cursor()
    hunt = cursor.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()
    if hunt:
        cursor.execute("UPDATE hunts SET status='Sin Stock', resolved_at=CURRENT_TIMESTAMP WHERE id=?", (hunt_id,))
        cursor.execute("INSERT INTO feed (message) VALUES (?)",
            (f" {user['full_name']} marco {hunt['item_name']} como Sin Stock.",))
        reporter = cursor.execute(
            "SELECT username FROM users WHERE full_name=?", (hunt['reported_by'],)
        ).fetchone()
        reporter_username = reporter['username'] if reporter else None
    else:
        reporter_username = None

    conn.commit()
    conn.close()
    await manager.broadcast("refresh")

    if hunt and reporter_username:
        await manager.notify_user(reporter_username,
            f"toast:amber|{hunt['item_name']} - Sin Stock confirmado por {user['full_name']}.")

    return api_hunts_list(request)

# ────────────────────────────────────────────────────────────────────────────
# TIMER RESOLUTION ACTIONS (hunter / supervisor)
# ────────────────────────────────────────────────────────────────────────────
@app.post("/api/hunts/{hunt_id}/not-found")
async def api_not_found_hunt(
    request: Request,
    hunt_id: int,
    resolution_note: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'picker':
        raise HTTPException(status_code=403, detail="Solo hunters/supervisores pueden resolver hunts")

    conn = get_db()
    cursor = conn.cursor()
    hunt = cursor.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()
    if hunt:
        cursor.execute(
            "UPDATE hunts SET status='No Encontrado', resolution_note=?, resolved_at=CURRENT_TIMESTAMP WHERE id=?",
            (resolution_note.strip() or None, hunt_id),
        )
        cursor.execute(
            "INSERT INTO feed (message) VALUES (?)",
            (f" {user['full_name']} marcó '{hunt['item_name']}' como No Encontrado.",),
        )
        reporter = cursor.execute(
            "SELECT username FROM users WHERE full_name=?", (hunt['reported_by'],)
        ).fetchone()
        reporter_username = reporter['username'] if reporter else None
    else:
        reporter_username = None

    conn.commit()
    conn.close()
    await manager.broadcast("refresh")

    if hunt and reporter_username:
        await manager.notify_user(
            reporter_username,
            f"protocolo:{hunt['item_name']}|no-encontrado|{user['full_name']}"
        )

    return api_hunts_list(request)


@app.post("/api/hunts/{hunt_id}/adjust-inventory")
async def api_adjust_inventory_hunt(
    request: Request,
    hunt_id: int,
    inventory_delta: int = Form(...),
    resolution_note: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'picker':
        raise HTTPException(status_code=403, detail="Solo hunters/supervisores pueden ajustar inventario")

    conn = get_db()
    cursor = conn.cursor()
    hunt = cursor.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()
    if hunt:
        cursor.execute(
            "UPDATE hunts SET status='Ajustado', inventory_delta=?, resolution_note=?, resolved_at=CURRENT_TIMESTAMP WHERE id=?",
            (inventory_delta, resolution_note.strip() or None, hunt_id),
        )
        cursor.execute(
            "UPDATE users SET points = points + 30, hunts_completed = hunts_completed + 1 WHERE id = ?",
            (user['id'],),
        )
        sign = '+' if inventory_delta >= 0 else ''
        cursor.execute(
            "INSERT INTO feed (message) VALUES (?)",
            (f" {user['full_name']} ajust\u00f3 inventario de '{hunt['item_name']}' ({sign}{inventory_delta} un.).",),
        )
        reporter = cursor.execute(
            "SELECT username FROM users WHERE full_name=?", (hunt['reported_by'],)
        ).fetchone()
        reporter_username = reporter['username'] if reporter else None
    else:
        reporter_username = None

    conn.commit()
    conn.close()
    await manager.broadcast("refresh")

    if hunt and reporter_username:
        await manager.notify_user(
            reporter_username,
            f"protocolo:{hunt['item_name']}|ajuste|{user['full_name']}"
        )

    return api_hunts_list(request)


# Picker avisa al hunter que aplicara protocolo si no responde
@app.post("/api/hunts/{hunt_id}/warn-hunter")
async def api_warn_hunter(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] != 'picker':
        raise HTTPException(status_code=403, detail="Solo pickers pueden enviar este aviso")

    conn = get_db()
    hunt = conn.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()
    if not hunt:
        conn.close()
        return {"ok": False, "detail": "Hunt no encontrado"}
    if hunt['status'] != 'Yendo':
        conn.close()
        return {"ok": False, "detail": "Hunt no esta en curso"}
    if hunt['reported_by'] != user['full_name']:
        conn.close()
        raise HTTPException(status_code=403, detail="Solo el picker que reporto puede enviar este aviso")

    # Buscar username del hunter asignado
    hunter_row = conn.execute(
        "SELECT username FROM users WHERE full_name = ?", (hunt['assigned_to'],)
    ).fetchone()
    conn.close()

    if hunter_row:
        await manager.notify_user(
            hunter_row['username'],
            f"aviso-protocolo:{hunt['id']}|{hunt['item_name']}|{user['full_name']}"
        )

    return {"ok": True}


# ── Hunter acepta protocolo del picker (Entendido) ────────────────────────
@app.post("/api/hunts/{hunt_id}/entendido")
async def api_hunt_entendido(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user or user['role'] != 'hunter':
        raise HTTPException(status_code=403, detail="Solo hunters")

    conn = get_db()
    hunt = conn.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()
    if not hunt or hunt['status'] not in ('Buscando', 'Yendo'):
        conn.close()
        return {"ok": False, "detail": "Hunt no activo"}

    # Cerrar como Sin Stock
    conn.execute(
        "UPDATE hunts SET status='Sin Stock', assigned_to=?, resolved_at=CURRENT_TIMESTAMP WHERE id=?",
        (user['full_name'], hunt_id)
    )
    conn.execute(
        "INSERT INTO feed (message) VALUES (?)",
        (f"{user['full_name']} confirmo protocolo de quiebre para {hunt['item_name']} (Sin Stock)",)
    )
    conn.commit()

    # Notificar al picker que reporto
    reporter_row = conn.execute(
        "SELECT username FROM users WHERE full_name = ?", (hunt['reported_by'],)
    ).fetchone()
    conn.close()

    if reporter_row:
        await manager.notify_user(
            reporter_row['username'],
            f"toast:green|Aprobado - Hunter confirmo Sin Stock para {hunt['item_name']}"
        )

    await manager.broadcast("refresh")
    return {"ok": True}


# ── Picker aplica protocolo ──────────────────────────────────────────
#   Fase 2 (5min adicionales corriendo): → 'Protocolo', espera confirmacion
#   VENCIDO (>20min): → 'Protocolo Aplicado', cierre inmediato sin validacion
@app.post("/api/hunts/{hunt_id}/aplico-protocolo")
async def api_aplico_protocolo(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] != 'picker':
        raise HTTPException(status_code=403, detail="Solo pickers pueden aplicar protocolo")

    conn = get_db()
    cursor = conn.cursor()
    hunt = cursor.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()

    if not hunt:
        conn.close()
        return {"ok": False, "detail": "Hunt no encontrado"}
    if hunt['reported_by'] != user['full_name']:
        conn.close()
        raise HTTPException(status_code=403, detail="Solo el picker que reporto puede aplicar protocolo")
    if hunt['status'] not in ('Buscando', 'Yendo'):
        conn.close()
        return {"ok": False, "detail": "Hunt no esta activo"}

    # Calcular fase actual segun tiempo transcurrido
    import datetime as _dt
    TOTAL_SECONDS = 20 * 60  # 15 fase1 + 5 fase2
    reported_str = hunt['reported_at']
    try:
        rep_dt = _dt.datetime.strptime(reported_str, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=_dt.timezone.utc)
        elapsed = (_dt.datetime.now(_dt.timezone.utc) - rep_dt).total_seconds()
    except Exception:
        elapsed = TOTAL_SECONDS + 1  # si no parsea, asumir vencido

    is_vencido = elapsed >= TOTAL_SECONDS

    if is_vencido:
        # VENCIDO: cierre inmediato, sin confirmacion
        cursor.execute(
            "UPDATE hunts SET status='Protocolo Aplicado', protocolo_at=CURRENT_TIMESTAMP,"
            " resolved_at=CURRENT_TIMESTAMP WHERE id=?",
            (hunt_id,),
        )
        cursor.execute(
            "INSERT INTO feed (message) VALUES (?)",
            (f" {user['full_name']} aplic\u00f3 protocolo (cerrado) en '{hunt['item_name']}' ({hunt['quantity']} un.).",),
        )
        conn.commit()
        conn.close()
        await manager.broadcast("refresh")
        return {"ok": True, "auto_closed": True}
    else:
        # Fase 2: espera confirmacion de hunter/supervisor
        cursor.execute(
            "UPDATE hunts SET status='Protocolo', protocolo_at=CURRENT_TIMESTAMP WHERE id=?",
            (hunt_id,),
        )
        cursor.execute(
            "INSERT INTO feed (message) VALUES (?)",
            (f" {user['full_name']} aplic\u00f3 protocolo en '{hunt['item_name']}' ({hunt['quantity']} un.).",),
        )
        conn.commit()

        has_hunter = bool(hunt['assigned_to'])
        if has_hunter:
            hunter_row = cursor.execute(
                "SELECT username FROM users WHERE full_name=?", (hunt['assigned_to'],)
            ).fetchone()
            conn.close()
            if hunter_row:
                await manager.notify_user(
                    hunter_row['username'],
                    f"protocolo-confirmar:{hunt['item_name']}|{user['full_name']}|{hunt_id}"
                )
        else:
            conn.close()
            await manager.broadcast(
                f"protocolo-libre:{hunt['item_name']}|{user['full_name']}|{hunt_id}"
            )

        await manager.broadcast("refresh")
        return {"ok": True, "auto_closed": False}

    await manager.broadcast("refresh")
    return {"ok": True}


# ── Hunter/supervisor confirma el protocolo ───────────────────────────────
@app.post("/api/hunts/{hunt_id}/confirmar-protocolo")
async def api_confirmar_protocolo(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'picker':
        raise HTTPException(status_code=403, detail="Solo hunters y supervisores pueden confirmar")

    conn = get_db()
    cursor = conn.cursor()
    hunt = cursor.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()

    if not hunt or hunt['status'] != 'Protocolo':
        conn.close()
        return {"ok": False, "detail": "Hunt no esta en estado Protocolo"}

    cursor.execute(
        "UPDATE hunts SET status='Protocolo Confirmado', resolved_at=CURRENT_TIMESTAMP WHERE id=?",
        (hunt_id,),
    )
    # Puntos al confirmador
    cursor.execute(
        "UPDATE users SET points=points+20, hunts_completed=hunts_completed+1 WHERE id=?",
        (user['id'],),
    )
    cursor.execute(
        "INSERT INTO feed (message) VALUES (?)",
        (f" {user['full_name']} confirm\u00f3 protocolo en '{hunt['item_name']}'.",),
    )
    # Buscar picker para notificarle
    reporter = cursor.execute(
        "SELECT username FROM users WHERE full_name=?", (hunt['reported_by'],)
    ).fetchone()
    conn.commit()
    conn.close()

    await manager.broadcast("refresh")
    if reporter:
        await manager.notify_user(
            reporter['username'],
            f"toast:green|Protocolo confirmado por {user['full_name']} en '{hunt['item_name']}'"
        )

    return api_hunts_list(request)


# ── Hunter decide mantener busqueda (revierte Protocolo → Yendo) ──────────────
@app.post("/api/hunts/{hunt_id}/mantener-busqueda")
async def api_mantener_busqueda(request: Request, hunt_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Not logged in")
    if user['role'] == 'picker':
        raise HTTPException(status_code=403, detail="Solo hunters y supervisores pueden decidir")

    conn = get_db()
    cursor = conn.cursor()
    hunt = cursor.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()

    if not hunt or hunt['status'] != 'Protocolo':
        conn.close()
        return {"ok": False, "detail": "Hunt no esta en estado Protocolo"}

    # Revertir a Yendo — el hunter sigue buscando
    cursor.execute(
        "UPDATE hunts SET status='Yendo', protocolo_at=NULL WHERE id=?",
        (hunt_id,),
    )
    cursor.execute(
        "INSERT INTO feed (message) VALUES (?)",
        (f" {user['full_name']} mantiene busqueda de '{hunt['item_name']}' (protocolo cancelado).",),
    )
    # Notificar al picker que el hunter sigue buscando
    reporter = cursor.execute(
        "SELECT username FROM users WHERE full_name=?", (hunt['reported_by'],)
    ).fetchone()
    conn.commit()
    conn.close()

    await manager.broadcast("refresh")
    if reporter:
        await manager.notify_user(
            reporter['username'],
            f"toast:blue|{user['full_name']} sigue buscando '{hunt['item_name']}'"
        )

    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
# ADMIN - USER MANAGEMENT (supervisor only)
# ────────────────────────────────────────────────────────────────────────────
VALID_ROLES = ("picker", "hunter", "supervisor")
@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_get(request: Request, msg: str = ""):
    user = get_current_user(request)
    if not user or user["role"] != "supervisor":
        return RedirectResponse(url="/dashboard", status_code=303)

    conn = get_db()
    users_list = conn.execute(
        "SELECT id, username, full_name, role, points, hunts_completed FROM users ORDER BY role, full_name"
    ).fetchall()
    conn.close()

    role_badge = {
        "picker": "bg-blue-100 text-blue-800",
        "hunter": "bg-green-100 text-green-700",
        "supervisor": "bg-purple-100 text-purple-800",
    }

    rows_html = ""
    for u in users_list:
        badge = role_badge.get(u["role"], "bg-gray-100 text-gray-700")
        delete_btn = (
            f'<form method="post" action="/admin/users/delete/{u["id"]}" '
            f'onsubmit="return confirm(\'Eliminar {u["full_name"]}?\')" class="inline">'
            f'<button type="submit" class="text-red-500 hover:text-red-700 text-[10px] font-bold px-2 py-1 '
            f'border border-red-200 rounded hover:bg-red-50 transition">Eliminar</button></form>'
        ) if u["username"] != user["username"] else '<span class="text-[10px] text-gray-400">(tu cuenta)</span>'

        rows_html += f"""
        <tr class="border-t border-gray-100 hover:bg-gray-50/50">
            <td class="py-2.5 px-3 text-xs font-semibold text-gray-800">{u['full_name']}</td>
            <td class="py-2.5 px-3 text-[10px] font-mono text-gray-500">{u['username']}</td>
            <td class="py-2.5 px-3">
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full {badge}">{u['role'].capitalize()}</span>
            </td>
            <td class="py-2.5 px-3 text-xs text-right text-[#0053e2] font-bold">{u['points']}</td>
            <td class="py-2.5 px-3 text-xs text-right text-gray-500">{u['hunts_completed']}</td>
            <td class="py-2.5 px-3 text-right">{delete_btn}</td>
        </tr>"""

    msg_html = ""
    if msg == "ok":
        msg_html = '<div class="mb-4 p-3 bg-green-50 border border-green-200 rounded-xl text-xs text-green-700 font-bold">Usuario creado correctamente.</div>'
    elif msg == "dup":
        msg_html = '<div class="mb-4 p-3 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700 font-bold">Ese nombre de usuario ya existe. Elige otro.</div>'
    elif msg == "invalid":
        msg_html = '<div class="mb-4 p-3 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700 font-bold">Datos invalidos. Revisa los campos.</div>'
    elif msg == "del":
        msg_html = '<div class="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-xl text-xs text-yellow-700 font-bold">Usuario eliminado.</div>'

    admin_html = f"""
    <div class="max-w-4xl mx-auto space-y-6">
      {msg_html}

      <!-- FORM: ADD USER -->
      <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
        <h2 class="text-sm font-black text-gray-900 mb-4">Agregar Nuevo Usuario</h2>
        <form method="post" action="/admin/users/add" class="grid grid-cols-1 sm:grid-cols-2 gap-4">

          <div>
            <label class="block text-[10px] font-semibold text-gray-600 uppercase tracking-wider mb-1">Nombre Completo</label>
            <input name="full_name" type="text" required placeholder="Ej: Maria Gonzalez"
              class="w-full border border-gray-300 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"/>
          </div>

          <div>
            <label class="block text-[10px] font-semibold text-gray-600 uppercase tracking-wider mb-1">Nombre de Usuario</label>
            <input name="username" type="text" required placeholder="Ej: picker_maria"
              class="w-full border border-gray-300 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"
              pattern="[a-z0-9_]+" title="Solo minusculas, numeros y guiones bajos"/>
          </div>

          <div>
            <label class="block text-[10px] font-semibold text-gray-600 uppercase tracking-wider mb-1">Contrasena</label>
            <input name="password" type="text" required placeholder="Ej: 123" value="123"
              class="w-full border border-gray-300 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"/>
          </div>

          <div>
            <label class="block text-[10px] font-semibold text-gray-600 uppercase tracking-wider mb-1">Rol</label>
            <select name="role" required
              class="w-full border border-gray-300 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]">
              <option value="picker">Picker - Reporta faltantes</option>
              <option value="hunter">Hunter - Busca y surte</option>
              <option value="supervisor">Supervisor</option>
            </select>
          </div>

          <div class="sm:col-span-2">
            <button type="submit"
              class="w-full bg-[#0053e2] text-white py-3 rounded-xl font-bold text-sm hover:bg-blue-700 transition shadow-md shadow-blue-500/20">
              Agregar Usuario
            </button>
          </div>
        </form>
      </div>

      <!-- TABLE: CURRENT USERS -->
      <div class="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
        <div class="p-5 border-b border-gray-100 flex items-center justify-between">
          <h2 class="text-sm font-black text-gray-900">Equipo Tienda 99</h2>
          <span class="bg-blue-50 text-[#0053e2] text-[10px] font-bold px-2 py-0.5 rounded-full">{len(users_list)} usuarios</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left">
            <thead class="bg-gray-50">
              <tr>
                <th class="py-2 px-3 text-[10px] font-black text-gray-500 uppercase tracking-wider">Nombre</th>
                <th class="py-2 px-3 text-[10px] font-black text-gray-500 uppercase tracking-wider">Usuario</th>
                <th class="py-2 px-3 text-[10px] font-black text-gray-500 uppercase tracking-wider">Rol</th>
                <th class="py-2 px-3 text-[10px] font-black text-gray-500 uppercase tracking-wider text-right">Pts</th>
                <th class="py-2 px-3 text-[10px] font-black text-gray-500 uppercase tracking-wider text-right">Hunts</th>
                <th class="py-2 px-3 text-[10px] font-black text-gray-500 uppercase tracking-wider text-right">Accion</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return HTMLResponse(content=render_template(admin_html, user=user, active_tab="admin"))


@app.post("/admin/users/add")
def admin_users_add(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
):
    user = get_current_user(request)
    if not user or user["role"] != "supervisor":
        return RedirectResponse(url="/dashboard", status_code=303)

    full_name = full_name.strip()
    username = username.strip().lower()
    password = password.strip()

    if not full_name or not username or not password or role not in VALID_ROLES:
        return RedirectResponse(url="/admin/users?msg=invalid", status_code=303)

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password, full_name, role, avatar, points, hunts_completed) "
            "VALUES (?, ?, ?, ?, '', 0, 0)",
            (username, password, full_name, role),
        )
        conn.commit()
        redirect_url = "/admin/users?msg=ok"
    except Exception:
        redirect_url = "/admin/users?msg=dup"
    finally:
        conn.close()

    return RedirectResponse(url=redirect_url, status_code=303)


@app.post("/admin/users/delete/{user_id}")
def admin_users_delete(request: Request, user_id: int):
    user = get_current_user(request)
    if not user or user["role"] != "supervisor":
        return RedirectResponse(url="/dashboard", status_code=303)

    # Usuarios protegidos (core del sistema)
    PROTECTED = {"picker_juan", "picker_camila", "picker_alex",
                 "hunter_mario", "hunter_lucia", "jose", "p0a005g"}

    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if (target
            and target["username"] != user["username"]      # nunca borrarse a si mismo
            and target["username"] not in PROTECTED):        # nunca borrar core
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/users?msg=del", status_code=303)


# ────────────────────────────────────────────────────────────────────────────
# PROFILE — CAMBIAR CONTRASEÑA
# ────────────────────────────────────────────────────────────────────────────
@app.get("/profile", response_class=HTMLResponse)
def profile_get(request: Request, msg: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if msg == "ok":
        msg_html = '<div class="mb-4 p-3 bg-green-50 border border-green-200 rounded-xl text-xs text-green-700 font-bold"> Contrasena actualizada correctamente.</div>'
    elif msg == "wrong":
        msg_html = '<div class="mb-4 p-3 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700 font-bold"> La contrasena actual es incorrecta.</div>'
    elif msg == "mismatch":
        msg_html = '<div class="mb-4 p-3 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700 font-bold"> Las contrasenas nuevas no coinciden.</div>'
    elif msg == "empty":
        msg_html = '<div class="mb-4 p-3 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700 font-bold"> La nueva contrasena no puede estar vacia.</div>'
    else:
        msg_html = ""

    profile_html = f"""
    <div class="max-w-md mx-auto space-y-6">
      {msg_html}
      <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">

        <!-- Encabezado usuario -->
        <div class="flex items-center gap-4 mb-6 pb-5 border-b border-gray-100">
          <span class="text-4xl">{user['avatar']}</span>
          <div>
            <p class="font-black text-gray-900 text-base leading-tight">{user['full_name']}</p>
            <p class="text-[10px] text-gray-400 uppercase tracking-widest mt-0.5">{user['role']}</p>
            <span class="inline-flex items-center gap-1 bg-yellow-50 text-yellow-700 text-[10px] font-bold px-2 py-0.5 rounded-full border border-yellow-200 mt-1">
              {_pts_badge(user)}
            </span>
          </div>
        </div>

        <!-- Formulario cambio de contrasena -->
        <h2 class="text-sm font-black text-gray-800 mb-4"> Cambiar Contrasena</h2>
        <form method="post" action="/profile/change-password" class="space-y-4">

          <div>
            <label class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Contrasena Actual</label>
            <input name="current_password" type="password" required autocomplete="current-password"
              placeholder="Tu contrasena actual"
              class="w-full border border-gray-300 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"/>
          </div>

          <div>
            <label class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Nueva Contrasena</label>
            <input name="new_password" type="password" required autocomplete="new-password"
              placeholder="Nueva contrasena"
              class="w-full border border-gray-300 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"/>
          </div>

          <div>
            <label class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Confirmar Nueva Contrasena</label>
            <input name="confirm_password" type="password" required autocomplete="new-password"
              placeholder="Repetir nueva contrasena"
              class="w-full border border-gray-300 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0053e2]"/>
          </div>

          <button type="submit"
            class="w-full bg-[#0053e2] hover:bg-blue-700 text-white font-bold text-sm py-3 rounded-xl transition shadow-sm">
             Guardar Contrasena
          </button>
        </form>
      </div>
    </div>
    """
    return HTMLResponse(content=render_template(profile_html, user=user, active_tab="profile"))


@app.post("/profile/change-password")
def profile_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if not new_password.strip():
        return RedirectResponse(url="/profile?msg=empty", status_code=303)

    if new_password != confirm_password:
        return RedirectResponse(url="/profile?msg=mismatch", status_code=303)

    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ? AND password = ?",
        (user["username"], current_password),
    ).fetchone()

    if not row:
        conn.close()
        return RedirectResponse(url="/profile?msg=wrong", status_code=303)

    conn.execute(
        "UPDATE users SET password = ? WHERE username = ?",
        (new_password, user["username"]),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/profile?msg=ok", status_code=303)


# ────────────────────────────────────────────────────────────────────────────
# PWA ENDPOINTS (APP CONVERSION)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/manifest.json")
def get_manifest():
    return {
        "name": "Picker Hunt - Tienda 99",
        "short_name": "PickerHunt99",
        "description": "Plataforma colaborativa para control de faltantes en gondola - Tienda 99",
        "start_url": "/dashboard",
        "display": "standalone",
        "background_color": "#0053e2",
        "theme_color": "#0053e2",
        "orientation": "portrait",
        "icons": [
            {
                "src": "/static/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml"
            }
        ]
    }

@app.get("/sw.js")
def get_sw():
    # SW que se desregistra a si mismo - elimina el PWA cache que causaba doble carga
    js_content = """
    self.addEventListener('install', (e) => { self.skipWaiting(); });
    self.addEventListener('activate', (e) => {
      e.waitUntil(
        caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k))))
          .then(() => self.registration.unregister())
      );
    });
    self.addEventListener('fetch', (e) => {
      e.respondWith(fetch(e.request));
    });
    """
    return Response(content=js_content, media_type="application/javascript")

@app.get("/static/icon.svg")
def get_icon():
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <rect width="512" height="512" rx="100" fill="#0053e2"/>
  <g transform="translate(256,230) scale(1.3)" stroke="#ffc220" stroke-width="24" stroke-linecap="round">
    <line x1="0" y1="-30" x2="0" y2="-110" stroke="#ffc220" />
    <line x1="0" y1="30" x2="0" y2="110" stroke="#ffc220" />
    <line x1="-26" y1="-15" x2="-95" y2="-55" stroke="#ffc220" />
    <line x1="26" y1="15" x2="95" y2="55" stroke="#ffc220" />
    <line x1="-26" y1="15" x2="-95" y2="55" stroke="#ffc220" />
    <line x1="26" y1="-15" x2="95" y2="-55" stroke="#ffc220" />
  </g>
  <text x="256" y="440" font-family="system-ui, -apple-system, sans-serif" font-weight="900" font-size="64" fill="#ffffff" text-anchor="middle">PICKER HUNT</text>
  <text x="256" y="490" font-family="system-ui, -apple-system, sans-serif" font-weight="900" font-size="32" fill="#ffc220" text-anchor="middle">TIENDA 99</text>
</svg>"""
    return Response(content=svg_content, media_type="image/svg+xml")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[INFO] Iniciando servidor para Picker Hunt (Tienda 99)...")
    uvicorn.run("picker_hunt_app:app", host="0.0.0.0", port=8099, reload=True)
