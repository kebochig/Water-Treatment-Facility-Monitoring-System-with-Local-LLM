# Water Treatment Facility Monitoring System

A comprehensive real-time monitoring system for water treatment facilities that simulates sensor data, detects anomalies, and generates human-readable summaries using a local LLM.

## 🏗️ System Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Sensor         │    │  Anomaly        │    │  LLM            │
│  Simulator      │───▶│  Detector       │───▶│  Summarizer     │
│                 │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │              ┌────────▼────────┐              │
         └─────────────▶│     Redis       │◀─────────────┘
                        │   Message Bus   │
                        └─────────────────┘
                                 │
                        ┌────────▼────────┐
                        │   REST API      │
                        │   Server        │
                        └─────────────────┘
```

## 🚀 Quick Start

### Prerequisites
- Docker and Docker Compose
- At least 4GB of available RAM (for LLM)
- 3GB of free disk space (for LLM model download)

### Installation

1. **Clone and setup:**
```bash
git clone <repository-url>
cd wtf-monitoring
```

2. **Start the system from terminal:**
```bash
# Stop existing containers
docker-compose down

# Run containers
docker-compose up -d
```

3. **Verify deployment:**
```bash
# Check all services are running
docker-compose ps

# Check API health
curl http://localhost:8000/health
```

4. **Access the API:**
- API Documentation: http://localhost:8000/docs
- Health Check: http://localhost:8000/health
- System Status: http://localhost:8000/status

## 📊 API Endpoints

### GET /anomalies
Returns recent anomalies detected by the system.

**Parameters:**
- `limit` (optional): Maximum number of anomalies to return (default: 50)

**Response:**
```json
[
  {
    "type": "spike",
    "timestamp": "2025-05-19T10:15:00Z",
    "sensor_id": "wtf-pipe-1",
    "parameter": "pressure",
    "value": 4.5,
    "message": "Pressure spike detected: 4.5 exceeds threshold 4.0"
  }
]
```

### GET /summary
Returns the latest LLM-generated summary of system status and anomalies. Summaries are generated within a 3 mins terminal.

**Response:**
```json
{
  "summary": "Between 10:20 and 10:22, a temperature drift occurred on wtf-pipe-1...",
  "timestamp": "2025-05-19T10:25:00Z",
  "generated_by": "llm_summarizer"
}
```
Note that before you can use this service, the LLM model needs to be download in the Ollama service model bank which is triggered as part of the environment setup.
Run `docker-compose logs ollama-init` to check model download status.


### GET /status
Returns overall system health and component status.

**Response:**
```json
{
  "sensor_active": true,
  "detector_active": true,
  "llm_active": true,
  "api_active": true,
  "last_reading_time": "2025-05-19T10:25:00Z",
  "anomaly_count_24h": 12
}
```

### GET /metrics
Returns detailed system metrics and anomaly statistics on the last 100 anomalies for quick reporting.

**Response:**
```json
{
  "total_anomalies": 100,
  "anomalies_by_type": {
    "spike": 79,
    "drift": 7,
    "dropout": 14
  },
  "anomalies_by_parameter": {
    "temperature": 4,
    "pressure": 8,
    "flow": 74
  },
  "last_update": "2025-05-19T14:11:18.009010+00:00"
}
```

### GET /health
Simple health check endpoint for monitoring.

## ⚙️ Configuration

### Sensor Parameters
- **Temperature:** Normal range 10-35°C, spike threshold (far off normal range) >45°C, drift threshold (slightly above normal range) >38°C
- **Pressure:** Normal range 1.0-3.0 bar, spike threshold >4.0 bar, drift threshold >3.5 bar  
- **Flow:** Normal range 20-100 L/min, spike threshold >120 L/min, drift threshold >110 L/min

### Anomaly Detection Thresholds

#### Spike Detection
Detects single readings that exceed critical thresholds:
- Temperature > 45°C
- Pressure > 4.0 bar
- Flow > 120 L/min

#### Drift Detection  
Detects sustained abnormal values:
- Temperature > 38°C for >15 seconds
- Pressure > 3.5 bar for >15 seconds
- Flow > 110 L/min for >15 seconds

#### Dropout Detection
Detects sensor communication failures:
- No data received for >10 seconds

### LLM Configuration
- **Model:** Llama 3.2 1B
- **Context Length:** 128,000 tokens
- **Max Response:** 2,048 tokens
- **Temperature:** 0.3 (focused, less creative responses)

## 🔧 Development

### Project Structure
```
wtf-monitoring/
├── src/
│   ├── config.py           # System configuration
│   ├── models.py           # Data models
│   ├── sensor_simulator.py # Sensor data generation
│   ├── anomaly_detector.py # Anomaly detection logic
│   ├── llm_summarizer.py   # LLM integration
│   └── api_server.py       # REST API server
├── docker-compose.yml      # Service orchestration
├── Dockerfile.*            # Service containers for proper seperation of concerns
├── pull-model.sh           # Bash script for model download
├── README.md               # Readme file
├── requirements-llm.txt    # Python dependencies for LLM
└── requirements.txt        # Python dependencies
```

### Running Individual Components

**Sensor Simulator:**
```bash
python -m src.sensor_simulator
```

**Anomaly Detector:**
```bash
python -m src.anomaly_detector
```

**LLM Summarizer:**
```bash
python -m src.llm_summarizer
```

**API Server:**
```bash
python -m src.api_server
```

### Customization

#### Modify Detection Thresholds
Edit `src/config.py`:
```python
@dataclass
class AnomalyConfig:
    spike_thresholds: Dict[str, float] = None
    drift_thresholds: Dict[str, float] = None
    drift_duration: int = 15  # seconds
```


## 🔍 Monitoring and Observability

### Logs
All services log to `/logs` directory:
- `docker-compose logs sensor` - Sensor simulation logs
- `docker-compose logs detector` - Anomaly detection logs  
- `docker-compose logs llm` - LLM processing logs
- `docker-compose logs api` - API server logs

### Health Monitoring
- **Service Health:** `GET /health`
- **System Status:** `GET /status`
- **Component Metrics:** `GET /metrics`

### Real-time Monitoring
```bash
# Watch sensor readings
docker-compose logs -f sensor

# Watch anomaly alerts
docker-compose logs -f detector

# Monitor API requests
docker-compose logs -f api

# Track LLM summary generation
docker-compose logs -f llm
```

## 🔒 Security Considerations

### Network Security
- All services communicate via internal Docker network
- Only API server exposes external port (8000)
- Redis is not exposed externally

### Data Security
- No persistent storage of sensitive data
- Anomaly data expires after 24 hours
- All processing happens locally (no external API calls)

### Resource Security
- LLM runs with limited memory allocation
- Services restart automatically on failure
- Rate limiting can be added to API endpoints

## 🐛 Troubleshooting

### Common Issues

**LLM Service Won't Start:**
- Ensure sufficient memory (4GB+)
- Check Ollama container logs for server status: `docker-compose logs ollama`
- Confirm LLM models available: `docker exec -it [ollama_container_name] ollama list`

**No Anomalies Detected:**
- Check sensor simulator is running: `docker-compose logs sensor`
- Verify Redis connectivity: `docker-compose logs redis`
- Review detection thresholds in config

**API Timeouts:**
- LLM summarization can take 30-60 seconds initially
- Increase client timeout or check LLM service logs

**High Memory Usage:**
- LLM service uses 2-4GB RAM normally
- Reduce model size or increase swap if needed

### Debug Commands
```bash
# Check service status
docker-compose ps

# View all logs
docker-compose logs

# Restart specific service  
docker-compose restart llm

# Access Redis CLI
docker-compose exec redis redis-cli

# Check resource usage
docker stats
```

## 📈 Performance Optimization

### For Production Deployment:
1. **Use GPU acceleration** for LLM inference
2. **Implement connection pooling** for Redis
3. **Add caching layers** for API responses
4. **Configure log rotation** to prevent disk filling
5. **Set up monitoring** with Prometheus/Grafana
6. **Implement backup strategy** for anomaly data

### Scaling Considerations:
- **Multiple sensors:** Extend simulator for multiple sensor IDs
- **Distributed deployment:** Use Redis Cluster for scaling
- **Load balancing:** Add multiple API server instances
- **Data persistence:** Add PostgreSQL for long-term storage

## 📝 License

MIT License - see LICENSE file for details.

## 🤝 Improvement

1. Add GPU config for faster response
2. Add tests for new functionality  

## 📞 Support

For issues and questions:
- Check troubleshooting section above
- Review service logs
- Create GitHub issue with full error details

##  Developer
- Chigozilai Kejeh
- kebochig@gmail.com
