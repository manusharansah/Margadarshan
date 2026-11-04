FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MARGA_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gis_backend ./gis_backend
RUN useradd -r -u 10001 marga && mkdir /data && chown marga /data
USER marga
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=4).status==200 else 1)"
CMD ["sh", "-c", "python -m gis_backend.fetch_artifact && exec uvicorn gis_backend.api:app --host 0.0.0.0 --port 8000 --proxy-headers"]
