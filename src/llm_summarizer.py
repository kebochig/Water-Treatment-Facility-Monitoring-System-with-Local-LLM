import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import redis
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
    """Custom parser to clean up LLM output"""
    
    def parse(self, text: str) -> str:
        # Remove any markdown formatting and clean up the text
        cleaned = text.strip()
        if cleaned.startswith('"""') and cleaned.endswith('"""'):
            cleaned = cleaned[3:-3].strip()
        return cleaned

class LLMSummaryGenerator:
    def __init__(self, redis_host: str = "redis", redis_port: int = 6379, 
                 ollama_host: str = "ollama", ollama_port: int = 11434):
        self.redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self.running = False
        
        # Initialize Ollama LLM
        # self.llm = Ollama(
        #     base_url=f"http://{ollama_host}:{ollama_port}",
        #     model="llama3.2:3b",  # Using Llama 3.2 3B model
        #     temperature=0.1
        # )

        model_name = "llama3.2:1b" 
        self.llm = Ollama(
        model=model_name,
        temperature=0.3,
        top_p=0.9,
        repeat_penalty=1.1,
        base_url="http://ollama:11434"
        )
        logger.info(self.llm)

        # Summary storage
        self.latest_summary = ""
        self.summary_history = deque(maxlen=50)
        self.summary_lock = threading.Lock()
        
        # Prompt template for anomaly summarization
        self.prompt_template = PromptTemplate(
            input_variables=["time_period", "anomalies", "sensor_status"],
            template="""You are analyzing water treatment facility sensor data. Please provide a clear, concise summary of the system status.

                        Time Period: {time_period}
                        Sensor ID: wtf-pipe-1

                        Anomalies Detected:
                        {anomalies}

                        Current Sensor Status: {sensor_status}
                        Kindly state and analyse current sensor status.

                         Note the following normal sensor readings:

                        Temperature Normal range is between 10°C to 35°C, spike threshold (far off normal range) greater than 45°C, drift threshold (slightly above normal range) greater than 38°C
                        Pressure Normal range is between 1.0 bar to 3.0 bar, spike threshold greater than 4.0 bar, drift threshold greater than 3.5 bar
                        Flow Normal range is between  20 L/min to 100 L/min, spike threshold greater than 120 L/min, drift threshold greater than 110 L/min

                        Please provide a professional summary in 2-3 sentences that explains:
                        1. What anomalies occurred and when
                        2. The current system status
                        3. Any operational implications
                        4. Overall system status

                        Keep the summary under 200 words, factual, human-readable and actionable for facility operators.
                        """
                                )
                                
        # Chain components
        self.chain = self.prompt_template | self.llm | SummaryOutputParser()
        
    def wait_for_ollama(self, max_retries: int = 30, retry_delay: int = 5):
        """Wait for Ollama service to be ready"""
        for i in range(max_retries):
            try:
                # Test connection by making a simple request
                response = self.llm.invoke("Hello")
                logger.info("Ollama service is ready")
                return True
            except Exception as e:
                logger.info(f"Waiting for Ollama service... (attempt {i+1}/{max_retries})")
                time.sleep(retry_delay)
        
        logger.error("Ollama service not available after maximum retries")
        return False
        
    def get_recent_anomalies(self, hours: int = 0.5) -> List[Dict[str, Any]]:
        """Get anomalies from the last N hours"""
        try:
            # Get anomalies from Redis
            anomaly_strings = self.redis_client.lrange('anomalies', 0, -1)
            anomalies = []
            
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            for anomaly_str in anomaly_strings:
                try:
                    anomaly = json.loads(anomaly_str)
                    anomaly_time = datetime.fromisoformat(anomaly['timestamp'].replace('Z', '+00:00'))
                    
                    if anomaly_time >= cutoff_time:
                        anomalies.append(anomaly)
                except Exception as e:
                    logger.warning(f"Error parsing anomaly: {e}")
            
            # Sort by timestamp (most recent first)
            anomalies.sort(key=lambda x: x['timestamp'], reverse=True)
            logger.info(f"Anomalies retrieved. Count: {len(anomalies)}")
            return anomalies
            
        except Exception as e:
            logger.error(f"Error retrieving anomalies: {e}")
            return []
    
    def get_sensor_status(self) -> str:
        """Get current sensor operational status"""
        try:
            # Get latest reading from stream
            stream_data = self.redis_client.get("last_reading") #self.redis_client.xrevrange('sensor_readings', count=1)
            if stream_data:
                reading = json.loads(stream_data)
            logger.info(f"Sensor - {stream_data} >>>> {reading}")
            if not stream_data:
                logger.info("No recent sensor data available")
                return "No recent sensor data available"
            
            # Parse latest reading
            fields = reading
            timestamp = fields['timestamp']
            temp = float(fields['temperature'])
            pressure = float(fields['pressure'])
            flow = float(fields['flow'])
            
            # Check how recent the data is
            reading_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            time_diff = datetime.now(timezone.utc) - reading_time
            
            if time_diff.total_seconds() > 30:
                return f"Last reading {int(time_diff.total_seconds())}s ago - possible connectivity issues"
            
            return f"Active - Latest: {temp}°C, {pressure} bar, {flow} L/min"
            
        except Exception as e:
            logger.error(f"Error getting sensor status: {e}")
            return "Sensor status unknown"
    
    def format_anomalies_for_prompt(self, anomalies: List[Dict[str, Any]]) -> str:
        """Format anomalies for the LLM prompt"""
        if not anomalies:
            return "No anomalies detected in the specified time period."
        
        formatted = []
        for anomaly in anomalies:
            time_str = anomaly['timestamp'][:19].replace('T', ' ')  # Simple format
            
            if anomaly['type'] == 'spike':
                formatted.append(f"- {time_str}: {anomaly['parameter']} spike ({anomaly['value']})")
            elif anomaly['type'] == 'drift':
                formatted.append(f"- {time_str}: {anomaly['parameter']} drift for {anomaly['duration_seconds']}s ({anomaly['value']})")
            elif anomaly['type'] == 'dropout':
                formatted.append(f"- {time_str}: Sensor dropout for {anomaly['duration_seconds']}s")
        
        return '\n'.join(formatted)
    
    def generate_summary(self) -> Optional[str]:
        """Generate a summary using the LLM"""
        try:
            # Get recent anomalies (last hour)
            anomalies = self.get_recent_anomalies(hours=1)
            sensor_status = self.get_sensor_status()
            
            # Prepare time period
            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            time_period = f"{one_hour_ago.strftime('%H:%M')} - {now.strftime('%H:%M')} UTC"
            
            # Format anomalies
            anomalies_text = self.format_anomalies_for_prompt(anomalies)
            
            # Generate summary using LangChain
            logger.info("Generating summary with LLM...")
            summary = self.chain.invoke({
                "time_period": time_period,
                "anomalies": anomalies_text,
                "sensor_status": sensor_status
            })

            # Clean up the response
            if isinstance(summary, str):
                summary = summary.strip()
            else:
                summary = str(summary).strip()
            
            # Store summary
            with self.summary_lock:
                self.latest_summary = summary
                self.summary_history.append({
                    "timestamp": now.isoformat(),
                    "summary": summary,
                    "anomaly_count": len(anomalies)
                })
            
            # Store in Redis
            summary_data = {
                "timestamp": now.isoformat(),
                "summary": summary,
                "anomaly_count": len(anomalies)
            }
            self.redis_client.set('latest_summary', json.dumps(summary_data))
            
            logger.info(f"Generated summary: {summary}...")
            return summary
            
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return None
    
    def monitor_anomalies(self):
        """Monitor for new anomalies and trigger summary generation"""
        last_summary_time = time.time()
        summary_interval = 180  # Generate summary every 3 minutes
        
        try:
            last_id = '$'  # Start from latest
            while self.running:
                try:
                    # Check if it's time for a scheduled summary
                    current_time = time.time()
                    if current_time - last_summary_time >= summary_interval:
                        self.generate_summary()
                        last_summary_time = current_time
                    
                    # Listen for new anomalies
                    streams = self.redis_client.xread(
                        {'anomaly_stream': last_id}, 
                        count=1, 
                        block=5000  # 5 second timeout
                    )
                    
                    if streams:
                        for stream_name, messages in streams:
                            for message_id, fields in messages:
                                logger.info(f"New anomaly detected, generating summary...")
                                self.generate_summary()
                                last_summary_time = current_time
                                last_id = message_id
                    
                except redis.exceptions.ResponseError:
                    # Stream might not exist yet
                    time.sleep(5)
                except Exception as e:
                    logger.error(f"Error monitoring anomalies: {e}")
                    time.sleep(5)
                    
        except Exception as e:
            logger.error(f"Anomaly monitoring error: {e}")
    
    def get_latest_summary(self) -> Dict[str, Any]:
        """Get the latest generated summary"""
        with self.summary_lock:
            if not self.latest_summary:
                return {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "summary": "No summary available yet. System is initializing...",
                    "anomaly_count": 0
                }
            
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "summary": self.latest_summary,
                "anomaly_count": len(self.get_recent_anomalies())
            }
    
    def run(self):
        """Main loop for the summary generator"""
        self.running = True
        
        logger.info("Starting LLM Summary Generator")
        
        # Wait for Ollama to be ready
        if not self.wait_for_ollama():
            logger.error("Cannot start summary generator - Ollama not available")
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
        """Stop the summary generator"""
        self.running = False

if __name__ == "__main__":
    generator = LLMSummaryGenerator()
    generator.run()