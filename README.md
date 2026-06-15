# BerkeleyDashboard

Dual-mode web dashboard for the Berkeley Home Intelligence Platform.

One FastAPI app, two audiences:

| URL | Audience | Content |
|-----|----------|---------|
| `/internal/` | Household (LAN / VPN) | Full house data — alarms, messages, climate, power, garden, birds |
| `/public/` | Anyone (internet) | Seismograph, air quality, BirdNET, weather — no personal data |

nginx on Node 01 enforces the boundary:
- `home.mosswood.internal` → routes all paths → port 8090
- `mosswood.science` (public DNS) → routes only `/public/*` and `/api/public/*`

## Architecture

```
BerkeleyDashboard (port 8090)
├── MQTT Bridge — subscribes home/# → maintains live state dict
│
├── /internal/          Internal SPA
│   ├── Alarm panel     ← BerkeleyAlarms API proxy (port 8084)
│   ├── Message inbox   ← BerkeleyMessages API proxy (port 8085)
│   ├── Climate/rooms   ← MQTT home/sensors/house/#
│   ├── Garden/soil     ← MQTT home/sensors/house/#
│   ├── Power circuits  ← MQTT home/sensors/power/#
│   ├── BirdNET (full)  ← MQTT home/events/bird-audio
│   └── Agent health    ← MQTT home/status/#
│
└── /public/            Public Science Site
    ├── Seismograph     ← MQTT home/sensors/seismic (live canvas waveform)
    ├── EQ alerts       ← MQTT home/alerts/earthquake
    ├── Air quality     ← MQTT home/sensors/air/# (AirGradient)
    ├── BirdNET sightings← species only, no location, no audio
    ├── Weather         ← MQTT home/sensors/environmental-station
    └── About / sensors ← static

WebSockets:
  /ws/internal — full MQTT relay (LAN only via nginx)
  /ws/public   — filtered to public-safe topics only
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_BROKER` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `DASHBOARD_PORT` | `8090` | Server port |
| `ALARMS_API_URL` | `http://localhost:8084` | BerkeleyAlarms service |
| `MESSAGES_API_URL` | `http://localhost:8085` | BerkeleyMessages service |
| `HOMESENSORS_API_URL` | `http://localhost:8082` | BerkeleyHomeSensors service |
| `LOG_LEVEL` | `INFO` | Log verbosity |

## Related Services

| Service | Port | Role |
|---------|------|------|
| BerkeleyAlarms | 8084 | Alert actuator (Alexa, banners) |
| BerkeleyMessages | 8085 | AI agent message inbox |
| BerkeleyHomeSensors | 8082 | House sensor agent |
| BerkeleyDashboard | **8090** | **This service** |
