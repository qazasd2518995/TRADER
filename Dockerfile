# Cloud signal Hub image (deployed to Fly.io as app "gold-signal-hub-tw").
#
# The Hub is pure standard-library Python (copy_trader/central/hub_server.py).
# We copy ONLY copy_trader/central and blank out copy_trader/__init__.py so the
# container does not need the local LINE database reader or MT5 client. hub_server's
# `from copy_trader.config import DATA_DIR` is wrapped in try/except and falls
# back to cwd, and the store path is supplied via COPY_TRADER_HUB_STORE below.
FROM python:3.12-slim

WORKDIR /app

COPY copy_trader/central /app/copy_trader/central

RUN echo "" > /app/copy_trader/__init__.py \
 && mkdir -p /data

ENV COPY_TRADER_HUB_HOST=0.0.0.0 \
    COPY_TRADER_HUB_PORT=8080 \
    COPY_TRADER_HUB_STORE=/data/central_hub_signals.jsonl \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Token is provided at runtime as a Fly secret:
#   fly secrets set COPY_TRADER_HUB_TOKEN=<your-token>
CMD ["python", "-m", "copy_trader.central.hub_server"]
