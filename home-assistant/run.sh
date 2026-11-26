#!/usr/bin/with-contenv bashio
echo "Miele Local Server starting..."

ARGS="--state /data/miele-local.db --bind 0.0.0.0 --port 5001"

LOG_LEVEL="$(bashio::config 'log_level')"
if [ "$LOG_LEVEL" = "debug" ]; then
    ARGS="$ARGS --verbose"
fi

PURGE_DAYS="$(bashio::config 'purge_history')"
if [ -n "$PURGE_DAYS" ] && [ "$PURGE_DAYS" -ge 1 ] 2>/dev/null; then
    ARGS="$ARGS --purgehistory $PURGE_DAYS"
fi

HOME_NAME="$(bashio::config 'home_name')"
if [ -n "$HOME_NAME" ]; then
    export MIELE_HOME_NAME="$HOME_NAME"
fi

# Import configured endpoints from add-on options into temporary YAML
if bashio::config.has_value 'endpoints'; then
    python3 - <<'PY'
import json, yaml
from pathlib import Path
opts = json.loads(Path("/data/options.json").read_text())
endpoints = {}
for item in opts.get("endpoints") or []:
    name = item.get("name")
    if name:
        endpoints[name] = {
            "host": item["host"],
            "groupId": item["groupId"],
            "groupKey": item["groupKey"],
            "route": item.get("route") or "auto",
        }
if endpoints:
    Path("/tmp/ha-endpoints.yaml").write_text(yaml.safe_dump({"endpoints": endpoints}))
PY
    if [ -f "/tmp/ha-endpoints.yaml" ]; then
        ARGS="$ARGS --config /tmp/ha-endpoints.yaml"
    fi
fi

exec /usr/src/app/venv/bin/miele-local $ARGS
