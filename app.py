#!/usr/bin/env python3
"""天光 —— 六个城市的天气与空气质量，只走 Open-Meteo 的免密钥公开接口。

两个请求覆盖全部城市（接口支持多云量逗号拼接），10 分钟一轮，
落 SQLite 当历史，页面读缓存。不解析 HTML、不碰反爬。
"""
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "sky.db")
PORT = int(os.environ.get("SKY_PORT", "8840"))
REFRESH = int(os.environ.get("SKY_REFRESH", "600"))     # 秒，10 分钟
TIMEOUT = 12
UA = "auto-lab/1.0 (+https://lab.824109091.xyz)"
CST = timezone(timedelta(hours=8))

CITIES = [
    {"id": "beijing",   "name": "北京",  "lat": 39.90, "lon": 116.41},
    {"id": "shanghai",  "name": "上海",  "lat": 31.23, "lon": 121.47},
    {"id": "guangzhou", "name": "广州",  "lat": 23.13, "lon": 113.26},
    {"id": "chengdu",   "name": "成都",  "lat": 30.57, "lon": 104.07},
    {"id": "harbin",    "name": "哈尔滨", "lat": 45.80, "lon": 126.53},
    {"id": "urumqi",    "name": "乌鲁木齐", "lat": 43.83, "lon": 87.62},
]

# WMO 天气代码 → 中文
WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "小毛雨", 53: "毛雨", 55: "浓毛雨",
    56: "冻毛雨", 57: "浓冻毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "强阵雨", 82: "暴阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}

app = Flask(__name__)
_state = {"ok": False, "ts": 0, "cities": [], "error": "", "took": 0}
_lock = threading.Lock()


# ---------------------------------------------------------------- 抓取

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def fetch_weather():
    lat = ",".join(str(c["lat"]) for c in CITIES)
    lon = ",".join(str(c["lon"]) for c in CITIES)
    q = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "wind_speed_10m,precipitation,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,sunrise,sunset",
        "timezone": "Asia/Shanghai", "forecast_days": "7",
    }
    return _get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))


def fetch_air():
    lat = ",".join(str(c["lat"]) for c in CITIES)
    lon = ",".join(str(c["lon"]) for c in CITIES)
    q = {"latitude": lat, "longitude": lon,
         "current": "pm2_5,pm10,us_aqi", "timezone": "Asia/Shanghai"}
    return _get("https://air-quality-api.open-meteo.com/v1/air-quality?"
                + urllib.parse.urlencode(q))


# ---------------------------------------------------------------- 存储

def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS readings(
            ts INTEGER, city TEXT, temp REAL, feels REAL, hum REAL,
            wind REAL, code INTEGER, aqi REAL, pm25 REAL,
            PRIMARY KEY(ts, city))""")
        c.execute("""CREATE TABLE IF NOT EXISTS forecast(
            city TEXT, day TEXT, code INTEGER, tmax REAL, tmin REAL,
            pprob REAL, sunrise TEXT, sunset TEXT,
            PRIMARY KEY(city, day))""")


def save(cities, ts):
    with _connect() as c:
        for ct in cities:
            cur = ct.get("current") or {}
            air = ct.get("air") or {}
            c.execute("INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?,?,?,?,?)",
                      (ts, ct["id"], cur.get("temperature_2m"), cur.get("apparent_temperature"),
                       cur.get("relative_humidity_2m"), cur.get("wind_speed_10m"),
                       cur.get("weather_code"), air.get("us_aqi"), air.get("pm2_5")))
            for d in ct.get("daily", []):
                c.execute("INSERT OR REPLACE INTO forecast VALUES (?,?,?,?,?,?,?,?)",
                          (ct["id"], d["day"], d["code"], d["tmax"], d["tmin"],
                           d["pprob"], d["sunrise"], d["sunset"]))
        c.execute("DELETE FROM readings WHERE ts < ?", (ts - 7 * 86400,))


# ---------------------------------------------------------------- 归并

def _row_list(raw):
    """接口在多坐标时返回 list，单坐标时返回 dict —— 统一成 list。"""
    if isinstance(raw, list):
        return raw
    return [raw]


def merge(wx, air):
    wl, al = _row_list(wx), _row_list(air)
    out = []
    for i, ct in enumerate(CITIES):
        w = wl[i] if i < len(wl) else {}
        a = al[i] if i < len(al) else {}
        d = w.get("daily") or {}
        days = []
        for j, day in enumerate(d.get("time", [])):
            def pick(key, idx=j):
                arr = d.get(key) or []
                return arr[idx] if idx < len(arr) else None
            days.append({
                "day": day,
                "code": pick("weather_code"),
                "tmax": pick("temperature_2m_max"),
                "tmin": pick("temperature_2m_min"),
                "pprob": pick("precipitation_probability_max"),
                "sunrise": (pick("sunrise") or "")[11:16],
                "sunset": (pick("sunset") or "")[11:16],
            })
        cur = w.get("current") or {}
        code = cur.get("weather_code")
        out.append({
            "id": ct["id"], "name": ct["name"],
            "current": {**cur, "desc": WMO.get(code, "—") if code is not None else "—"},
            "air": (a.get("current") or {}),
            "daily": days,
        })
    return out


def refresh(force=False):
    t0 = time.time()
    try:
        wx = fetch_weather()
        time.sleep(1.1)                     # 两个接口之间留一点间隔，别扎堆
        air = fetch_air()
        cities = merge(wx, air)
        ts = int(time.time())
        with _lock:
            _state.update(ok=True, ts=ts, cities=cities, error="",
                          took=round(time.time() - t0, 1))
        save(cities, ts)
        print(f"[sky] 刷新完成 {len(cities)} 城，用时 {round(time.time()-t0,1)}s", flush=True)
    except Exception as e:
        with _lock:
            _state.update(ok=False, error=f"{type(e).__name__}: {e}"[:180])
        print(f"[sky] 刷新失败: {e}", flush=True)


def loop():
    refresh()
    while True:
        time.sleep(REFRESH)
        refresh()


# ---------------------------------------------------------------- 路由

def aqi_level(v):
    """US AQI 分级，和国内常见的 0–500 口径接近。"""
    if v is None:
        return {"label": "—", "cls": "na"}
    if v <= 50:
        return {"label": "优", "cls": "good"}
    if v <= 100:
        return {"label": "良", "cls": "fair"}
    if v <= 150:
        return {"label": "轻度", "cls": "mid"}
    if v <= 200:
        return {"label": "中度", "cls": "poor"}
    if v <= 300:
        return {"label": "重度", "cls": "bad"}
    return {"label": "严重", "cls": "bad"}


@app.template_filter("aqi")
def _aqi(v):
    return aqi_level(v)


@app.route("/")
def index():
    with _lock:
        st = dict(_state)
    for c in st["cities"]:
        c["aqi_lv"] = aqi_level((c.get("air") or {}).get("us_aqi"))
    stamp = datetime.fromtimestamp(st["ts"], CST).strftime("%m-%d %H:%M") if st["ts"] else "—"
    return render_template("index.html", st=st, stamp=stamp,
                           wmo=WMO, refresh=REFRESH // 60)


@app.route("/api/state")
def api_state():
    with _lock:
        return jsonify(dict(_state))


@app.route("/healthz")
def healthz():
    with _lock:
        return {"ok": _state["ok"], "cities": len(_state["cities"]),
                "age": int(time.time() - _state["ts"]) if _state["ts"] else None}


if __name__ == "__main__":
    init_db()
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
