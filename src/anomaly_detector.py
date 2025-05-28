import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from collections import deque, defaultdict
import redis.asyncio as redis
import logging
from .config import Config
from .models import SensorReading, Anomaly, AnomalyType

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class AnomalyDetector:
    def __init__(self, config: Config):
        self.config = config
        self.redis_client = None
        self.running = False
        
        # Store recent readings for drift detection
        self.reading_history = deque(maxlen=100)
        self.parameter_history = defaultdict(lambda: deque(maxlen=50))
        self.last_reading_time = defaultdict(float)
        self.drift_start_times = defaultdict(lambda: None)
        
    async def connect_redis(self):
        """Connect to Redis"""
        try:
            self.redis_client = redis.from_url(self.config.redis_url)
            await self.redis_client.ping()
            logger.info("Anomaly detector connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def detect_spike(self, reading: SensorReading) -> List[Anomaly]:
        """Detect spike anomalies"""
        anomalies = []
        thresholds = self.config.anomaly.spike_thresholds
        
        parameters = {
            "temperature": reading.temperature,
            "pressure": reading.pressure, 
            "flow": reading.flow
        }
        
        for param, value in parameters.items():
            if value > thresholds[param]:
                anomaly = Anomaly(
                    type=AnomalyType.SPIKE.value,
                    timestamp=reading.timestamp,
                    sensor_id=reading.sensor_id,
                    parameter=param,
                    value=value,
                    message=f"{param.capitalize()} spike detected: {value} exceeds threshold {thresholds[param]}"
                )
                anomalies.append(anomaly)
                logger.warning(f"Spike detected: {anomaly.message}")
        
        return anomalies
    
    def detect_drift(self, reading: SensorReading) -> List[Anomaly]:
        """Detect drift anomalies"""
        anomalies = []
        thresholds = self.config.anomaly.drift_thresholds
        current_time = time.time()
        
        parameters = {
            "temperature": reading.temperature,
            "pressure": reading.pressure,
            "flow": reading.flow
        }
        
        for param, value in parameters.items():
            param_key = f"{reading.sensor_id}_{param}"
            
            if value > thresholds[param]:
                # Value is above drift threshold
                if self.drift_start_times[param_key] is None:
                    self.drift_start_times[param_key] = current_time
                else:
                    duration = current_time - self.drift_start_times[param_key]
                    if duration >= self.config.anomaly.drift_duration:
                        anomaly = Anomaly(
                            type=AnomalyType.DRIFT.value,
                            timestamp=reading.timestamp,
                            sensor_id=reading.sensor_id,
                            parameter=param,
                            value=value,
                            duration_seconds=int(duration),
                            message=f"{param.capitalize()} drift detected over {int(duration)} seconds."
                        )
                        anomalies.append(anomaly)
                        logger.warning(f"Drift detected: {anomaly.message}")
                        # Reset to avoid duplicate alerts
                        self.drift_start_times[param_key] = current_time
            else:
                # Value is back to normal, reset drift tracking
                self.drift_start_times[param_key] = None
        
        return anomalies
    
    def detect_dropout(self) -> List[Anomaly]:
        """Detect sensor dropout"""
        anomalies = []
        current_time = time.time()
        
        for sensor_id, last_time in self.last_reading_time.items():
            if current_time - last_time > self.config.anomaly.dropout_timeout:
                anomaly = Anomaly(
                    type=AnomalyType.DROPOUT.value,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    sensor_id=sensor_id,
                    parameter="all",
                    value=0.0,
                    duration_seconds=int(current_time - last_time),
                    message=f"Sensor dropout detected. No data for {int(current_time - last_time)} seconds."
                )
                anomalies.append(anomaly)
                logger.warning(f"Dropout detected: {anomaly.message}")
                # Update to avoid duplicate alerts
                self.last_reading_time[sensor_id] = current_time
        
        return anomalies
    
    async def process_reading(self, reading: SensorReading):
        """Process a sensor reading for anomalies"""
        self.reading_history.append(reading)
        self.last_reading_time[reading.sensor_id] = time.time()
        
        # Detect all types of anomalies
        anomalies = []
        anomalies.extend(self.detect_spike(reading))
        anomalies.extend(self.detect_drift(reading))
        
        # Store anomalies in Redis
        for anomaly in anomalies:
            await self.store_anomaly(anomaly)
    
    async def store_anomaly(self, anomaly: Anomaly):
        """Store anomaly in Redis"""
        try:
            # Store in a list with expiration
            await self.redis_client.lpush("anomalies", anomaly.to_json())
            await self.redis_client.ltrim("anomalies", 0, self.config.api.max_stored_anomalies - 1)
            await self.redis_client.expire("anomalies", 86400)  # 24 hours
            
            # Publish for real-time notifications
            await self.redis_client.publish("anomaly_alerts", anomaly.to_json())
            
        except Exception as e:
            logger.error(f"Failed to store anomaly: {e}")
    
    async def check_dropouts(self):
        """Periodic check for sensor dropouts"""
        while self.running:
            try:
                dropout_anomalies = self.detect_dropout()
                for anomaly in dropout_anomalies:
                    await self.store_anomaly(anomaly)
                    
                await asyncio.sleep(5)  # Check every 5 seconds
            except Exception as e:
                logger.error(f"Error checking dropouts: {e}")
                await asyncio.sleep(5)
    
    async def run(self):
        """Main detection loop"""
        await self.connect_redis()
        self.running = True
        logger.info("Starting anomaly detection")
        
        # Start dropout monitoring task
        dropout_task = asyncio.create_task(self.check_dropouts())
        
        try:
            # Subscribe to sensor readings
            pubsub = self.redis_client.pubsub()
            await pubsub.subscribe("sensor_readings")
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        reading = SensorReading.from_dict(data)
                        await self.process_reading(reading)
                    except Exception as e:
                        logger.error(f"Error processing reading: {e}")
                        
        except Exception as e:
            logger.error(f"Anomaly detection error: {e}")
        finally:
            self.running = False
            dropout_task.cancel()
            if self.redis_client:
                await self.redis_client.close()
    
    def stop(self):
        """Stop the detector"""
        self.running = False
        logger.info("Stopping anomaly detector")

async def main():
    config = Config()
    detector = AnomalyDetector(config)
    
    try:
        await detector.run()
    except KeyboardInterrupt:
        detector.stop()
        logger.info("Anomaly detector stopped")

if __name__ == "__main__":
    asyncio.run(main())