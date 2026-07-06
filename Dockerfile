FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies first, from requirements.txt (the single source of
# truth), so this layer is cached and only rebuilds when dependencies change.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Then copy the app and install it as a package WITHOUT re-resolving dependencies
# (they are already installed above). Editing app code only re-runs this fast step.
COPY pyproject.toml README.md ./
COPY core ./core
COPY config ./config
COPY scripts ./scripts
COPY docs ./docs
RUN pip install --no-cache-dir --no-deps .

# GDAL/COG read tuning (previously set in docker-compose.yml) baked into the image
# so it applies wherever the container runs, including Cloud Run.
ENV GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR \
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF,.tiff" \
    GDAL_HTTP_MULTIPLEX=YES \
    GDAL_HTTP_VERSION=2 \
    VSI_CACHE=TRUE \
    GDAL_CACHEMAX=200 \
    PORT=8080

# Cloud Run routes to the port in $PORT (default 8080). The entrypoint picks the
# service (app|tiler) from the SERVICE env var and binds to that port.
EXPOSE 8080

ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]
