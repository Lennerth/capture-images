# Camera Capture Cron Service

## What This Code Does
This service runs inside a Docker container and automates camera snapshots plus daily cloud upload.

Here is the exact current behavior:
1. **WireGuard startup attempt:** On container start, it tries to bring up WireGuard using `/etc/wireguard/wg0.conf`.
2. **Scheduled camera snapshots:** It calls the configured HTTP snapshot URL for each camera on a schedule from `config.yaml`.
3. **Daily local folders:** It stores images in daily folders such as `27_03_26`, using filenames like `cam_cam01_14-30-00.jpg`.
4. **Daily Slices S3 upload:** At the configured upload time, it uploads every non-current day folder to the Slices S3 bucket under `Werf Hoboken/timelapses/DD_MM_YY/`.
5. **State tracking:** It stores upload and health state in `/data/state/upload_manifest.json` and `/data/state/health.json`.
6. **Safety behavior:**
   - It skips captures if free disk space drops below the configured minimum.
   - It uses exponential backoff for global network failure detection.
   - It reduces retry frequency for cameras that keep failing repeatedly.
   - It deletes old local folders only after they were fully uploaded and passed the retention window.

Important limits of the current code:
- If WireGuard startup fails, the app still continues and logs a warning.
- The health check tests VPN reachability to `100.66.241.254`, process liveness, and stale health state.
- `restart: always` restarts the container if the main process exits. An `unhealthy` status alone does not guarantee a restart in plain Docker Compose.
- The current uploader expects S3 access keys.

---

## What You Need Before Starting
You need all of the following:
- **Docker** and **Docker Compose**
- A Docker environment that supports Linux containers, `NET_ADMIN`, and `/dev/net/tun`
- Your WireGuard `.conf` file
- Your camera HTTP snapshot URLs
- Any camera usernames/passwords, if the cameras require login
- Your Slices S3 access key
- Your Slices S3 secret key

If any one of those is missing, the service may start, but it will not work fully.

---

## Step-By-Step Setup

### Step 1: Put the VPN file in the right place
The container expects the WireGuard file to be mounted from the project folder.

Right now, `docker-compose.yml` is hard-coded to use this exact filename:
- `lennert-hoboken.conf`

So do one of these:
1. Rename your WireGuard config file to `lennert-hoboken.conf` and place it in the project root.
2. Or edit `docker-compose.yml` yourself so it mounts your real filename to `/etc/wireguard/wg0.conf`.

If you do nothing here, the app will still start, but it will log that it is proceeding without VPN.

### Step 2: Create the `.env` file for S3 credentials
The code reads cloud credentials from a file named `.env`.

Do this:
1. Copy `.env.example` to `.env`.
2. Open `.env`.
3. Fill in both values.

What each value means:
- `S3_ACCESS_KEY`: your Slices S3 access key
- `S3_SECRET_KEY`: your Slices S3 secret key

### Step 3: Fill in `config.yaml`
This file controls when the service runs, which cameras it calls, and where it stores data.

#### Basic settings
- `timezone`: timezone used for timestamps, daily folder naming, and upload scheduling
- `capture_interval_seconds`: how often to take pictures
- `upload_time`: what time to upload completed day folders

Example:
```yaml
timezone: "Europe/Brussels"
capture_interval_seconds: 60
upload_time: "00:05"
```

#### Retry settings
These control retry-related behavior:
- `network_backoff_start_seconds`: first wait time after a full network failure
- `network_backoff_max_seconds`: longest wait time for repeated network failures
- `upload_max_retries`: how many times to retry a single upload
- `upload_retry_spacing_seconds`: wait between upload retries
- `max_consecutive_camera_failures`: after this many failures, a camera gets skipped on most cycles

Notes:
- The current code does use global network backoff.
- The current code does **not** implement true per-camera exponential backoff, even if similar keys exist in the config template.

#### Disk and retention settings
- `min_free_disk_mb`: below this free space, captures stop
- `retention_days`: fully uploaded day folders older than this are deleted locally

#### Camera settings
You need one entry per camera.

For each camera:
- `id`: short unique ID, best without spaces
- `name`: human-readable label
- `type`: currently `http`
- `snapshot_url`: full snapshot URL
- `timeout_seconds`: request timeout
- `auth.username` and `auth.password`: optional camera login

Example:
```yaml
cameras:
  - id: cam01
    name: "Hoboken North"
    type: http
    snapshot_url: "http://100.66.241.123/snapshot.jpg"
    timeout_seconds: 10
    auth:
      username: ""
      password: ""
```

#### S3 target path
This is the object prefix path inside your bucket:

```yaml
s3:
  endpoint_url: "https://s3.slices-be.eu"
  bucket: "ilabt.imec.be-project-coock-aida"
  base_path: "Werf Hoboken/timelapses"
```

The code will upload objects to this bucket using the `base_path` as a prefix.

#### Local storage paths
These are the in-container storage locations:
- `local_storage.path`: where images go
- `local_storage.state_path`: where state files go

Default values:
```yaml
local_storage:
  path: "/data/images"
  state_path: "/data/state"
```

Leave these alone unless you know why you want to change them.

### Step 4: Start the service
From the project root, run:

```bash
docker-compose up -d --build
```

This will:
1. Build the image
2. Start the container
3. Attempt to bring up WireGuard
4. Start the Python scheduler

### Step 5: Watch the logs
To check whether startup worked:

```bash
docker-compose logs -f capture-app
```

What you want to see:
- the scheduler started
- capture jobs are scheduled
- camera snapshots succeed
- upload jobs run at the configured time

Press `Ctrl + C` to stop watching logs. This does not stop the container.

---

## Where Files Go

Local files are stored in the Docker volume mounted at `/data`.

Inside that volume:
- `/data/images/DD_MM_YY/` contains snapshot images
- `/data/state/upload_manifest.json` tracks uploaded files
- `/data/state/health.json` tracks last successful capture and upload times

Remote files are uploaded to:
- S3 Bucket: `ilabt.imec.be-project-coock-aida`
- Object prefix: `Werf Hoboken/timelapses/DD_MM_YY/`

---

## Troubleshooting

### "The app started but cameras still do not work"
Check these first:
1. Did WireGuard actually come up?
2. Does the camera snapshot URL work from inside the VPN?
3. Did you put the right camera IP and path in `snapshot_url`?
4. Does the camera require username/password?

### "Uploads never happen"
Check these:
1. Is `.env` present?
2. Are `S3_ACCESS_KEY` and `S3_SECRET_KEY` filled in?
3. Did the local images land in a non-current day folder yet?

### "The container is unhealthy"
The current health check marks the container unhealthy if:
- the `wg0` interface exists but `100.66.241.254` cannot be pinged
- the main process is gone
- `/data/state/health.json` becomes stale

### "Where did my disk space go?"
Images accumulate under `/data/images`. They are only deleted locally after:
1. upload succeeded for the folder
2. the folder was marked complete
3. the folder is older than `retention_days`

### Stop or reset
- Stop the service: `docker-compose down`
- Stop and delete the data volume too: `docker-compose down -v`