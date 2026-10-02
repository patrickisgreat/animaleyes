FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY animaleyes ./animaleyes
COPY petlibro-cli ./petlibro-cli
RUN pip install --no-cache-dir . ./petlibro-cli

# Local YOLO detector backend (IDENTIFIER=yolo). CPU-only torch keeps the image far smaller
# than the default CUDA build. Build with --build-arg WITH_YOLO=1 to include it (adds ~1-2 GB);
# omit for the lean Claude-only image.
ARG WITH_YOLO=0
RUN if [ "$WITH_YOLO" = "1" ]; then \
      pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        ".[yolo]"; \
    fi

COPY tools ./tools
COPY config.toml ./config.toml.default

# config.toml, the SQLite database and saved frames live on volumes (see docker-compose.yml).
ENV ANIMALEYES_CONFIG=/app/config/config.toml ANIMALEYES_DATA=/app/data
CMD ["sh", "-c", "mkdir -p /app/config && [ -f $ANIMALEYES_CONFIG ] || cp config.toml.default $ANIMALEYES_CONFIG; exec animaleyes"]
