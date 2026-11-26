# Miele Local Server

A modern, containerized local REST API, Server-Sent Events (SSE) stream, and responsive Web UI for **Miele@home** appliances — built in the style of **Tado Local Server** and based on the protocol reverse-engineering from [akappner/MieleRESTServer](https://github.com/akappner/MieleRESTServer).

## Key Features

- **Modern FastAPI + OpenAPI (`/docs`)**: Async HTTP client with explicit timeouts, Pydantic validation, and full interactive Swagger UI.
- **Responsive Web UI (`/`)**: Light/dark theme, live appliance cards with progress bars & finish ETA, deep-sleep awareness, Add/Commission/Import wizard with human-readable error/success guidance, simplified Overview modal, Run Statistics & History graphs.
- **Zero-Block Startup & SQLite Persistence**: Starts cleanly without a pre-existing config file, storing appliances, run history, state history, DOP2 cache, and settings in `/data/miele-local.db`.
- **Real-Time SSE Events (`/events`)**: Instant push notifications when appliance status, program phase, remaining time, or temperatures change.
- **100% Backward Compatible**: Supports legacy `MieleRESTServer` routes (`/generate-summary`, `/endpoints`, `/start`, `/wakeup`, `/walkdop2tree`, `/dop2leaf`, `/dop2setting`, `/command`) plus YAML import.
- **Container & Home Assistant Ready**: Includes `Dockerfile`, `docker-compose.yml`, `HEALTHCHECK`, `qnap-update.sh`, mDNS/zeroconf advertisement, and `home-assistant/` add-on definitions.

## Quick Start (Docker Compose)

```bash
docker compose up -d
```

Open **`http://<SERVER-IP>:5001/`** for the Web UI or **`http://<SERVER-IP>:5001/docs`** for Swagger OpenAPI docs.

## Issues from `akappner/MieleRESTServer` Addressed

See [ISSUES_ADDRESSED.md](ISSUES_ADDRESSED.md) for a full breakdown of how issues #1–#32 are resolved or improved in this implementation.

## Attribution & Disclaimer

This project is an independent rewrite and extension derived from [akappner/MieleRESTServer](https://github.com/akappner/MieleRESTServer) (licensed under GNU GPLv3), adding FastAPI/OpenAPI, SQLite persistence, pairing & diagnostics improvements, run statistics, deep-sleep handling, and a modern Web UI.

> **Disclaimer:** Based on independent protocol reverse engineering and **not** authorized, warranted, or tested by Miele. Use at your own risk; incorrect commands or unsupported endpoints may affect appliance behavior or warranty.

## License

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License v3.0 (GPL-3.0)** or later as published by the Free Software Foundation. See the [LICENSE](LICENSE) file for the full license text.
