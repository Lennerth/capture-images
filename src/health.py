import os
import time
import json
import logging
import shutil
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

class HealthMonitor:
    def __init__(self, config):
        self.config = config
        self.tz = pytz.timezone(config.get("timezone", "Europe/Brussels"))
        
        disk_config = config.get("disk", {})
        self.min_free_mb = disk_config.get("min_free_disk_mb", 500)
        self.retention_days = disk_config.get("retention_days", 7)
        
        storage_config = config.get("local_storage", {})
        self.image_dir = storage_config.get("path", "/data/images")
        self.state_dir = storage_config.get("state_path", "/data/state")
        os.makedirs(self.state_dir, exist_ok=True)
        
        self.health_file = os.path.join(self.state_dir, "health.json")
        self.manifest_file = os.path.join(self.state_dir, "upload_manifest.json")

    def _update_health_state(self, key, value):
        state = {}
        if os.path.exists(self.health_file):
            try:
                with open(self.health_file, "r") as f:
                    state = json.load(f)
            except json.JSONDecodeError:
                pass
        
        state[key] = value
        
        tmp_path = self.health_file + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, self.health_file)
        except Exception as e:
            logger.error(f"Failed to update health state: {e}")

    def can_capture(self) -> bool:
        """Check if there's enough disk space to continue capturing."""
        if not os.path.exists(self.image_dir):
            os.makedirs(self.image_dir, exist_ok=True)
            
        usage = shutil.disk_usage(self.image_dir)
        free_mb = usage.free / (1024 * 1024)
        
        if free_mb < self.min_free_mb:
            logger.error(f"Low disk space: {free_mb:.2f} MB free. Minimum required: {self.min_free_mb} MB.")
            return False
            
        return True

    def record_successful_capture(self):
        now_str = datetime.now(self.tz).isoformat()
        self._update_health_state("last_successful_capture", now_str)

    def record_successful_upload(self):
        now_str = datetime.now(self.tz).isoformat()
        self._update_health_state("last_successful_upload", now_str)
        self._cleanup_old_folders()

    def _cleanup_old_folders(self):
        """Removes local day-folders that are older than retention_days AND have been successfully uploaded."""
        if not os.path.exists(self.image_dir):
            return
            
        manifest = {}
        if os.path.exists(self.manifest_file):
            try:
                with open(self.manifest_file, "r") as f:
                    manifest = json.load(f)
            except json.JSONDecodeError:
                pass

        now = datetime.now(self.tz)
        cutoff_date = now - timedelta(days=self.retention_days)
        
        for folder in os.listdir(self.image_dir):
            folder_path = os.path.join(self.image_dir, folder)
            if not os.path.isdir(folder_path):
                continue
                
            try:
                # Folder format is DD_MM_YY
                folder_date = datetime.strptime(folder, "%d_%m_%y")
                folder_date = self.tz.localize(folder_date)
            except ValueError:
                continue # Not a date folder
                
            if folder_date < cutoff_date:
                # Check if it was successfully uploaded
                folder_manifest = manifest.get(folder, {})
                if folder_manifest.get("_folder_complete") is True:
                    logger.info(f"Retention policy: cleaning up old uploaded folder {folder}")
                    try:
                        shutil.rmtree(folder_path)
                        # Optional: remove from manifest to keep it small
                        if folder in manifest:
                            del manifest[folder]
                    except Exception as e:
                        logger.error(f"Failed to delete folder {folder_path}: {e}")
        
        # Save manifest if we deleted entries
        tmp_path = self.manifest_file + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(manifest, f, indent=2)
            os.replace(tmp_path, self.manifest_file)
        except Exception:
            pass


class RetryManager:
    def __init__(self, config):
        retry_config = config.get("retry", {})
        
        self.net_backoff_start = retry_config.get("network_backoff_start_seconds", 60)
        self.net_backoff_max = retry_config.get("network_backoff_max_seconds", 3600)
        
        self.cam_max_failures = retry_config.get("max_consecutive_camera_failures", 10)
        
        # State
        self.network_failures = 0
        self.next_network_attempt = 0.0
        self.camera_skip_counters = {}

    def should_attempt_capture(self) -> bool:
        """Determines if we are in a global network backoff period."""
        if self.network_failures == 0:
            return True
            
        now = time.time()
        if now >= self.next_network_attempt:
            return True
            
        return False

    def record_network_success(self):
        if self.network_failures > 0:
            logger.info("Network connection recovered.")
        self.network_failures = 0
        self.next_network_attempt = 0.0

    def record_network_failure(self):
        self.network_failures += 1
        # Exponential backoff: start * 2^(failures-1)
        backoff = min(self.net_backoff_max, self.net_backoff_start * (2 ** (self.network_failures - 1)))
        self.next_network_attempt = time.time() + backoff
        logger.warning(f"Global network failure #{self.network_failures}. Next attempt in {backoff} seconds.")

    def should_skip_camera(self, camera) -> bool:
        """
        If a camera has failed consecutively more than the threshold, we skip it
        frequently.
        """
        if camera.consecutive_failures >= self.cam_max_failures:
            # Try once every 10 cycles when in max failure state
            counter = self.camera_skip_counters.get(camera.id, 0)
            self.camera_skip_counters[camera.id] = counter + 1
            if (counter % 10) != 0:
                return True
        return False

    def record_camera_success(self, camera):
        if camera.id in self.camera_skip_counters:
            del self.camera_skip_counters[camera.id]

    def record_camera_failure(self, camera):
        pass
