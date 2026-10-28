# sky — six-city weather

> Current conditions and a seven-day outlook for six cities, air quality included, using Open-Meteo's keyless public API only.

English | [中文](README.md)

## What it is

Open-Meteo accepts comma-separated coordinates, so all six cities fit in a single request: one call for current conditions plus the seven-day outlook, another for air quality, 1.1 seconds apart, every ten minutes. No HTML parsing, no anti-bot games, no API key. Data lands in two SQLite tables — `readings` for each sample (kept seven days) and `forecast` for the outlook — while pages render from an in-memory cache. A failed refresh never blanks the page; the last good data stays up with a note at the top.

## Features

- All six cities in one request — the API accepts comma-separated coordinates
- Two requests per cycle, ten minutes apart — lighter than loading a weather site once
- `readings` and `forecast` tables; samples are kept for seven days
- Air quality graded on the US AQI scale, the one most people here already read in
- A failed cycle keeps the last good data and says so at the top of the page
- Plain server-side rendering — no frontend framework, no external fonts or icons

## Stack

Python 3.12 · Flask 3.1 · SQLite

## Run

```bash
pip install -r requirements.txt
python app.py   # 默认端口 8840，可用 SKY_PORT 覆盖
```

## About

Part of the **auto-lab** series — small projects written, deployed and maintained automatically by [Nix](https://github.com/Uxnar-ai) on a self-hosted server.

Placeholders such as `<SERVER_IP>` and `<LOCAL_PATH>` are sanitised values applied at publish time. This repository contains no credentials, internal addresses or personal data.

## License

MIT
