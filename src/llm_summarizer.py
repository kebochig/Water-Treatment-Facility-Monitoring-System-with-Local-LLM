import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
import redis
import re
import threading
from collections import deque
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.schema import BaseOutputParser

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class SummaryOutputParser(BaseOutputParser):
    """
    Custom parser for cleaning and formatting LLM output responses.
    
    This parser removes markdown formatting, triple quotes, and other
    unwanted characters from the LLM's generated summaries to ensure
    clean, readable output for facility operators.
    """
    
    def parse(self, text: str) -> str:
        """Clean and format raw LLM output text."""
        cleaned = text.strip()
        if cleaned.startswith('"""') and cleaned.endswith('"""'):
            cleaned = cleaned[3:-3].strip()
        return cleaned

class SensorDataManager:
    """
    Handles all sensor data operations and Redis interactions.
    
    Separates data access logic from business logic, making it easier
    to test and modify data storage mechanisms.
    """
    
    def __init__(self, redis_client: redis.Redis):
        """
        Initialize sensor data manager with Redis client.
        
        Args:
            redis_client: Configured Redis client instance
        """
        self.redis_client = redis_client
    
    def get_latest_sensor_reading(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve the most recent sensor reading from Redis.
        
        Returns:
            Optional[Dict[str, Any]]: Sensor reading data or None if unavailable
        """
        try:
            stream_data = self.redis_client.get("last_reading")
            if stream_data:
                logger.info(f"Latest Sensor Reading -> {json.loads(stream_data)}")
                return json.loads(stream_data)
            logger.info(f"No Sensor Reading available -> ")
            return None
        except Exception as e:
            logger.error(f"Error retrieving sensor reading: {e}")
            return None
    
    def get_anomalies_from_redis(self) -> List[str]:
        """
        Fetch raw anomaly strings from Redis list.
        
        Returns:
            List[str]: Raw JSON strings of anomaly data
        """
        try:
            return self.redis_client.lrange('anomalies', 0, -1)
        except Exception as e:
            logger.error(f"Error fetching anomalies from Redis: {e}")
            return []
    
    def store_summary(self, summary_data: Dict[str, Any]) -> bool:
        """
        Store generated summary in Redis.
        
        Args:
            summary_data: Dictionary containing summary and metadata
            
        Returns:
            bool: True if stored successfully, False otherwise
        """
        try:
            self.redis_client.set('latest_summary', json.dumps(summary_data))
            return True
        except Exception as e:
            logger.error(f"Error storing summary: {e}")
            return False
    
    def listen_for_anomalies(self, stream_name: str, last_id: str, 
                           timeout: int = 5000) -> List[Tuple[str, List[Tuple[str, Dict]]]]:
        """
        Listen for new anomalies on Redis stream.
        
        Args:
            stream_name: Name of the Redis stream
            last_id: Last processed message ID
            timeout: Timeout in milliseconds
            
        Returns:
            List of stream data tuples
        """
        try:
            return self.redis_client.xread(
                {stream_name: last_id}, 
                count=1, 
                block=timeout
            )
        except redis.exceptions.ResponseError:
            # Stream might not exist yet
            return []
        except Exception as e:
            logger.error(f"Error listening for anomalies: {e}")
            return []

class AnomalyProcessor:
    """
    Handles anomaly data processing and filtering logic.
    
    Separates anomaly-specific business logic from data access
    and summary generation concerns.
    """
    
    @staticmethod
    def parse_anomaly_json(anomaly_str: str) -> Optional[Dict[str, Any]]:
        """
        Parse JSON string into anomaly dictionary.
        
        Args:
            anomaly_str: JSON string representation of anomaly
            
        Returns:
            Optional[Dict[str, Any]]: Parsed anomaly data or None if invalid
        """
        try:
            return json.loads(anomaly_str)
        except Exception as e:
            logger.warning(f"Error parsing anomaly JSON: {e}")
            return None
    
    @staticmethod
    def is_anomaly_within_timeframe(anomaly: Dict[str, Any], cutoff_time: datetime) -> bool:
        """
        Check if anomaly occurred within the specified timeframe.
        
        Args:
            anomaly: Anomaly dictionary with timestamp
            cutoff_time: Cutoff datetime for filtering
            
        Returns:
            bool: True if anomaly is within timeframe
        """
        try:
            anomaly_time = datetime.fromisoformat(anomaly['timestamp'].replace('Z', '+00:00'))
            return anomaly_time >= cutoff_time
        except Exception as e:
            logger.warning(f"Error parsing anomaly timestamp: {e}")
            return False
    
    @staticmethod
    def filter_recent_anomalies(anomalies: List[Dict[str, Any]], hours: float) -> List[Dict[str, Any]]:
        """
        Filter anomalies to only include those within the specified time window.
        
        Args:
            anomalies: List of anomaly dictionaries
            hours: Time window in hours
            
        Returns:
            List[Dict[str, Any]]: Filtered and sorted anomalies
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        recent_anomalies = [
            anomaly for anomaly in anomalies 
            if AnomalyProcessor.is_anomaly_within_timeframe(anomaly, cutoff_time)
        ]
        
        # Sort by timestamp (most recent first)
        recent_anomalies.sort(key=lambda x: x['timestamp'], reverse=True)
        return recent_anomalies
    
    @staticmethod
    def format_single_anomaly(anomaly: Dict[str, Any]) -> str:
        """
        Format a single anomaly into human-readable text for LLM prompt injection.
        
        Args:
            anomaly: Anomaly dictionary
            
        Returns:
            str: Formatted anomaly description
        """
        time_str = anomaly['timestamp'][:19].replace('T', ' ')
        
        if anomaly['type'] == 'spike':
            return f"- {time_str}: {anomaly['parameter']} spike ({anomaly['value']})"
        elif anomaly['type'] == 'drift':
            return f"- {time_str}: {anomaly['parameter']} drift for {anomaly['duration_seconds']}s ({anomaly['value']})"
        elif anomaly['type'] == 'dropout':
            return f"- {time_str}: Sensor dropout for {anomaly['duration_seconds']}s"
        else:
            return f"- {time_str}: Unknown anomaly type: {anomaly.get('type', 'N/A')}"

class SensorStatusAnalyzer:
    """
    Analyzes sensor status and connectivity health.
    
    Encapsulates logic for determining sensor operational status
    and formatting status messages for operators.
    """
    
    CONNECTIVITY_THRESHOLD_SECONDS = 30
    
    @staticmethod
    def parse_sensor_reading(reading: Dict[str, Any]) -> Tuple[datetime, float, float, float]:
        """
        Extract and parse sensor values from reading data.
        
        Args:
            reading: Sensor reading dictionary
            
        Returns:
            Tuple[datetime, float, float, float]: timestamp, temperature, pressure, flow
            
        Raises:
            ValueError: If required fields are missing or invalid
        """
        try:
            timestamp = datetime.fromisoformat(reading['timestamp'].replace('Z', '+00:00'))
            temp = float(reading['temperature'])
            pressure = float(reading['pressure'])
            flow = float(reading['flow'])
            return timestamp, temp, pressure, flow
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid sensor reading format: {e}")
    
    @staticmethod
    def calculate_data_age(reading_time: datetime) -> float:
        """
        Calculate age of sensor data in seconds.
        
        Args:
            reading_time: Timestamp of the sensor reading
            
        Returns:
            float: Age in seconds
        """
        time_diff = datetime.now(timezone.utc) - reading_time
        return time_diff.total_seconds()
    
    @staticmethod
    def format_sensor_status(temp: float, pressure: float, flow: float, 
                           data_age_seconds: float) -> str:
        """
        Format sensor readings into status string.
        
        Args:
            temp: Temperature reading
            pressure: Pressure reading  
            flow: Flow rate reading
            data_age_seconds: Age of the data in seconds
            
        Returns:
            str: Formatted status message
        """
        if data_age_seconds > SensorStatusAnalyzer.CONNECTIVITY_THRESHOLD_SECONDS:
            return f"Last reading {int(data_age_seconds)}s ago - possible connectivity issues"
        
        return f"Active - Latest: {temp}°C, {pressure} bar, {flow} L/min"

class TimeWindowManager:
    """
    Manages time-related operations and formatting.
    
    Centralizes time window calculations and formatting logic.
    """
    
    @staticmethod
    def get_time_period_string(hours: int = 1) -> str:
        """
        Generate formatted time period string for the specified duration.
        
        Args:
            hours: Number of hours to look back
            
        Returns:
            str: Formatted time period (e.g., "09:30 - 10:30 UTC")
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=hours)
        return f"{start_time.strftime('%H:%M')} - {now.strftime('%H:%M')} UTC"
    
    @staticmethod
    def get_current_timestamp() -> str:
        """
        Get current timestamp in ISO format.
        
        Returns:
            str: Current timestamp
        """
        return datetime.now(timezone.utc).isoformat()

class LLMResponseProcessor:
    """
    Handles LLM response processing and cleaning.
    
    Separates response processing logic from the main generator class.
    """
    
    @staticmethod
    def clean_llm_response(response: Any) -> str:
        """
        Clean and process raw LLM response.
        
        Args:
            response: Raw response from LLM
            
        Returns:
            str: Cleaned response text
        """
        if isinstance(response, str):
            summary = response.strip()
        else:
            summary = str(response).strip()

        # Remove thinking tags that some models include
        if '<think>' in summary:
            summary = re.sub(r"<think>.*?</think>\s*", "", summary, flags=re.DOTALL)
        
        return summary

class LLMSummaryGenerator:
    """
    AI-powered water treatment facility monitoring and anomaly analysis system.
    
    This class provides real-time monitoring and intelligent analysis of water treatment
    sensor data using a Large Language Model (LLM). It detects anomalies, generates
    human-readable summaries, and provides actionable insights for facility operators.
    """
    
    def __init__(self, redis_host: str = "redis", redis_port: int = 6379, 
                 ollama_host: str = "ollama", ollama_port: int = 11434):
        """Initialize the LLM Summary Generator with connection parameters."""
        # Initialize Redis connection
        redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        
        # Initialize helper classes
        self.data_manager = SensorDataManager(redis_client)
        self.anomaly_processor = AnomalyProcessor()
        self.sensor_analyzer = SensorStatusAnalyzer()
        self.time_manager = TimeWindowManager()
        self.response_processor = LLMResponseProcessor()
        
        # Service state
        self.running = False
        self.latest_summary = ""
        self.summary_history = deque(maxlen=50)
        self.summary_lock = threading.Lock()
        
        # Initialize LLM
        self._initialize_llm(ollama_host, ollama_port)
        
        # Setup prompt template and chain
        self._setup_llm_chain()
    
    def _initialize_llm(self, ollama_host: str, ollama_port: int):
        """
        Initialize the Ollama LLM with configuration.
        
        Args:
            ollama_host: Ollama server hostname
            ollama_port: Ollama server port
        """
        model_name = "deepseek-r1:1.5b"
        self.llm = Ollama(
            model=model_name,
            temperature=0.3,
            top_p=0.9,
            repeat_penalty=1.1,
            base_url=f"http://{ollama_host}:{ollama_port}"
        )
        logger.info(f"Initialized LLM: {self.llm}")
    
    def _setup_llm_chain(self):
        """Setup the LangChain prompt template and processing chain."""
        self.prompt_template = PromptTemplate(
            input_variables=["time_period", "anomalies", "sensor_status"],
            template="""   You are a water treatment facility monitoring system analyzing sensor data for operational status assessment. Your role is to provide clear, actionable insights for facility operators using the following instructions.

                            ## Analysis Parameters
                            - **Time Period:** {time_period}
                            - **Sensor ID:** wtf-pipe-1
                            - **Detected Anomalies:** {anomalies}
                            - **Current Sensor reading:** {sensor_status}

                            ## Normal Operating Ranges & Thresholds

                            ### Temperature
                            - **Normal Range:** 10°C - 35°C
                            - **Drift Threshold:** >38°C (slightly elevated)
                            - **Spike Threshold:** >45°C (critically high)

                            ### Pressure
                            - **Normal Range:** 1.0 - 3.0 bar
                            - **Drift Threshold:** >3.5 bar (slightly elevated)
                            - **Spike Threshold:** >4.0 bar (critically high)

                            ### Flow Rate
                            - **Normal Range:** 20 - 100 L/min
                            - **Drift Threshold:** >110 L/min (slightly elevated)
                            - **Spike Threshold:** >120 L/min (critically high)

                            Sensor dropout indcate abnormal behaviour as there are no readings within the specified duration

                            ## Required Analysis Output

                            1. **Anomaly Assessment:** Identify what anomalies occurred, their severity level, and timeframe
                            2. **Current reading Evaluation:** Compare current reading against normal ranges and classify as normal, drift, or spike conditions
                            3. **Recommended Actions:** Suggest specific next steps for operators (monitoring, maintenance, or emergency response)
                            4. **Overall System Health:** Provide a clear status classification (Normal, Caution, Alert, Critical)

                            ## Output Requirements
                            - Use clear, technical language appropriate for facility operators
                            - Prioritize safety-critical information
                            - Include specific numerical values when relevant
                            - End with a one-sentence system status summary
                            - Maintain professional, factual tone without speculation
                            
                            Ensure response is within 150-200 words.
                        """
        )
        
        # Chain components
        self.chain = self.prompt_template | self.llm | SummaryOutputParser()
    
    def wait_for_ollama(self, max_retries: int = 30, retry_delay: int = 5) -> bool:
        """Wait for Ollama LLM service to become available with retry logic."""
        for i in range(max_retries):
            try:
                response = self.llm.invoke("Hello")
                logger.info("Ollama service is ready")
                return True
            except Exception as e:
                logger.info(f"Waiting for Ollama service... (attempt {i+1}/{max_retries})")
                time.sleep(retry_delay)
        
        logger.error("Ollama service not available after maximum retries")
        return False
    
    def get_recent_anomalies(self, hours: float = 0.5) -> List[Dict[str, Any]]:
        """
        Retrieve and filter anomalies from Redis within the specified time window.
        
        Args:
            hours: Time window in hours to look back for anomalies
            
        Returns:
            List[Dict[str, Any]]: Filtered and sorted anomalies
        """
        # Get raw anomaly data
        anomaly_strings = self.data_manager.get_anomalies_from_redis()
        
        # Parse valid anomalies
        valid_anomalies = []
        for anomaly_str in anomaly_strings:
            parsed = self.anomaly_processor.parse_anomaly_json(anomaly_str)
            if parsed:
                valid_anomalies.append(parsed)
        
        # Filter by time window
        recent_anomalies = self.anomaly_processor.filter_recent_anomalies(valid_anomalies, hours)
        
        logger.info(f"Anomalies retrieved. Count: {len(recent_anomalies)}")
        return recent_anomalies
    
    def get_sensor_status(self) -> str:
        """
        Retrieve current sensor operational status and latest readings.
        
        Returns:
            str: Formatted sensor status string
        """
        reading = self.data_manager.get_latest_sensor_reading()
        
        if not reading:
            return "No recent sensor data available"
        
        try:
            timestamp, temp, pressure, flow = self.sensor_analyzer.parse_sensor_reading(reading)
            data_age = self.sensor_analyzer.calculate_data_age(timestamp)
            
            return self.sensor_analyzer.format_sensor_status(temp, pressure, flow, data_age)
            
        except ValueError as e:
            logger.error(f"Error parsing sensor reading: {e}")
            return "Sensor status unknown"
    
    def format_anomalies_for_prompt(self, anomalies: List[Dict[str, Any]]) -> str:
        """
        Format anomaly data into human-readable text for LLM prompt injection.
        
        Args:
            anomalies: List of anomaly dictionaries
            
        Returns:
            str: Formatted multi-line string with anomaly descriptions
        """
        if not anomalies:
            return "No anomalies detected in the specified time period."
        
        formatted_anomalies = [
            self.anomaly_processor.format_single_anomaly(anomaly) 
            for anomaly in anomalies
        ]
        
        return '\n'.join(formatted_anomalies)
    
    def _prepare_llm_inputs(self) -> Dict[str, str]:
        """
        Prepare all inputs needed for LLM summary generation.
        
        Returns:
            Dict[str, str]: Dictionary with formatted inputs for LLM
        """
        anomalies = self.get_recent_anomalies(hours=1)
        sensor_status = self.get_sensor_status()
        time_period = self.time_manager.get_time_period_string(hours=1)
        anomalies_text = self.format_anomalies_for_prompt(anomalies)
        
        return {
            "time_period": time_period,
            "anomalies": anomalies_text,
            "sensor_status": sensor_status,
            "anomaly_count": len(anomalies)
        }
    
    def _store_generated_summary(self, summary: str, anomaly_count: int):
        """
        Store generated summary in memory and Redis.
        
        Args:
            summary: Generated summary text
            anomaly_count: Number of anomalies processed
        """
        current_time = self.time_manager.get_current_timestamp()
        
        # Store in memory with thread safety
        with self.summary_lock:
            self.latest_summary = summary
            self.summary_history.append({
                "timestamp": current_time,
                "summary": summary,
                "anomaly_count": anomaly_count,
                "generated_by": "LLM Assistant"
            })
        
        # Store in Redis
        summary_data = {
            "timestamp": current_time,
            "summary": summary,
            "anomaly_count": anomaly_count
        }
        self.data_manager.store_summary(summary_data)
    
    def generate_summary(self) -> Optional[str]:
        """
        Generate AI-powered operational summary using LLM analysis.
        
        Returns:
            Optional[str]: Generated summary text, or None if generation fails
        """
        try:
            # Prepare inputs
            inputs = self._prepare_llm_inputs()
            
            # Generate summary using LangChain
            logger.info("Generating summary with LLM...")
            raw_response = self.chain.invoke({
                "time_period": inputs["time_period"],
                "anomalies": inputs["anomalies"],
                "sensor_status": inputs["sensor_status"]
            })
            
            # Process and clean response
            summary = self.response_processor.clean_llm_response(raw_response)
            
            # Store the summary
            self._store_generated_summary(summary, inputs["anomaly_count"])
            
            logger.info(f"Generated summary: {summary[:250]}...")
            return summary
            
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return None
    
    def _handle_scheduled_summary(self, last_summary_time: float, 
                                summary_interval: int) -> Tuple[bool, float]:
        """
        Check if scheduled summary is needed and generate if so.
        
        Args:
            last_summary_time: Timestamp of last summary generation
            summary_interval: Interval between scheduled summaries
            
        Returns:
            Tuple[bool, float]: (summary_generated, new_last_summary_time)
        """
        current_time = time.time()
        if current_time - last_summary_time >= summary_interval:
            self.generate_summary()
            return True, current_time
        return False, last_summary_time
    
    def _handle_anomaly_stream_message(self, streams: List, last_id: str) -> Tuple[str, bool]:
        """
        Process anomaly stream messages and generate summaries.
        
        Args:
            streams: Stream data from Redis
            last_id: Last processed message ID
            
        Returns:
            Tuple[str, bool]: (new_last_id, summary_generated)
        """
        summary_generated = False
        new_last_id = last_id
        
        for stream_name, messages in streams:
            for message_id, fields in messages:
                logger.info("New anomaly detected, generating summary...")
                self.generate_summary()
                summary_generated = True
                new_last_id = message_id
        
        return new_last_id, summary_generated
    
    def monitor_anomalies(self):
        """Continuous monitoring loop for anomaly detection and summary generation."""
        last_summary_time = time.time()
        summary_interval = 180  # Generate summary every 3 minutes
        
        try:
            last_id = '$'  # Start from latest
            while self.running:
                try:
                    # Check for scheduled summary
                    summary_generated, last_summary_time = self._handle_scheduled_summary(
                        last_summary_time, summary_interval
                    )
                    
                    # Listen for new anomalies
                    streams = self.data_manager.listen_for_anomalies('anomaly_stream', last_id)
                    
                    if streams:
                        last_id, event_summary_generated = self._handle_anomaly_stream_message(
                            streams, last_id
                        )
                        if event_summary_generated:
                            last_summary_time = time.time()
                    
                except Exception as e:
                    logger.error(f"Error monitoring anomalies: {e}")
                    time.sleep(5)
                    
        except Exception as e:
            logger.error(f"Anomaly monitoring error: {e}")
    
    def get_latest_summary(self) -> Dict[str, Any]:
        """
        Retrieve the most recently generated operational summary.
        
        Returns:
            Dict[str, Any]: Dictionary containing summary and metadata
        """
        with self.summary_lock:
            if not self.latest_summary:
                return {
                    "timestamp": self.time_manager.get_current_timestamp(),
                    "summary": "No summary available yet. System is initializing...",
                    "anomaly_count": 0
                }
            
            return {
                "timestamp": self.time_manager.get_current_timestamp(),
                "summary": self.latest_summary,
                "anomaly_count": len(self.get_recent_anomalies())
            }
    
    def run(self):
        """Main execution loop for the LLM Summary Generator service."""
        self.running = True
        
        logger.info("Starting LLM Summary Generator")
        
        # Wait for Ollama to be ready
        if not self.wait_for_ollama():
            logger.error("Cannot start summary generator - Ollama not available. Use /metrics endpoint for anomal breakdown")
            return
        
        # Generate initial summary
        self.generate_summary()
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=self.monitor_anomalies)
        monitor_thread.daemon = True
        monitor_thread.start()
        
        try:
            # Keep main thread alive
            while self.running:
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Summary generator stopped by user")
        except Exception as e:
            logger.error(f"Summary generator error: {e}")
        finally:
            self.running = False
    
    def stop(self):
        """Gracefully stop the LLM Summary Generator service."""
        self.running = False

if __name__ == "__main__":
    generator = LLMSummaryGenerator()
    generator.run()