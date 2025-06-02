import json
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
import redis.asyncio as redis
import uvicorn
import logging
from .config import Config
from .models import Anomaly, SystemStatus

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Pydantic Response Models
class APIInfoResponse(BaseModel):
    message: str
    version: str

class AnomalyResponse(BaseModel):
    type: str
    timestamp: str
    sensor_id: str
    parameter: str
    value: float
    duration_seconds: Optional[int] = None
    message: str = ""
    
    class Config:
        schema_extra = {
            "example": {
                "type": "spike",
                "timestamp": "2025-05-19T10:15:00Z",
                "sensor_id": "wtf-pipe-1",
                "parameter": "pressure",
                "value": 4.5,
                "message": "Pressure spike detected: 4.5 exceeds threshold 4.0"
            }
        }

class AnomaliesResponse(BaseModel):
    anomalies: List[AnomalyResponse]
    count: int
    
    class Config:
        schema_extra = {
            "example": {
                "anomalies": [
                    {
                        "type": "drift",
                        "timestamp": "2025-06-02T16:41:22.934180+00:00",
                        "sensor_id": "wtf-pipe-1",
                        "parameter": "pressure",
                        "value": 4.9,
                        "duration_seconds": 16,
                        "message": "Pressure drift detected over 16 seconds."
                    }
                ],
                "count": 1
            }
        }

class SummaryResponse(BaseModel):
    summary: str
    timestamp: str
    generated_by: str
    anomaly_count: Optional[int] = None
    
    class Config:
        schema_extra = {
            "example": {
                "summary": "System operating normally with 2 minor anomalies detected in the last hour.",
                "timestamp": "2024-01-15T10:30:00Z",
                "generated_by": "llm_analyzer"
            }
        }

class SystemStatusResponse(BaseModel):
    sensor_active: bool
    detector_active: bool
    llm_active: bool
    api_active: bool
    last_reading_time: Optional[str] = None
    anomaly_count_24h: Optional[int] = None
    uptime_seconds: Optional[int] = None
    
    class Config:
        schema_extra = {
            "example": {
                "sensor_active": True,
                "detector_active": True,
                "llm_active": True,
                "api_active": True,
                "last_reading_time": "2024-01-15T10:29:45Z",
                "anomaly_count_24h": 5,
                "uptime_seconds": 3600
            }
        }

class AnomaliesByType(BaseModel):
    spike: int = 0
    drift: int = 0
    dropout: int = 0

class AnomaliesByParameter(BaseModel):
    temperature: int = 0
    pressure: int = 0
    flow: int = 0

class MetricsResponse(BaseModel):
    total_anomalies: int
    anomalies_by_type: AnomaliesByType
    anomalies_by_parameter: AnomaliesByParameter
    last_update: str
    
    class Config:
        schema_extra = {
            "example": {
                "total_anomalies": 15,
                "anomalies_by_type": {
                    "spike": 8,
                    "drift": 5,
                    "dropout": 2
                },
                "anomalies_by_parameter": {
                    "temperature": 6,
                    "pressure": 5,
                    "flow": 4
                },
                "last_update": "2024-01-15T10:30:00Z"
            }
        }

class HealthResponse(BaseModel):
    status: str
    timestamp: str
    
    class Config:
        schema_extra = {
            "example": {
                "status": "healthy",
                "timestamp": "2024-01-15T10:30:00Z"
            }
        }

# Request Models
class AnomalyQueryParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=1000, description="Number of anomalies to retrieve")
    
    @validator('limit')
    def validate_limit(cls, v):
        if v < 1:
            raise ValueError('Limit must be at least 1')
        if v > 1000:
            raise ValueError('Limit cannot exceed 1000')
        return v

class APIServer:
    def __init__(self, config: Config):
        self.config = config
        self.app = FastAPI(
            title="Water Treatment Facility Monitor",
            version="1.0.0",
            description="API for monitoring water treatment facility sensors and anomalies",
            docs_url="/docs",
            redoc_url="/redoc"
        )
        self.redis_client = None
        self.start_time = datetime.now(timezone.utc)
        
        # Setup CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        self.setup_routes()
    
    async def connect_redis(self):
        """Connect to Redis"""
        try:
            self.redis_client = redis.from_url(self.config.redis_url)
            await self.redis_client.ping()
            logger.info("API Server connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def setup_routes(self):
        """Setup API routes"""
        
        @self.app.on_event("startup")
        async def startup_event():
            await self.connect_redis()
        
        @self.app.on_event("shutdown")
        async def shutdown_event():
            if self.redis_client:
                await self.redis_client.close()
        
        @self.app.get(
            "/",
            response_model=APIInfoResponse,
            summary="Get API Information",
            description="Returns basic information about the API"
        )
        async def root() -> APIInfoResponse:
            return APIInfoResponse(
                message="Water Treatment Facility Monitor API",
                version="1.0.0"
            )
        
        @self.app.get(
            "/anomalies",
            response_model=AnomaliesResponse,
            summary="Get Recent Anomalies",
            description="Retrieve a list of recent anomalies detected by the system"
        )
        async def get_anomalies(
            limit: int = Query(
                default=50,
                ge=1,
                le=1000,
                description="Number of anomalies to retrieve (1-1000)"
            )
        ) -> AnomaliesResponse:
            """Get recent anomalies"""
            try:
                anomaly_data = await self.redis_client.lrange("anomalies", 0, limit - 1)
                anomalies = []
                
                for data in anomaly_data:
                    anomaly_dict = json.loads(data)
                    anomaly = Anomaly.from_dict(anomaly_dict)
                    
                    # Convert to response model
                    anomaly_response = AnomalyResponse(
                        type = anomaly.type,
                        timestamp = anomaly.timestamp,
                        sensor_id = anomaly.sensor_id,
                        parameter = anomaly.parameter,
                        value = anomaly.value,
                        duration_seconds = anomaly.duration_seconds,
                        message = anomaly.message
                    )
                    anomalies.append(anomaly_response)
                
                return AnomaliesResponse(
                    anomalies=anomalies,
                    count=len(anomalies)
                )
                
            except Exception as e:
                logger.error(f"Error getting anomalies: {e}")
                raise HTTPException(status_code=500, detail="Failed to retrieve anomalies")
        
        @self.app.get(
            "/summary",
            response_model=SummaryResponse,
            summary="Get System Summary",
            description="Get the latest LLM-generated summary of system status"
        )
        async def get_summary() -> SummaryResponse:
            """Get latest LLM-generated summary"""
            try:
                summary_data = await self.redis_client.get("latest_summary")
                if summary_data:
                    summary_dict = json.loads(summary_data)
                    return SummaryResponse(
                        summary=summary_dict.get("summary", ""),
                        timestamp=summary_dict.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        generated_by=summary_dict.get("generated_by", "LLM Summarizer"),
                        anomaly_count=summary_dict.get("anomaly_count")
                    )
                else:
                    return SummaryResponse(
                        summary="No summary available yet. System is initializing.",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        generated_by="api_fallback"
                    )
            except Exception as e:
                logger.error(f"Error getting summary: {e}")
                raise HTTPException(status_code=500, detail="No summary available yet. System is initializing. Utilize /metrics endpoint for anomaly summary till system is back up")
        
        @self.app.get(
            "/status",
            response_model=SystemStatusResponse,
            summary="Get System Status",
            description="Get the current health status of all system components"
        )
        async def get_status() -> SystemStatusResponse:
            """Get system health status"""
            try:
                # Calculate uptime
                uptime_seconds = int((datetime.now(timezone.utc) - self.start_time).total_seconds())
                
                status_data = {
                    "sensor_active": False,
                    "detector_active": False,
                    "llm_active": False,
                    "api_active": True,
                    "uptime_seconds": uptime_seconds
                }
                
                # Check if sensor is active (recent reading exists)
                last_reading = await self.redis_client.get("last_reading")
                if last_reading:
                    reading_data = json.loads(last_reading)
                    status_data["last_reading_time"] = reading_data.get("timestamp")
                    status_data["sensor_active"] = True
                
                # Check if detector is active (recent anomalies or activity)
                anomaly_count = await self.redis_client.llen("anomalies")
                if anomaly_count is not None:
                    status_data["detector_active"] = True
                    status_data["anomaly_count_24h"] = anomaly_count
                
                # Check if LLM is active (recent summary exists) 
                summary_data = await self.redis_client.get("latest_summary")
                if summary_data:
                    status_data["llm_active"] = True
                
                return SystemStatusResponse(**status_data)
                
            except Exception as e:
                logger.error(f"Error getting status: {e}")
                raise HTTPException(status_code=500, detail="Failed to retrieve status")
        
        @self.app.get(
            "/metrics",
            response_model=MetricsResponse,
            summary="Get System Metrics",
            description="Get detailed metrics about anomalies and system performance"
        )
        async def get_metrics() -> MetricsResponse:
            """Get system metrics"""
            try:
                anomalies_by_type = AnomaliesByType()
                anomalies_by_parameter = AnomaliesByParameter()
                
                # Get all anomalies for metrics
                anomaly_data = await self.redis_client.lrange("anomalies", 0, -1)
                total_anomalies = len(anomaly_data)
                
                for data in anomaly_data:
                    anomaly = Anomaly.from_dict(json.loads(data))
                    
                    # Count by type
                    if hasattr(anomalies_by_type, anomaly.type):
                        current_count = getattr(anomalies_by_type, anomaly.type)
                        setattr(anomalies_by_type, anomaly.type, current_count + 1)
                    
                    # Count by parameter
                    if hasattr(anomalies_by_parameter, anomaly.parameter):
                        current_count = getattr(anomalies_by_parameter, anomaly.parameter)
                        setattr(anomalies_by_parameter, anomaly.parameter, current_count + 1)
                
                return MetricsResponse(
                    total_anomalies=total_anomalies,
                    anomalies_by_type=anomalies_by_type,
                    anomalies_by_parameter=anomalies_by_parameter,
                    last_update=datetime.now(timezone.utc).isoformat()
                )
                
            except Exception as e:
                logger.error(f"Error getting metrics: {e}")
                raise HTTPException(status_code=500, detail="Failed to retrieve metrics")
        
        @self.app.get(
            "/health",
            response_model=HealthResponse,
            summary="Health Check",
            description="Simple health check endpoint to verify API and Redis connectivity"
        )
        async def health_check() -> HealthResponse:
            """Simple health check endpoint"""
            try:
                await self.redis_client.ping()
                return HealthResponse(
                    status="healthy",
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
            except Exception as e:
                logger.error(f"Health check failed: {e}")
                raise HTTPException(status_code=503, detail="Service unavailable")

def create_app():
    """Create FastAPI app"""
    config = Config()
    server = APIServer(config)
    return server.app

async def main():
    config = Config()
    server = APIServer(config)
    
    config_uvicorn = uvicorn.Config(
        server.app,
        host=config.api.host,
        port=config.api.port,
        log_level=config.log_level.lower()
    )
    
    server_uvicorn = uvicorn.Server(config_uvicorn)
    
    try:
        logger.info(f"Starting API server on {config.api.host}:{config.api.port}")
        await server_uvicorn.serve()
    except KeyboardInterrupt:
        logger.info("API Server stopped")

if __name__ == "__main__":
    asyncio.run(main())