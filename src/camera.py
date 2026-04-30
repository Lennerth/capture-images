import os
import logging
import requests
import time
import subprocess
import urllib.parse
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class CameraCapture(ABC):
    def __init__(self, camera_config: Dict[str, Any]):
        self.id = camera_config.get("id")
        self.name = camera_config.get("name")
        self.timeout = camera_config.get("timeout_seconds", 10)
        self.consecutive_failures = 0
    
    @abstractmethod
    def capture(self, output_path: str) -> bool:
        """
        Captures an image and saves it to output_path.
        Returns True if successful, False otherwise.
        """
        pass

    def record_success(self):
        if self.consecutive_failures > 0:
            logger.info(f"Camera {self.id} ('{self.name}') recovered after {self.consecutive_failures} failures.")
        self.consecutive_failures = 0

    def record_failure(self):
        self.consecutive_failures += 1
        logger.warning(f"Camera {self.id} ('{self.name}') capture failed. Consecutive failures: {self.consecutive_failures}")

class HTTPSnapshotCamera(CameraCapture):
    def __init__(self, camera_config: Dict[str, Any]):
        super().__init__(camera_config)
        self.snapshot_url = camera_config.get("snapshot_url")
        auth_config = camera_config.get("auth", {})
        self.username = auth_config.get("username")
        self.password = auth_config.get("password")
        
        self.auth = None
        if self.username and self.password:
            self.auth = (self.username, self.password)

    def capture(self, output_path: str) -> bool:
        tmp_path = output_path + ".tmp"
        try:
            logger.debug(f"Attempting capture from {self.id} at {self.snapshot_url}")
            response = requests.get(
                self.snapshot_url,
                auth=self.auth,
                timeout=self.timeout,
                stream=True
            )
            response.raise_for_status()
            
            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            os.replace(tmp_path, output_path)
            self.record_success()
            return True
            
        except requests.exceptions.RequestException as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.error(f"HTTP request failed for camera {self.id}: {e}")
            self.record_failure()
            return False
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.error(f"Unexpected error capturing from camera {self.id}: {e}")
            self.record_failure()
            return False

class NVRChannelCamera(CameraCapture):
    """
    Captures a single frame from an NVR RTSP stream using ffmpeg.
    
    URL Templating Contract:
    - Only {user} and {password} are URL-encoded via urllib.parse.quote(..., safe="").
    - {host}, {port}, {channel}, {subtype} are inserted as-is.
    - This allows firmware variants where credentials live in the path (e.g. /user={user}&password={password})
      without leaking an unencoded password.
    """
    def __init__(self, camera_config: Dict[str, Any], nvr_config: Dict[str, Any]):
        super().__init__(camera_config)
        self.channel = camera_config.get("channel")
        self.subtype = camera_config.get("subtype", nvr_config.get("default_subtype", 0))
        
        self.host = os.environ.get("NVR_HOST") or nvr_config.get("host")
        self.port = nvr_config.get("rtsp_port", 554)
        self.username = nvr_config.get("username", "")
        
        # Prefer NVR_PASSWORD env var, fallback to config
        self.password = os.environ.get("NVR_PASSWORD") or nvr_config.get("password", "")
        
        self.url_template = nvr_config.get(
            "url_template", 
            "rtsp://{user}:{password}@{host}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}"
        )
        
    def capture(self, output_path: str) -> bool:
        tmp_path = output_path + ".tmp"
        
        safe_user = urllib.parse.quote(self.username, safe="")
        safe_pass = urllib.parse.quote(self.password, safe="")
        
        rtsp_url = self.url_template.format(
            user=safe_user,
            password=safe_pass,
            host=self.host,
            port=self.port,
            channel=self.channel,
            subtype=self.subtype
        )
        
        # Redacted URL for logging
        redacted_url = self.url_template.format(
            user=safe_user,
            password="***",
            host=self.host,
            port=self.port,
            channel=self.channel,
            subtype=self.subtype
        )
        
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-y",
            "-i", rtsp_url,
            "-frames:v", "1",
            "-q:v", "2",
            tmp_path
        ]
        
        try:
            logger.debug(f"Attempting capture from {self.id} (channel {self.channel}, subtype {self.subtype}) using template")
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            
            if result.returncode == 0 and os.path.exists(tmp_path):
                os.replace(tmp_path, output_path)
                self.record_success()
                return True
            else:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                stderr_summary = result.stderr[-200:] if result.stderr else "No stderr output"
                logger.error(f"ffmpeg failed for camera {self.id} (channel {self.channel}). Return code: {result.returncode}. Stderr: {stderr_summary}")
                self.record_failure()
                return False
                
        except subprocess.TimeoutExpired as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.error(f"ffmpeg timed out after {self.timeout}s for camera {self.id} (channel {self.channel})")
            self.record_failure()
            return False
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.error(f"Unexpected error capturing from camera {self.id}: {e}")
            self.record_failure()
            return False

def create_camera(camera_config: Dict[str, Any], nvr_config: Optional[Dict[str, Any]] = None) -> CameraCapture:
    cam_type = camera_config.get("type", "http").lower()
    if cam_type in ["http", "https"]:
        return HTTPSnapshotCamera(camera_config)
    elif cam_type == "nvr_rtsp":
        if not nvr_config:
            raise ValueError("nvr_config is required for nvr_rtsp cameras")
        return NVRChannelCamera(camera_config, nvr_config)
    else:
        raise ValueError(f"Unsupported camera type: {cam_type}")
