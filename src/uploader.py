import os
import json
import time
import logging
import requests
from urllib.parse import quote
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

class NextcloudUploader:
    def __init__(self, config):
        self.config = config
        self.retry_config = config.get("retry", {})
        self.max_retries = self.retry_config.get("upload_max_retries", 3)
        self.retry_spacing = self.retry_config.get("upload_retry_spacing_seconds", 60)
        
        nc_config = config.get("nextcloud", {})
        self.base_path = nc_config.get("base_path", "Werf Hoboken/timelapses").strip('/')
        
        self.username = os.environ.get("NEXTCLOUD_USER")
        self.password = os.environ.get("NEXTCLOUD_PASSWORD")
        self.base_url = os.environ.get("NEXTCLOUD_BASE_URL", "").rstrip('/')
        
        self.auth = (self.username, self.password) if self.username and self.password else None
        
        storage_config = config.get("local_storage", {})
        self.state_dir = storage_config.get("state_path", "/data/state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.manifest_path = os.path.join(self.state_dir, "upload_manifest.json")
        self.tz = pytz.timezone(config.get("timezone", "Europe/Brussels"))

    def _load_manifest(self):
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.error("Failed to parse upload manifest, starting fresh.")
        return {}

    def _save_manifest(self, manifest):
        tmp_path = self.manifest_path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(manifest, f, indent=2)
            os.replace(tmp_path, self.manifest_path)
        except Exception as e:
            logger.error(f"Error saving manifest: {e}")

    def _get_dav_url(self, path):
        # Build the full WebDAV URL properly escaping the path parts
        parts = path.strip('/').split('/')
        escaped_path = '/'.join(quote(p) for p in parts)
        return f"{self.base_url}/{escaped_path}"

    def _ensure_remote_folder(self, day_folder):
        """Creates the base path and the specific day folder via MKCOL if they don't exist."""
        if not self.base_url:
            logger.error("NEXTCLOUD_BASE_URL is not set. Cannot ensure remote folder.")
            return False

        segments = self.base_path.split('/') + [day_folder]
        current_path = ""
        
        for segment in segments:
            if not segment:
                continue
            current_path = f"{current_path}/{segment}" if current_path else segment
            url = self._get_dav_url(current_path)
            
            success = False
            for attempt in range(self.max_retries):
                try:
                    resp = requests.request("MKCOL", url, auth=self.auth, timeout=10)
                    if resp.status_code in [201, 405]: # 201 Created, 405 Method Not Allowed (already exists)
                        success = True
                        break
                    logger.warning(f"MKCOL {current_path} failed with status {resp.status_code}: {resp.text}")
                except requests.RequestException as e:
                    logger.warning(f"MKCOL network error for {current_path}: {e}")
                
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_spacing)
            
            if not success:
                return False
                
        return True

    def upload_file(self, local_filepath, remote_dir, filename):
        remote_path = f"{remote_dir}/{filename}"
        dav_url = self._get_dav_url(remote_path)
        
        for attempt in range(self.max_retries):
            try:
                with open(local_filepath, 'rb') as f:
                    resp = requests.put(dav_url, data=f, auth=self.auth, timeout=60)
                    
                if resp.status_code in [201, 204]:
                    return True
                logger.warning(f"Failed to upload {filename}, status: {resp.status_code}")
            except Exception as e:
                logger.warning(f"Error uploading {filename}: {e}")
            
            time.sleep(self.retry_spacing)
        
        return False

    def upload_pending_folders(self, local_storage_path, health_monitor):
        """
        Scans local storage for day folders that are not the current day,
        and uploads their contents.
        """
        if not self.base_url or not self.auth:
            logger.error("Nextcloud credentials or URL not configured properly.")
            return

        manifest = self._load_manifest()
        
        # Determine the current day string to skip it
        now = datetime.now(self.tz)
        current_day_str = now.strftime("%d_%m_%y")
        
        if not os.path.exists(local_storage_path):
            return
            
        day_folders = [d for d in os.listdir(local_storage_path) if os.path.isdir(os.path.join(local_storage_path, d))]
        
        def safe_date(d_str):
            try:
                return datetime.strptime(d_str, "%d_%m_%y")
            except ValueError:
                return datetime.min
                
        day_folders.sort(key=safe_date)
        
        uploaded_something = False
        
        for day_folder in day_folders:
            if day_folder == current_day_str:
                continue # Skip current day, it's still being written to
                
            day_path = os.path.join(local_storage_path, day_folder)
            files = [f for f in os.listdir(day_path) if os.path.isfile(os.path.join(day_path, f))]
            
            if not files:
                continue # Empty folder
                
            if day_folder not in manifest:
                manifest[day_folder] = {}
                
            # Create remote folder
            remote_dir = f"{self.base_path}/{day_folder}"
            if not self._ensure_remote_folder(day_folder):
                logger.error(f"Could not create remote folder {day_folder}, skipping upload.")
                continue
                
            all_success = True
            for filename in files:
                if manifest[day_folder].get(filename) == "success":
                    continue # Already uploaded
                    
                local_filepath = os.path.join(day_path, filename)
                
                logger.info(f"Uploading {filename} to {day_folder}...")
                success = self.upload_file(local_filepath, remote_dir, filename)
                
                if success:
                    manifest[day_folder][filename] = "success"
                    uploaded_something = True
                    self._save_manifest(manifest)
                else:
                    all_success = False
            
            if all_success:
                manifest[day_folder]["_folder_complete"] = True
                self._save_manifest(manifest)
                
        if uploaded_something:
            health_monitor.record_successful_upload()
            logger.info("Upload job completed successfully.")
        else:
            logger.info("No new files to upload.")
