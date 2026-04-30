import os
import sys
import time
import yaml
import logging
import signal
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from camera import create_camera
from uploader import S3Uploader
from health import HealthMonitor, RetryManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Main")

def load_config(config_path="config.yaml"):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

def _validate_config(cfg: dict):
    errors = []
    seen_ids = set()
    
    cameras = cfg.get("cameras", [])
    nvr_cfg = cfg.get("nvr", {})
    has_nvr_camera = False
    
    for i, cam in enumerate(cameras):
        cam_id = cam.get("id")
        if not cam_id:
            errors.append(f"Camera at index {i} is missing an 'id'.")
        else:
            if cam_id in seen_ids:
                errors.append(f"Duplicate camera id found: {cam_id}")
            seen_ids.add(cam_id)
            
        cam_type = cam.get("type", "http").lower()
        if cam_type not in ["http", "https", "nvr_rtsp"]:
            errors.append(f"Camera '{cam_id}' has unsupported type: {cam_type}")
            
        timeout = cam.get("timeout_seconds", 10)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append(f"Camera '{cam_id}' has invalid timeout_seconds: {timeout}")
            
        if cam_type == "nvr_rtsp":
            has_nvr_camera = True
            channel = cam.get("channel")
            if not isinstance(channel, int) or channel <= 0:
                errors.append(f"Camera '{cam_id}' (nvr_rtsp) must have a positive integer 'channel'.")
            if "snapshot_url" in cam:
                logger.warning(f"Typo guard: Camera '{cam_id}' (nvr_rtsp) has 'snapshot_url' set, which will be ignored.")
        elif cam_type in ["http", "https"]:
            if "channel" in cam:
                logger.warning(f"Typo guard: Camera '{cam_id}' ({cam_type}) has 'channel' set, which will be ignored.")

    if has_nvr_camera:
        host = os.environ.get("NVR_HOST") or nvr_cfg.get("host")
        if not host:
            errors.append("NVR host is missing. Set NVR_HOST env var or nvr.host in config.")
        if not nvr_cfg.get("username"):
            errors.append("NVR username is missing in config.")
            
        password = os.environ.get("NVR_PASSWORD") or nvr_cfg.get("password")
        if not password:
            errors.append("NVR password is missing. Set NVR_PASSWORD env var or nvr.password in config.")
            
        template = nvr_cfg.get("url_template", "")
        if template:
            for req in ["{host}", "{port}", "{channel}"]:
                if req not in template:
                    errors.append(f"NVR url_template must contain {req}")
            if "{user}" not in template and "{password}" not in template:
                errors.append("NVR url_template must contain at least one of {user} or {password}")

    if errors:
        raise ValueError("Configuration validation failed:\n" + "\n".join(errors))

class CameraService:
    def __init__(self, config_path="config.yaml"):
        self.config = load_config(config_path)
        _validate_config(self.config)
        self.tz = pytz.timezone(self.config.get("timezone", "Europe/Brussels"))
        
        capture_window = self.config.get("capture_window", {})
        self.capture_window_start = capture_window.get("start", "07:00")
        self.capture_window_end = capture_window.get("end", "19:00")
        
        self.health_monitor = HealthMonitor(self.config)
        self.retry_manager = RetryManager(self.config)
        self.uploader = S3Uploader(self.config)
        
        self.cameras = [create_camera(c_conf, self.config.get("nvr")) for c_conf in self.config.get("cameras", [])]
        self.local_storage_path = self.config.get("local_storage", {}).get("path", "/data/images")
        
        self.scheduler = BackgroundScheduler(timezone=self.tz)
        
        self.running = True

    def capture_job(self):
        """
        Job triggered every N minutes to capture images from all cameras.
        """
        now = datetime.now(self.tz)
        current_time_str = now.strftime("%H:%M")
        
        if not (self.capture_window_start <= current_time_str < self.capture_window_end):
            logger.info(f"Outside capture window ({self.capture_window_start}-{self.capture_window_end}), skipping capture.")
            return

        if not self.health_monitor.can_capture():
            logger.warning("Skipping capture cycle due to health/disk constraints.")
            return

        if not self.retry_manager.should_attempt_capture():
            logger.warning("Skipping capture cycle due to network backoff.")
            return

        day_folder = now.strftime("%d_%m_%y")
        time_str = now.strftime("%H-%M-%S")
        
        target_dir = os.path.join(self.local_storage_path, day_folder)
        os.makedirs(target_dir, exist_ok=True)
        
        success_count = 0

        def capture_camera(cam):
            if self.retry_manager.should_skip_camera(cam):
                logger.info(f"Skipping camera {cam.id} due to consecutive failures backoff.")
                return cam, False

            output_path = os.path.join(target_dir, f"cam_{cam.id}_{time_str}.jpg")
            success = cam.capture(output_path)
            return cam, success

        if not self.cameras:
            return

        with ThreadPoolExecutor(max_workers=len(self.cameras)) as executor:
            futures = [executor.submit(capture_camera, cam) for cam in self.cameras]
            for future in as_completed(futures):
                try:
                    cam, success = future.result()
                    if success:
                        success_count += 1
                        self.retry_manager.record_camera_success(cam)
                    else:
                        self.retry_manager.record_camera_failure(cam)
                except Exception as e:
                    logger.error(f"Error in capture thread: {e}")
        
        if success_count > 0:
            self.health_monitor.record_successful_capture()
            self.retry_manager.record_network_success()
        else:
            # We no longer assume all-camera failure means a network-wide problem
            logger.warning("All cameras failed to capture in this cycle. Will rely on per-camera retries.")

    def upload_job(self):
        """
        Job triggered at the end of the day to upload the previous day's folder.
        We just trigger the uploader to process all complete day folders.
        """
        logger.info("Starting scheduled upload job")
        # Find folders to upload (yesterday or older, or current day if configured differently)
        # Uploader will handle reading the state and idempotency
        self.uploader.upload_pending_folders(self.local_storage_path, self.health_monitor)

    def start(self):
        # Schedule capture job
        interval_secs = self.config.get("capture_interval_seconds", 60)
        
        # Calculate aligned start date (next occurrence where seconds == 0)
        now = datetime.now(self.tz)
        if now.second == 0 and now.microsecond == 0:
            start_date = now
        else:
            start_date = now + timedelta(seconds=(60 - now.second))
            start_date = start_date.replace(microsecond=0)

        self.scheduler.add_job(
            self.capture_job,
            IntervalTrigger(seconds=interval_secs, start_date=start_date, timezone=self.tz),
            id='capture_job',
            max_instances=1
        )
        logger.info(f"Scheduled capture job every {interval_secs} seconds, aligned to start at {start_date.strftime('%H:%M:%S')}.")

        # Schedule upload job
        upload_time_str = self.config.get("upload_time", "00:05")
        hour, minute = map(int, upload_time_str.split(":"))
        self.scheduler.add_job(
            self.upload_job,
            CronTrigger(hour=hour, minute=minute, timezone=self.tz),
            id='upload_job',
            max_instances=1
        )
        logger.info(f"Scheduled upload job at {upload_time_str} {self.tz}.")

        self.scheduler.start()

        # Handle graceful shutdown
        signal.signal(signal.SIGINT, self.handle_shutdown)
        signal.signal(signal.SIGTERM, self.handle_shutdown)

        logger.info("Camera Service started.")
        while self.running:
            time.sleep(1)

    def handle_shutdown(self, signum, frame):
        logger.info(f"Received signal {signum}. Shutting down gracefully...")
        self.running = False
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        # Any pending cleanup can be done here
        logger.info("Shutdown complete.")
        sys.exit(0)

if __name__ == "__main__":
    service = CameraService()
    service.start()
