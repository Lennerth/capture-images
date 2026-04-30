# NVR Camera Capture Service

## What This Code Does
Every minute during daylight hours, it grabs a JPEG from each camera through the on-site NVR, stores it in dated folders, and uploads each completed day to S3.

## What You Need Before You Start
- **Docker** and **Docker Compose** installed on your Linux machine.
- The **NVR IP address, username, and password**.
- Your **Slices S3 access key and secret key**.

---

## Step-By-Step Setup

### Step 1: Clone the repo
```bash
git clone <repo url>
cd "capture images"
```

### Step 2: Create `.env`
Copy the example environment file and fill in your secrets.
```bash
cp .env.example .env
```
Open `.env` and fill in:
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
- `NVR_PASSWORD`

### Step 3: Edit `config.yaml`
Open `config.yaml` to configure your NVR and cameras.
1. Under `nvr:`, set `host` (e.g., `"192.168.1.229"`) and `username`.
2. Under `cameras:`, list your cameras. Set `type: nvr_rtsp` and assign a `channel` number (e.g., 1, 2, 3) to each. You can leave the placeholder channel numbers for now and fix them in Step 5.
3. Optionally adjust `timezone`, `capture_window`, and `upload_time`.

### Step 4: Build and start
```bash
docker compose up -d --build
docker compose logs -f capture-app
```
Watch the logs. Success looks like log lines for each camera saying it captured the channel to `/data/images/...`. Press `Ctrl+C` to exit the logs (the container keeps running).

### Step 5: Verify channel mapping
You need to make sure channel 1 is actually the camera you think it is.
Run the included test script:
```bash
bash scripts/test_channels.sh 1 2 3 4
```
This will download `channel_1.jpg`, `channel_2.jpg`, etc. to your current folder. Open them and look at the images. If the names in `config.yaml` don't match the views, update the `channel:` numbers in `config.yaml`, then restart the service:
```bash
docker compose restart capture-app
```

### Step 6: Confirm S3 upload
To test the upload without waiting until midnight:
1. Open `config.yaml` and change `upload_time` to 5 minutes from now.
2. `docker compose restart capture-app`
3. Wait for the time to pass and check your S3 bucket.
4. Change `upload_time` back to `"00:05"` and restart again.

---

## Step 7: Day-to-Day Operations

- **View logs:** `docker compose logs -f capture-app`
- **Restart service:** `docker compose restart capture-app`
- **Stop service:** `docker compose down`
- **Stop and wipe all local data:** `docker compose down -v`

---

## Troubleshooting

- **"All captures fail with timeout"**
  The NVR might use a different RTSP URL format. Open `config.yaml` and try changing `url_template` to one of these alternatives:
  - `rtsp://{user}:{password}@{host}:{port}/h264/ch{channel}/main/av_stream`
  - `rtsp://{user}:{password}@{host}:{port}/user={user}&password={password}&channel={channel}&stream={subtype}.sdp`
  Also, try increasing `timeout_seconds` from 15 to 20. (The timeout needs to be high enough to wait for the next video keyframe).

- **"Some channels show the wrong camera view"**
  Re-run `bash scripts/test_channels.sh 1 2 3 4`, check the JPEGs, and fix the `channel:` numbers in `config.yaml`.

- **"The container is unhealthy"**
  This means captures have been failing for a while and `health.json` is stale. Check the logs (`docker compose logs --tail=100 capture-app`) to see why ffmpeg is failing.

- **"Uploads never happen"**
  Check that your `.env` file exists and has the correct S3 keys. Also, uploads only process *completed* day folders (yesterday or older).

---

## Security Notes for the NVR
Because this is a Xiongmai-based NVR, you should lock it down:
1. **Disable Cloud:** In the NVR UI, go to Network → Advanced → Cloud (or "P2P") and disable XMEye/Cloud connectivity.
2. **Block Internet:** Block the NVR from outbound internet access at your router/gateway. Only allow NTP (UDP 123) outbound.
3. **Change Passwords:** Change the default admin password on the device.

---

## What the Code Does Internally (For Power Users)
1. **Scheduled camera snapshots:** It concurrently pulls a single RTSP frame using `ffmpeg` for each camera between the daytime window, aligned exactly to wall-clock seconds.
2. **Daily local folders:** It stores images in daily folders such as `27_03_26`, using filenames like `cam_cam01_14-30-00.jpg`.
3. **Daily Slices S3 upload:** At the configured upload time, it uploads every non-current day folder to the Slices S3 bucket under `Werf Hoboken/timelapses/DD_MM_YY/`.
4. **State tracking:** It stores upload and health state in `/data/state/upload_manifest.json` and `/data/state/health.json`.
5. **Safety behavior:**
   - It skips captures if free disk space drops below the configured minimum.
   - It uses exponential backoff for true network failures, but handles camera failures independently.
   - It reduces retry frequency for cameras that keep failing repeatedly.
   - It deletes old local folders only after they were fully uploaded and passed the retention window.
