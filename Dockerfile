FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend backend
COPY frontend frontend
COPY pipeline pipeline
COPY data data
# The Tuesday NIBRS rebuild matches permit dates from this file. DuckDB is
# loaded from the files that rebuild writes, so it is not available here.
COPY prediction_data/ladbs_tse_permits.csv prediction_data/ladbs_tse_permits.csv

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
