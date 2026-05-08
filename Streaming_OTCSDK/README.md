# LPBF Thermal Monitoring

Real-time thermal camera streaming and recording system for LPBF (Laser Powder Bed Fusion) process monitoring.  
Developed at **WAAM Research** by Andrea Rodriguez.

---

## Overview

This system streams thermal data from an **Optris camera** connected to a **Raspberry Pi** to any laptop on the same network. It uses **ZeroMQ** for low-latency streaming and saves raw data in **HDF5** format for post-processing.

```
Optris Camera → Raspberry Pi (thermal_stream_pi.py) ──ZeroMQ──► Laptop (thermal_viewer_laptop.py)
```

---

## Files

| File | Runs on | Description |
|------|---------|-------------|
| `thermal_stream_pi.py` | Raspberry Pi | Captures frames via Optris OTC SDK and streams over ZeroMQ PUB |
| `thermal_viewer_laptop.py` | Laptop (Win/Mac/Linux) | Receives stream, displays false-color video, records to HDF5 |

---

## Requirements

### Raspberry Pi
```
pip install pyzmq numpy
# + Optris OTC SDK (install separately from Optris)
```

### Laptop
```
pip install pyzmq numpy opencv-python h5py
```

---

## Usage

### 1. Start the stream on the Pi
```bash
python3 thermal_stream_pi.py
# or specify port:
python3 thermal_stream_pi.py --port 5555
```

### 2. Connect from the laptop
```bash
python thermal_viewer_laptop.py --host <PI_IP_ADDRESS> --port 5555
```
> Default Pi IP in the script is `192.168.0.50` — change it with `--host` or edit `PI_IP` at the top of the file.

### Viewer controls

| Key | Action |
|-----|--------|
| `R` | Start recording (saves HDF5) |
| `S` | Stop recording |
| `Q` | Quit |

---

## Output (HDF5)

Recordings are saved to `C:\Users\andre\Downloads\` by default (change with `--output`).

```
thermal_YYYYMMDD_HHMMSS.h5
├── thermal_raw      uint16  (n_frames, height, width)
└── timestamp_ms     int64   (n_frames,)   ← Unix milliseconds
attrs: width, height, pi_ip, note
```

---

## Network Setup

Both devices must be on the **same local network**. Find the Pi's IP with:
```bash
hostname -I
```

---

## Notes

- Frames are dropped (never blocked) if the network is slow — capture integrity is preserved.
- The viewer always displays the **newest frame** (old frames are discarded on receive).
- Raw `uint16` values are Optris-specific counts — apply Optris calibration for real temperatures.
