# HPE OneView Prometheus Exporter

A Prometheus exporter for [HPE OneView](https://www.hpe.com/us/en/integrated-systems/software.html) that exposes hardware metrics at `/metrics`. Tested with OneView v9.30 (API version 7200).

## Quick start

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` with your OneView appliance address and credentials:

```bash
OV_HOST=oneview.example.com
OV_USERNAME=monitoring
OV_PASSWORD=secret
OV_LOGIN_DOMAIN=LOCAL          # omit or set to your LDAP domain
TLS_INSECURE=true              # set to false once you provide a CA bundle
```

### 2. Run

**With Docker:**

```bash
docker compose up --build
```

Or without Compose:

```bash
docker build -t oneview-exporter .
docker run -d -p 9130:9130 --env-file .env oneview-exporter
```

**Without Docker:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' .env | grep -v '^\s*$' | xargs)
python -m oneview_exporter.main
```

### 3. Verify

Wait ~60 seconds for the first poll, then:

```bash
# Check the exporter is up and polling successfully
curl -s http://localhost:9130/metrics | grep oneview_scrape_success
# oneview_scrape_success 1.0

# Check server hardware is being collected
curl -s http://localhost:9130/metrics | grep oneview_server_hardware_status
```

### 4. Point Prometheus at it

Add to your `prometheus.yml` (see also `examples/prometheus.yml`):

```yaml
scrape_configs:
  - job_name: oneview
    scrape_interval: 60s
    scrape_timeout: 10s
    static_configs:
      - targets: ["localhost:9130"]
```

## Configuration

All settings are via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OV_HOST` | *(required)* | OneView appliance hostname |
| `OV_USERNAME` | *(required)* | OneView username |
| `OV_PASSWORD` | *(required)* | OneView password |
| `OV_LOGIN_DOMAIN` | | Login domain (e.g. `LOCAL`, or your LDAP domain) |
| `OV_API_VERSION` | `7200` | OneView REST API version |
| `OV_SCRAPE_INTERVAL` | `60` | Seconds between background polls |
| `EXPORTER_PORT` | `9130` | HTTP port for `/metrics` |
| `TLS_CA_BUNDLE` | | Path to a CA bundle PEM for OneView HTTPS |
| `TLS_INSECURE` | `false` | Skip TLS verification (development only) |

## Metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `oneview_server_hardware_status` | name, model, serial_number, uri | 0=Unknown, 1=OK, 2=Warning, 3=Critical, 4=Disabled |
| `oneview_server_hardware_power_state` | name, model | 0=Unknown, 1=On, 2=Off |
| `oneview_server_hardware_ambient_temperature_celsius` | name | Ambient temperature (C) |
| `oneview_server_hardware_cpu_utilization_percent` | name | CPU utilization (%) |
| `oneview_server_hardware_average_power_watts` | name | Average power draw (W) |
| `oneview_server_hardware_peak_power_watts` | name | Peak power draw (W) |
| `oneview_server_hardware_memory_mb` | name, model | Installed memory (MB) |
| `oneview_server_hardware_processor_count` | name, model | Processor count |
| `oneview_enclosure_status` | name, serial_number, uri | Enclosure health status |
| `oneview_enclosure_ambient_temperature_celsius` | name | Enclosure temperature (C) |
| `oneview_enclosure_average_power_watts` | name | Enclosure average power (W) |
| `oneview_enclosure_peak_power_watts` | name | Enclosure peak power (W) |
| `oneview_interconnect_status` | name, model, serial_number, uri | Interconnect health status |
| `oneview_active_alerts` | severity | Active alert count by severity |
| `oneview_scrape_duration_seconds` | | Time spent polling OneView |
| `oneview_scrape_success` | | 1 if the last poll succeeded, 0 otherwise |

## Dry-run mode

To test the exporter without a real OneView appliance, use `--dry-run`. It serves simulated data — no credentials or connectivity needed:

```bash
python -m oneview_exporter.main --dry-run
# or
docker run -p 9130:9130 oneview-exporter --dry-run
```

## How it works

The exporter runs a background thread that polls the OneView REST API every `OV_SCRAPE_INTERVAL` seconds and caches the results in memory. When Prometheus scrapes `/metrics`, it gets the cached values instantly. This avoids overloading the OneView API when multiple Prometheus instances scrape simultaneously.

If a poll fails, stale data is served and `oneview_scrape_success` is set to `0`. Sessions are renewed automatically on `401` responses.

## Examples

The `examples/` directory contains reference files you can import into your own stack:

- `prometheus.yml` — Prometheus scrape configuration
- `grafana-dashboard.json` — Grafana dashboard for hardware overview
- `grafana-alert-rules.yml` — Grafana alert rules for hardware health

## License

MIT
