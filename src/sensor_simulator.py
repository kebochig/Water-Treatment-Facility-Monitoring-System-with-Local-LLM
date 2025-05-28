import asyncio
import json
import random
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import redis.asyncio as redis
import logging
from .config import Config
from .models import SensorReading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class SensorSimulator:
    def __init__(self, config: Config):
        self.config = config
        self.redis_client = None
        self.running = False
        
        # Anomaly state tracking
        self.drift_state = {
            'active': False,
            'start_time': None,
            'duration': 0,
            'target_duration': 0,
            'drift_type': None,  # 'temp', 'pressure', or 'flow'
            'drift_values': {}
        }
        
        self.dropout_state = {
            'active': False,
            'start_time': None,
            'duration': 0,
            'target_duration': 0
        }
        
    async def connect_redis(self):
        """Connect to Redis"""
        try:
            self.redis_client = redis.from_url(self.config.redis_url)
            await self.redis_client.ping()
            logger.info("Connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def _should_start_drift(self) -> bool:
        """Determine if drift anomaly should start (4% chance per reading)"""
        return not self.drift_state['active'] and random.random() < 0.04
    
    def _should_start_dropout(self) -> bool:
        """Determine if dropout anomaly should start (2% chance per reading)"""
        return not self.dropout_state['active'] and random.random() < 0.02
    
    def _initialize_drift(self):
        """Initialize drift anomaly parameters"""
        self.drift_state['active'] = True
        self.drift_state['start_time'] = time.time()
        self.drift_state['target_duration'] = random.uniform(15, 45)  # 15-45 seconds
        self.drift_state['drift_type'] = random.choice(['temp', 'pressure', 'flow'])
        
        # Set drift target values (outside normal ranges)
        if self.drift_state['drift_type'] == 'temp':
            self.drift_state['drift_values']['temp'] = random.uniform(35, 45)
        elif self.drift_state['drift_type'] == 'pressure':
            self.drift_state['drift_values']['pressure'] = random.uniform(4.5, 5.5)
        else:  # flow
            self.drift_state['drift_values']['flow'] = random.uniform(120, 140)
            
        logger.info(f"Starting drift anomaly: {self.drift_state['drift_type']} for {self.drift_state['target_duration']:.1f}s")
    
    def _initialize_dropout(self):
        """Initialize dropout anomaly parameters"""
        self.dropout_state['active'] = True
        self.dropout_state['start_time'] = time.time()
        self.dropout_state['target_duration'] = random.uniform(10, 30)  # 10-30 seconds
        
        logger.info(f"Starting dropout anomaly for {self.dropout_state['target_duration']:.1f}s")
    
    def _update_anomaly_states(self):
        """Update the state of active anomalies"""
        current_time = time.time()
        
        # Update drift state
        if self.drift_state['active']:
            self.drift_state['duration'] = current_time - self.drift_state['start_time']
            if self.drift_state['duration'] >= self.drift_state['target_duration']:
                logger.info(f"Ending drift anomaly after {self.drift_state['duration']:.1f}s")
                self.drift_state['active'] = False
                self.drift_state['drift_values'] = {}
        
        # Update dropout state
        if self.dropout_state['active']:
            self.dropout_state['duration'] = current_time - self.dropout_state['start_time']
            if self.dropout_state['duration'] >= self.dropout_state['target_duration']:
                logger.info(f"Ending dropout anomaly after {self.dropout_state['duration']:.1f}s")
                self.dropout_state['active'] = False
    
    def generate_reading(self) -> Optional[SensorReading]:
        """Generate a sensor reading with various anomaly types"""
        # Update anomaly states
        self._update_anomaly_states()
        
        # Check if we should start new anomalies
        if self._should_start_drift():
            self._initialize_drift()
        
        if self._should_start_dropout():
            self._initialize_dropout()
        
        # Handle dropout - return None (no reading)
        if self.dropout_state['active']:
            logger.info("Dropout active - no reading generated")
            return None
        
        now = datetime.now(timezone.utc).isoformat()
        
        # Generate base readings
        temp_base = random.uniform(*self.config.sensor.temperature_range)
        pressure_base = random.uniform(*self.config.sensor.pressure_range)
        flow_base = random.uniform(*self.config.sensor.flow_range)
        
        # Apply drift anomaly if active
        if self.drift_state['active']:
            drift_type = self.drift_state['drift_type']
            progress = min(1.0, self.drift_state['duration'] / 10.0)  # Gradual drift over 10 seconds
            
            if drift_type == 'temp' and 'temp' in self.drift_state['drift_values']:
                target = self.drift_state['drift_values']['temp']
                temp_base = temp_base + (target - temp_base) * progress
            elif drift_type == 'pressure' and 'pressure' in self.drift_state['drift_values']:
                target = self.drift_state['drift_values']['pressure']
                pressure_base = pressure_base + (target - pressure_base) * progress
            elif drift_type == 'flow' and 'flow' in self.drift_state['drift_values']:
                target = self.drift_state['drift_values']['flow']
                flow_base = flow_base + (target - flow_base) * progress
        
        # Apply spike anomaly (original logic) - 6% chance if no drift active
        elif random.random() < 0.06:
            choice = random.choice(["temp", "pressure", "flow"])
            if choice == "temp":
                temp_base = random.uniform(40, 50)  # Anomalous temperature
                logger.info("Spike anomaly: temperature")
            elif choice == "pressure":
                pressure_base = random.uniform(4.5, 6.0)  # Anomalous pressure
                logger.info("Spike anomaly: pressure")
            else:
                flow_base = random.uniform(130, 150)  # Anomalous flow
                logger.info("Spike anomaly: flow")
        
        return SensorReading(
            timestamp=now,
            sensor_id=self.config.sensor.sensor_id,
            temperature=round(temp_base, 1),
            pressure=round(pressure_base, 1),
            flow=round(flow_base, 1)
        )
    
    async def publish_reading(self, reading: SensorReading):
        """Publish reading to Redis"""
        try:
            await self.redis_client.publish("sensor_readings", reading.to_json())
            await self.redis_client.setex("last_reading", 30, reading.to_json())
            logger.debug(f"Published reading: {reading.to_json()}")
        except Exception as e:
            logger.error(f"Failed to publish reading: {e}")
    
    async def run(self):
        """Main simulation loop"""
        await self.connect_redis()
        self.running = True
        logger.info(f"Starting sensor simulation for {self.config.sensor.sensor_id}")
        
        try:
            while self.running:
                reading = self.generate_reading()
                
                if reading is not None:
                    # Normal reading or anomalous reading
                    logger.info(reading)
                    await self.publish_reading(reading)
                else:
                    # Dropout - no reading to publish
                    logger.info("No reading due to dropout anomaly")
                
                await asyncio.sleep(self.config.sensor.emission_interval)
        except Exception as e:
            logger.error(f"Sensor simulation error: {e}")
        finally:
            if self.redis_client:
                await self.redis_client.close()
    
    def stop(self):
        """Stop the simulation"""
        self.running = False
        logger.info("Stopping sensor simulation")

async def main():
    config = Config()
    simulator = SensorSimulator(config)
    try:
        await simulator.run()
    except KeyboardInterrupt:
        simulator.stop()
        logger.info("Sensor simulator stopped")

if __name__ == "__main__":
    asyncio.run(main())