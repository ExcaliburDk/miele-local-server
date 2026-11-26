#!/bin/sh
set -eu

HOST_PORT="${HOST_PORT:-5001}"
CONTAINER_PORT="${CONTAINER_PORT:-5001}"
CONTAINER_NAME="${CONTAINER_NAME:-Miele-Local-Server}"
IMAGE_NAME="${IMAGE_NAME:-miele-local-server:latest}"
DATA_VOLUME="${DATA_VOLUME:-miele-local-server-data}"
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

SUDO=""
if docker info >/dev/null 2>&1; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    SUDO="sudo"
else
    echo "ERROR: Docker is not available for this user" >&2
    exit 1
fi

docker_cmd() {
    if [ -n "$SUDO" ]; then
        sudo docker "$@"
    else
        docker "$@"
    fi
}

echo "Building Docker image: $IMAGE_NAME..."
docker_cmd build --no-cache -t "$IMAGE_NAME" "$PROJECT_DIR"

if docker_cmd ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    echo "Stopping and replacing container: $CONTAINER_NAME..."
    docker_cmd stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker_cmd rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

docker_cmd volume create "$DATA_VOLUME" >/dev/null

docker_cmd run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --user 0:0 \
    -p "$HOST_PORT:$CONTAINER_PORT" \
    -e EXTERNAL_PORT="$HOST_PORT" \
    -e HOME_NAME="${HOME_NAME:-Miele Local Server}" \
    -v "$DATA_VOLUME:/data" \
    "$IMAGE_NAME" >/dev/null

sleep 2
docker_cmd ps --filter "name=^/${CONTAINER_NAME}$" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
echo "Web UI:   http://<HOST-IP>:$HOST_PORT/"
echo "API Docs: http://<HOST-IP>:$HOST_PORT/docs"
