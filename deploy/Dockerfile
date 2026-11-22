FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY contracts ./contracts
COPY config ./config
COPY console ./console
COPY db ./db
COPY services ./services
COPY scripts ./scripts
COPY tests ./tests

ARG PIP_EXTRAS=""
RUN if [ -n "$PIP_EXTRAS" ]; then \
      pip install --no-cache-dir ".[${PIP_EXTRAS}]"; \
    else \
      pip install --no-cache-dir .; \
    fi

CMD ["uvicorn", "services.case_api.app:app", "--host", "0.0.0.0", "--port", "8000"]