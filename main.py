import os, io, json, re, time
from datetime import datetime, timezone
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymongo import MongoClient, DESCENDING

app = FastAPI(title="Weather Sphere AI")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY", os.getenv("CLOUDFLARE_API_TOKEN", ""))
MONGODB_URI = os.getenv("MONGODB_URI", "")
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-before-production")

mongo = None
db = None
if MONGODB_URI:
    try:
        mongo = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3500)
        db = mongo["weather_sphere"]
        mongo.admin.command("ping")
    except Exception:
        db = None

class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=180)

class CoordinatesRequest(BaseModel):
    lat: float
    lon: float

def require_weather_key():
    if not OPENWEATHER_API_KEY:
        raise HTTPException(503, "OpenWeather API key is not configured in Render Environment.")

async def ow_get(path, params):
    require_weather_key()
    async with httpx.AsyncClient(timeout=18) as client:
        r = await client.get("https://api.openweathermap.org" + path, params={**params, "appid": OPENWEATHER_API_KEY})
    if r.status_code >= 400:
        try: detail = r.json().get("message", "OpenWeather request failed")
        except Exception: detail = "OpenWeather request failed"
        raise HTTPException(502, detail)
    return r.json()

async def resolve_query(query: str):
    q = query.strip()
    # Accept "lat, lon" or "lat lon".
    match = re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*", q)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
        raise HTTPException(400, "Coordinates are outside valid ranges.")
    results = await ow_get("/geo/1.0/direct", {"q": q, "limit": 5})
    if not results:
        raise HTTPException(404, "Location not found. Try a city, region, or coordinates.")
    # Prefer exact-ish first OpenWeather result.
    return float(results[0]["lat"]), float(results[0]["lon"])

async def reverse_name(lat, lon):
    try:
        rows = await ow_get("/geo/1.0/reverse", {"lat": lat, "lon": lon, "limit": 1})
        if rows:
            x = rows[0]
            return ", ".join(str(v) for v in [x.get("name"), x.get("state"), x.get("country")] if v)
    except Exception:
        pass
    return f"{lat:.3f}, {lon:.3f}"

def phase_from_sun(now_ts, sunrise, sunset):
    # Returns a visual phase using the location's local solar cycle.
    if not sunrise or not sunset:
        return "day"
    if now_ts < sunrise:
        if sunrise - now_ts <= 90 * 60: return "dawn"
        return "night"
    if now_ts < sunset:
        if sunset - now_ts <= 100 * 60: return "sunset"
        return "day"
    if now_ts - sunset <= 100 * 60: return "sunset"
    return "night"

@app.get("/", response_class=HTMLResponse)
async def home():
    with open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8") as f:
        return f.read()

@app.get("/api/health")
async def health():
    return {"status": "ok", "mongodb": db is not None, "openweather_configured": bool(OPENWEATHER_API_KEY),
            "groq_configured": bool(GROQ_API_KEY), "cloudflare_configured": bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_KEY)}

@app.post("/api/weather")
async def weather(req: SearchRequest):
    lat, lon = await resolve_query(req.query)
    return await weather_for_coords(lat, lon, req.query)

@app.post("/api/weather/coords")
async def weather_coords(req: CoordinatesRequest):
    if not (-90 <= req.lat <= 90 and -180 <= req.lon <= 180):
        raise HTTPException(400, "Invalid coordinates.")
    return await weather_for_coords(req.lat, req.lon, None)

async def weather_for_coords(lat, lon, query):
    current, forecast, place = await __weather_bundle(lat, lon)
    sys = current.get("sys", {})
    now = int(time.time()) + int(current.get("timezone", 0))
    sunrise = sys.get("sunrise", 0) + int(current.get("timezone", 0))
    sunset = sys.get("sunset", 0) + int(current.get("timezone", 0))
    phase = phase_from_sun(now, sunrise, sunset)
    weather_main = (current.get("weather") or [{}])[0]
    data = {
        "place": place, "lat": lat, "lon": lon,
        "timezone_offset": int(current.get("timezone", 0)),
        "dt": current.get("dt"), "sunrise": sys.get("sunrise"), "sunset": sys.get("sunset"),
        "phase": phase, "temp": round(current["main"]["temp"]), "feels_like": round(current["main"]["feels_like"]),
        "temp_min": round(current["main"].get("temp_min", current["main"]["temp"])),
        "temp_max": round(current["main"].get("temp_max", current["main"]["temp"])),
        "humidity": current["main"].get("humidity"), "pressure": current["main"].get("pressure"),
        "wind_speed": current.get("wind", {}).get("speed"), "wind_deg": current.get("wind", {}).get("deg", 0),
        "visibility": current.get("visibility"), "clouds": current.get("clouds", {}).get("all", 0),
        "description": weather_main.get("description", "Weather"), "condition": weather_main.get("main", "Clear"),
        "icon": weather_main.get("icon", "01d"), "country": sys.get("country"),
        "hourly": [{"dt": x.get("dt"), "temp": round(x.get("main", {}).get("temp", 0)),
                    "icon": (x.get("weather") or [{}])[0].get("icon", "01d"),
                    "description": (x.get("weather") or [{}])[0].get("description", "")}
                   for x in forecast.get("list", [])[:8]],
        "daily": []
    }
    # OpenWeather free forecast is 3-hourly; build a compact daily summary from available points.
    seen = set()
    for item in forecast.get("list", []):
        day = datetime.fromtimestamp(item["dt"], timezone.utc).strftime("%Y-%m-%d")
        if day in seen: continue
        seen.add(day)
        w = (item.get("weather") or [{}])[0]
        data["daily"].append({"date": day, "temp": round(item.get("main", {}).get("temp", 0)),
                              "icon": w.get("icon", "01d"), "description": w.get("description", "")})
        if len(data["daily"]) >= 5: break
    if db is not None:
        try:
            db.searches.insert_one({"query": query, "place": place, "lat": lat, "lon": lon,
                                    "weather": data, "created_at": datetime.now(timezone.utc)})
        except Exception:
            pass
    return data

async def __weather_bundle(lat, lon):
    current = await ow_get("/data/2.5/weather", {"lat": lat, "lon": lon, "units": "metric", "lang": "en"})
    forecast = await ow_get("/data/2.5/forecast", {"lat": lat, "lon": lon, "units": "metric", "lang": "en"})
    place = await reverse_name(lat, lon)
    return current, forecast, place

@app.get("/api/history")
async def history(limit: int = 15):
    if db is None:
        return {"items": [], "available": False}
    try:
        rows = list(db.searches.find({}, {"_id": 0, "query": 1, "place": 1, "lat": 1, "lon": 1, "created_at": 1}).sort("created_at", DESCENDING).limit(max(1, min(limit, 50))))
        for row in rows:
            if row.get("created_at"): row["created_at"] = row["created_at"].isoformat()
        return {"items": rows, "available": True}
    except Exception:
        return {"items": [], "available": False}

@app.post("/api/assistant")
async def assistant(req: SearchRequest):
    if not GROQ_API_KEY:
        raise HTTPException(503, "Groq API key is not configured in Render Environment.")
    prompt = ("You are a concise weather assistant. Extract the requested location from the user's message. "
              "Reply ONLY as JSON with keys location and reply. location must be a searchable place name or coordinate pair. "
              "If there is no clear location, set location to an empty string and ask one short clarification in reply. "
              "User message: " + req.query)
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role":"user","content":prompt}],
                  "temperature":0.2, "response_format":{"type":"json_object"}})
    if r.status_code >= 400: raise HTTPException(502, "Groq assistant request failed.")
    try: result = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception: raise HTTPException(502, "Could not understand the assistant response.")
    location = (result.get("location") or "").strip()
    if not location: return {"reply": result.get("reply", "Which location would you like to check?"), "weather": None}
    weather_data = await weather_for_coords(*await resolve_query(location), location)
    return {"reply": result.get("reply") or f"Here is the weather for {weather_data['place']}.", "weather": weather_data}

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not GROQ_API_KEY: raise HTTPException(503, "Groq API key is not configured.")
    raw = await file.read()
    if not raw: raise HTTPException(400, "No audio received.")
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post("https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (file.filename or "recording.webm", raw, file.content_type or "audio/webm")},
            data={"model":"whisper-large-v3-turbo", "response_format":"json"})
    if r.status_code >= 400: raise HTTPException(502, "Groq speech recognition failed.")
    return {"text": r.json().get("text", "")}

@app.post("/api/speak")
async def speak(req: SearchRequest):
    if not GROQ_API_KEY: raise HTTPException(503, "Groq API key is not configured.")
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post("https://api.groq.com/openai/v1/audio/speech",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={"model":"playai-tts", "voice":"Fritz-PlayAI", "input":req.query, "response_format":"wav"})
    if r.status_code >= 400: raise HTTPException(502, "Groq text-to-speech failed.")
    from fastapi.responses import Response
    return Response(content=r.content, media_type="audio/wav")

@app.post("/api/scene")
async def scene(req: CoordinatesRequest):
    if not (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_KEY):
        raise HTTPException(503, "Cloudflare credentials are not configured.")
    place = await reverse_name(req.lat, req.lon)
    prompt = f"Beautiful atmospheric cinematic weather illustration of {place}, latitude {req.lat:.3f}, longitude {req.lon:.3f}, realistic sky, wide landscape, no text, premium weather app background."
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers={"Authorization":f"Bearer {CLOUDFLARE_API_KEY}", "Content-Type":"application/json"},
                              json={"prompt":prompt})
    if r.status_code >= 400: raise HTTPException(502, "Cloudflare image generation failed.")
    content_type = r.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = r.json()
        if payload.get("success") and payload.get("result", {}).get("image"):
            return {"image": "data:image/png;base64," + payload["result"]["image"]}
        raise HTTPException(502, "Cloudflare returned no image.")
    import base64
    return {"image": "data:image/png;base64," + base64.b64encode(r.content).decode()}
