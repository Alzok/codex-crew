FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_RUNS_DIR=/workspace/runs \
    MCP_STORE_PATH=/workspace/store/tasks.db

WORKDIR /app

COPY pyproject.toml README.md docs src Makefile TODO.md /app/

RUN pip install --no-cache-dir .

VOLUME ["/workspace"]

ENTRYPOINT ["numerus"]
CMD ["start"]
