---
name: Switch to Slices S3
overview: Replace the Nextcloud WebDAV uploader with a Slices S3 uploader using boto3. The bucket is ilabt.imec.be-project-coock-aida at https://s3.slices-be.eu. Auth uses S3 access key + secret key via environment variables. The manifest, retry, and scheduling logic remain unchanged.
todos:
  - id: rewrite-uploader
    content: "Rewrite src/uploader.py: replace NextcloudUploader with S3Uploader using boto3, remove MKCOL/WebDAV logic, keep manifest and idempotency intact"
    status: completed
  - id: update-main-import
    content: "Update src/main.py: change import and instantiation from NextcloudUploader to S3Uploader"
    status: completed
  - id: update-env-example
    content: "Update .env.example: replace Nextcloud vars with S3_ACCESS_KEY and S3_SECRET_KEY"
    status: completed
  - id: update-config
    content: "Update config.yaml: replace nextcloud section with s3 section (endpoint_url, bucket, base_path)"
    status: completed
  - id: update-requirements
    content: Add boto3 to requirements.txt
    status: completed
  - id: update-readme
    content: "Update README.md: replace all Nextcloud references with Slices S3 storage"
    status: completed
isProject: false
---

# Switch Upload Target from Nextcloud to Slices S3

## What Changes

S3 object storage is simpler than WebDAV: there are no folders to create, objects are stored with key prefixes (e.g. `Werf Hoboken/timelapses/27_03_26/cam_cam01_14-30-00.jpg`), and uploads use `boto3.client('s3').upload_file()` instead of HTTP PUT with auth tuples.

## Files to Change

### 1. Rewrite [src/uploader.py](src/uploader.py)

Replace `NextcloudUploader` with `S3Uploader`.

Key differences from the current code:

- Use `boto3.client('s3')` with `endpoint_url='https://s3.slices-be.eu'` and `signature_version='s3v4'`
- Read `S3_ACCESS_KEY` and `S3_SECRET_KEY` from env vars
- Bucket name from config: `ilabt.imec.be-project-coock-aida`
- Object key format: `{base_path}/{day_folder}/{filename}` (e.g. `Werf Hoboken/timelapses/27_03_26/cam_cam01_14-30-00.jpg`)
- No `_ensure_remote_folder` needed. S3 has no folder concept; the prefix is implicit in the object key.
- `upload_file` becomes `s3_client.upload_file(local_path, bucket, object_key)`
- Retry: boto3 has built-in retries, but we keep our manifest-level retry logic for consistency
- The manifest, atomic write, deterministic sort, and idempotency logic all stay exactly the same

### 2. Update [src/main.py](src/main.py) line 15 and line 35

Change the import and instantiation:

```python
# Before
from uploader import NextcloudUploader
self.uploader = NextcloudUploader(self.config)

# After
from uploader import S3Uploader
self.uploader = S3Uploader(self.config)
```

### 3. Update [.env.example](.env.example)

Replace the three Nextcloud variables with two S3 variables:

```text
S3_ACCESS_KEY=your_s3_access_key
S3_SECRET_KEY=your_s3_secret_key
```

### 4. Update [config.yaml](config.yaml)

Replace the `nextcloud:` section with:

```yaml
s3:
  endpoint_url: "https://s3.slices-be.eu"
  bucket: "ilabt.imec.be-project-coock-aida"
  base_path: "Werf Hoboken/timelapses"
```

### 5. Update [requirements.txt](requirements.txt)

Add `boto3` (latest). Remove `requests` only if no other module uses it -- but `camera.py` still imports `requests`, so it stays.

```text
boto3
```

### 6. Update [README.md](README.md)

- Replace all Nextcloud references with Slices S3
- Update the "What You Need" section to list S3 access key + secret key instead of Nextcloud credentials
- Update "Step 2" to explain S3_ACCESS_KEY and S3_SECRET_KEY
- Update "Step 3" config.yaml section to show the `s3:` block
- Update "Where Files Go" remote target description
- Update troubleshooting to reference S3 instead of Nextcloud/WebDAV

## What Does NOT Change

- `src/camera.py` -- untouched
- `src/health.py` -- untouched
- `scripts/entrypoint.sh` -- untouched
- `scripts/healthcheck.sh` -- untouched
- `Dockerfile` -- untouched
- `docker-compose.yml` -- untouched (still reads `.env`, still mounts `/data`)
- `.gitignore` -- untouched
- Manifest format and idempotency logic -- same structure, same atomic writes
- Scheduling, retry, disk checks -- all unchanged

