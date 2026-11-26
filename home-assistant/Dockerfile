ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

RUN if command -v apk >/dev/null 2>&1; then \
      apk add --no-cache python3 py3-pip; \
    elif command -v apt-get >/dev/null 2>&1; then \
      apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv && \
      rm -rf /var/lib/apt/lists/*; \
    else \
      echo "No supported package manager found." >&2; exit 1; \
    fi

WORKDIR /usr/src/app
COPY . /usr/src/app
RUN python3 -m venv /usr/src/app/venv \
    && /usr/src/app/venv/bin/pip install --no-cache-dir /usr/src/app

COPY home-assistant/run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
