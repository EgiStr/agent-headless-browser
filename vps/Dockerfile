# Simulates a bare Linux CLI VPS: no X server, no desktop, just Chrome + Python.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates python3 python3-venv procps fonts-liberation \
 && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -y --no-install-recommends ./google-chrome-stable_current_amd64.deb \
 && rm -f google-chrome-stable_current_amd64.deb && rm -rf /var/lib/apt/lists/*
RUN useradd -m agent
WORKDIR /app
COPY requirements.txt /app/
RUN python3 -m venv /app/.venv && /app/.venv/bin/pip install -q -r /app/requirements.txt
COPY --chown=agent *.py /app/
COPY --chown=agent scripts /app/scripts
RUN mkdir -p /app/profiles /app/out && chown -R agent /app/profiles /app/out
USER agent
CMD ["sleep", "infinity"]
