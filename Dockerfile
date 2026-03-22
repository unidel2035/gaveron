FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY gaveron/ gaveron/

RUN pip install --no-cache-dir .

# History directory
RUN mkdir -p /run/gaveron

EXPOSE 8080

ENV GAVERON_FEED_TYPE=beast \
    GAVERON_FEED_HOST=127.0.0.1 \
    GAVERON_FEED_PORT=30005 \
    GAVERON_HTTP_HOST=0.0.0.0 \
    GAVERON_HTTP_PORT=8080 \
    GAVERON_HISTORY_DIR=/run/gaveron \
    GAVERON_LOG_LEVEL=INFO

ENTRYPOINT ["python", "-m", "gaveron"]
