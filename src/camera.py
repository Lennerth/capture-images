import os
import logging
import requests
import time
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

def create_camera(camera_config: Dict[str, Any]) -> CameraCapture:
    cam_type = camera_config.get("type", "http").lower()
    if cam_type == "http":
        return HTTPSnapshotCamera(camera_config)
    else:
        raise ValueError(f"Unsupported camera type: {cam_type}")
