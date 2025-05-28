from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum
import json

class AnomalyType(Enum):
    SPIKE = "spike"
    DRIFT = "drift" 
    DROPOUT = "dropout"

@dataclass
class SensorReading:
    timestamp: str
    sensor_id: str
    temperature: float
    pressure: float
    flow: float
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SensorReading':
        return cls(**data)

@dataclass
class Anomaly:
    type: str
    timestamp: str
    sensor_id: str
    parameter: str
    value: float
    duration_seconds: Optional[int] = None
    message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Anomaly':
        return cls(**data)

@dataclass
class SystemStatus:
    sensor_active: bool
    detector_active: bool
    llm_active: bool
    api_active: bool
    last_reading_time: Optional[str] = None
    anomaly_count_24h: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)