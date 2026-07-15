FROM python:3.11-slim

WORKDIR /app

# Dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App y assets
COPY picker_hunt_app.py .
COPY static/ ./static/

# Base de datos con data inicial
RUN mkdir -p /data
COPY picker_hunt_99.db /data/picker_hunt.db

ENV DB_FILE=/data/picker_hunt.db

# Render inyecta PORT como variable de entorno
EXPOSE 8080

CMD ["sh", "-c", "uvicorn picker_hunt_app:app --host 0.0.0.0 --port ${PORT:-8080}"]
