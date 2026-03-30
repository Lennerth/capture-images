import os
import json
import time
import logging
import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

class S3Uploader:
    def __init__(self, config):
        self.config = config
        self.retry_config = config.get("retry", {})
        self.max_retries = self.retry_config.get("upload_max_retries", 3)
        self.retry_spacing = self.retry_config.get("upload_retry_spacing_seconds", 60)
        
        s3_config = config.get("s3", {})
        self.base_path = s3_config.get("base_path", "Werf Hoboken/timelapses").strip('/')
        self.bucket = s3_config.get("bucket", "ilabt.imec.be-project-coock-aida")
        self.endpoint_url = s3_config.get("endpoint_url", "https://s3.slices-be.eu")
        
        self.access_key = os.environ.get("S3_ACCESS_KEY")
        self.secret_key = os.environ.get("S3_SECRET_KEY")
        
        storage_config = config.get("local_storage", {})
        self.state_dir = storage_config.get("state_path", "/data/state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.manifest_path = os.path.join(self.state_dir, "upload_manifest.json")
        self.tz = pytz.timezone(config.get("timezone", "Europe/Brussels"))

        if self.access_key and self.secret_key:
            self.s3_client = boto3.client(
                's3',
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                config=Config(signature_version='s3v4')
            )
        else:
            self.s3_client = None

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

    def upload_file(self, local_filepath, day_folder, filename):
        if not self.s3_client:
            return False
            
        object_key = f"{self.base_path}/{day_folder}/{filename}"
        if object_key.startswith('/'):
            object_key = object_key[1:]
            
        for attempt in range(self.max_retries):
            try:
                self.s3_client.upload_file(local_filepath, self.bucket, object_key)
                return True
            except (BotoCoreError, ClientError) as e:
                logger.warning(f"Failed to upload {filename} to S3: {e}")
            except Exception as e:
                logger.warning(f"Error uploading {filename}: {e}")
            
            time.sleep(self.retry_spacing)
        
        return False

    def upload_pending_folders(self, local_storage_path, health_monitor):
        """
        Scans local storage for day folders that are not the current day,
        and uploads their contents.
        """
        if not self.s3_client:
            logger.error("S3 credentials not configured properly.")
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
                
            all_success = True
            for filename in files:
                if manifest[day_folder].get(filename) == "success":
                    continue # Already uploaded
                    
                local_filepath = os.path.join(day_path, filename)
                
                logger.info(f"Uploading {filename} to {day_folder} in S3...")
                success = self.upload_file(local_filepath, day_folder, filename)
                
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
