FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY oneview_exporter/ oneview_exporter/

EXPOSE 9130
ENTRYPOINT ["python", "-m", "oneview_exporter.main"]
