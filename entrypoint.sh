#!/bin/sh
set -e

STATE_DB="/data/miele-local.db"
PORT="${MIELE_PORT:-5001}"
ARGS="--state $STATE_DB --bind 0.0.0.0 --port $PORT"

if [ -n "$MIELE_CONFIG" ] && [ -f "$MIELE_CONFIG" ]; then
    ARGS="$ARGS --config $MIELE_CONFIG"
elif [ -f "/data/MieleRESTServer.config" ]; then
    ARGS="$ARGS --config /data/MieleRESTServer.config"
fi

PURGE_DAYS="${MIELE_PURGE_DAYS:-${PURGE_DAYS:-30}}"
if [ -n "$PURGE_DAYS" ]; then
    ARGS="$ARGS --purgehistory $PURGE_DAYS"
fi

if [ -n "$EXTERNAL_PORT" ] && [ -z "$MIELE_EXTERNAL_PORT" ]; then
    export MIELE_EXTERNAL_PORT="$EXTERNAL_PORT"
fi

exec miele-local $ARGS
