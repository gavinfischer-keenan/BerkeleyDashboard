FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir hatchling

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8090

CMD ["berkeley-dashboard"]
