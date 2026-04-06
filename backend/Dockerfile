FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc cmake ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .
RUN python -c "from camel_tools.cli.camel_data import main; import sys; sys.argv = ['camel_data', '-i', 'light']; main()"

COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/
COPY scripts/ scripts/

COPY data/benchmarks/*.tsv data/benchmarks/
RUN mkdir -p data/logs data/audio data/podcasts data/podcasts/segments

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
