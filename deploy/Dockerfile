FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY contracts ./contracts
COPY config ./config
COPY db ./db
COPY services ./services
COPY scripts ./scripts
COPY tests ./tests

RUN pip install --no-cache-dir .

CMD ["uvicorn", "services.case_api.app:app", "--host", "0.0.0.0", "--port", "8000"]