import json
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

class APIServer:
    def __init__(self, config: Config):
        self.config = config
        self.app = FastAPI(title="Water Treatment Facility Monitor", version="1.0.0")
        self.redis_client = None
        
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
        
        @self.app.get("/")
        async def root():
            return {"message": "Water Treatment Facility Monitor API", "version": "1.0.0"}
        
        @self.app.get("/anomalies")
        async def get_anomalies(limit: int = 50) -> List[Dict[str, Any]]:
            """Get recent anomalies"""
            try:
                anomaly_data = await self.redis_client.lrange("anomalies", 0, limit - 1)
                anomalies = []
                for data in anomaly_data:
                    anomaly = Anomaly.from_dict(json.loads(data))
                    anomalies.append(anomaly.to_dict())
                return anomalies
            except Exception as e:
                logger.error(f"Error getting anomalies: {e}")
                raise HTTPException(status_code=500, detail="Failed to retrieve anomalies")
        
        @self.app.get("/summary")
        async def get_summary() -> Dict[str, Any]:
            """Get latest LLM-generated summary"""
            try:
                summary_data = await self.redis_client.get("latest_summary")
                if summary_data:
                    return json.loads(summary_data)
                else:
                    return {
                        "summary": "No summary available yet. System is initializing.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "generated_by": "api_fallback"
                    }
            except Exception as e:
                logger.error(f"Error getting summary: {e}")
                raise HTTPException(status_code=500, detail="Failed to retrieve summary")
        
        @self.app.get("/status")
        async def get_status() -> Dict[str, Any]:
            """Get system health status"""
            try:
                status = SystemStatus(
                    sensor_active=False,
                    detector_active=False,
                    llm_active=False,
                    api_active=True
                )
                
                # Check if sensor is active (recent reading exists)
                last_reading = await self.redis_client.get("last_reading")
                if last_reading:
                    reading_data = json.loads(last_reading)
                    status.last_reading_time = reading_data.get("timestamp")
                    status.sensor_active = True
                
                # Check if detector is active (recent anomalies or activity)
                anomaly_count = await self.redis_client.llen("anomalies")
                if anomaly_count is not None:
                    status.detector_active = True
                    status.anomaly_count_24h = anomaly_count
                
                # Check if LLM is active (recent summary exists) 
                summary_data = await self.redis_client.get("latest_summary")
                if summary_data:
                    status.llm_active = True
                
                return status.to_dict()
                
            except Exception as e:
                logger.error(f"Error getting status: {e}")
                raise HTTPException(status_code=500, detail="Failed to retrieve status")
        
        @self.app.get("/metrics")
        async def get_metrics() -> Dict[str, Any]:
            """Get system metrics"""
            try:
                metrics = {
                    "total_anomalies": 0,
                    "anomalies_by_type": {"spike": 0, "drift": 0, "dropout": 0},
                    "anomalies_by_parameter": {"temperature": 0, "pressure": 0, "flow": 0},
                    "last_update": datetime.now(timezone.utc).isoformat()
                }
                
                # Get all anomalies for metrics
                anomaly_data = await self.redis_client.lrange("anomalies", 0, -1)
                metrics["total_anomalies"] = len(anomaly_data)
                
                for data in anomaly_data:
                    anomaly = Anomaly.from_dict(json.loads(data))
                    
                    # Count by type
                    if anomaly.type in metrics["anomalies_by_type"]:
                        metrics["anomalies_by_type"][anomaly.type] += 1
                    
                    # Count by parameter
                    if anomaly.parameter in metrics["anomalies_by_parameter"]:
                        metrics["anomalies_by_parameter"][anomaly.parameter] += 1
                
                return metrics
                
            except Exception as e:
                logger.error(f"Error getting metrics: {e}")
                raise HTTPException(status_code=500, detail="Failed to retrieve metrics")
        
        @self.app.get("/health")
        async def health_check():
            """Simple health check endpoint"""
            try:
                await self.redis_client.ping()
                return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}
            except Exception as e:
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
        await server_uvicorn.serve()
    except KeyboardInterrupt:
        logger.info("API Server stopped")

if __name__ == "__main__":
    asyncio.run(main())