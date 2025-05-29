import os
from dataclasses import dataclass, field
from typing import Dict, Any

@dataclass
class SensorConfig:
    sensor_id: str = "wtf-pipe-1"
    emission_interval: int = 2  # seconds
    temperature_range: tuple = (10.0, 35.0)
    pressure_range: tuple = (1.0, 3.0)
    flow_range: tuple = (20.0, 100.0)

@dataclass
class AnomalyConfig:
    spike_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "temperature": 45.0,
        "pressure": 4.0,
        "flow": 120.0
    })
    drift_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "temperature": 38.0,
        "pressure": 3.5,
        "flow": 110.0
    })
    drift_duration: int = 15  # seconds
    dropout_timeout: int = 10  # seconds

# @dataclass
# class LLMConfig:
#     model_path: str = "/app/data/models/mistral-7b-instruct-v0.1.Q4_K_M.gguf"
#     model_url: str = "https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.1-GGUF/resolve/main/mistral-7b-instruct-v0.1.Q4_K_M.gguf"
#     max_tokens: int = 512
#     temperature: float = 0.3

@dataclass
class LLMConfig:
    model_name: str = "deepseek-r1:1.5b"  # Default Ollama model
    max_tokens: int = 512
    temperature: float = 0.3
    ollama_url: str = "http://ollama:11434"  # Ollama service URL

@dataclass
class APIConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    max_stored_anomalies: int = 100

@dataclass
class Config:
    sensor: SensorConfig = field(default_factory=SensorConfig)
    anomaly: AnomalyConfig = field(default_factory=AnomalyConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    api: APIConfig = field(default_factory=APIConfig)
    redis_url: str = "redis://redis:6379"
    log_level: str = "INFO"