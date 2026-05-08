#!/usr/bin/env python3
"""
Thermal Camera Viewer & Recorder
==================================
Runs on your laptop (Windows/Mac/Linux).
Receives thermal stream from Raspberry Pi via ZeroMQ,
displays false-color video, and records raw HDF5 data on demand.

Usage:
    python thermal_viewer_laptop.py
    python thermal_viewer_laptop.py --host 192.168.0.50 --port 5555

Keys:
    R  —  Start recording (saves HDF5 with raw uint16 frames + timestamps)
    S  —  Stop recording  (video keeps playing)
    Q  —  Quit

Output:
    C:\\Users\\andre\\Downloads\\thermal_YYYYMMDD_HHMMSS.h5

HDF5 structure:
    /thermal_raw     uint16  (n_frames, height, width)
    /timestamp_ms    int64   (n_frames,)             — unix ms
    attrs: width, height, pi_ip, note

Author: Andrea Rodriguez (WAAM Research)
"""

import argparse
import os
import time
from datetime import datetime

import cv2
import h5py
import numpy as np
import zmq

# ── Config ─────────────────────────────────────────────────────────────────────
PI_IP       = "192.168.0.50"
PORT        = 5555
SAVE_FOLDER = r"C:\Users\andre\Downloads"
RECV_TIMEOUT_MS = 3000   # ms before printing "waiting..."
# ──────────────────────────────────────────────────────────────────────────────


def raw_to_colormap(thermal_raw: np.ndarray) -> np.ndarray:
    """
    Convert uint16 raw thermal frame to false-color BGR image.
    Normalizes within the frame so contrast is always maximized.
    """
    # Normalize to 0-255
    mn, mx = thermal_raw.min(), thermal_raw.max()
    if mx == mn:
        gray = np.zeros(thermal_raw.shape, dtype=np.uint8)
    else:
        gray = ((thermal_raw.astype(np.float32) - mn) / (mx - mn) * 255).astype(np.uint8)

    # Apply INFERNO colormap (good for thermal)
    color = cv2.applyColorMap(gray, cv2.COLORMAP_INFERNO)
    return color


def main():
    parser = argparse.ArgumentParser(description="Thermal Camera Viewer & Recorder")
    parser.add_argument("--host",   default=PI_IP)
    parser.add_argument("--port",   type=int, default=PORT)
    parser.add_argument("--output", default=SAVE_FOLDER)
    args = parser.parse_args()

    # ZeroMQ SUB socket
    context = zmq.Context()
    socket  = context.socket(zmq.SUB)
    socket.connect(f"tcp://{args.host}:{args.port}")
    socket.setsockopt(zmq.SUBSCRIBE, b"")
    socket.setsockopt(zmq.RCVTIMEO, RECV_TIMEOUT_MS)
    socket.setsockopt(zmq.RCVHWM, 1)       # keep only 1 frame in receive buffer
    socket.setsockopt(zmq.CONFLATE, 1)     # always deliver newest frame, drop old ones

    print(f"Connecting to Pi at {args.host}:{args.port}")
    print("Keys:  R = Start recording   S = Stop recording   Q = Quit")

    os.makedirs(args.output, exist_ok=True)

    recording   = False
    h5_file     = None
    ds_thermal  = None
    ds_ts       = None
    frame_count = 0
    rec_start   = None
    fps_counter = 0
    fps_time    = time.time()
    fps_display = 0.0

    while True:
        # ── Receive frame ──────────────────────────────────────────────────────
        try:
            meta_bytes    = socket.recv()
            thermal_bytes = socket.recv()
        except zmq.Again:
            print("Waiting for Pi stream...")
            # Still check for keypress
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            continue
        except Exception as e:
            print(f"Error: {e}")
            break

        # ── Parse ──────────────────────────────────────────────────────────────
        meta         = np.frombuffer(meta_bytes, dtype=np.int64)
        timestamp_ms = int(meta[0])
        w, h         = int(meta[1]), int(meta[2])

        thermal = np.frombuffer(thermal_bytes, dtype=np.uint16).reshape(h, w).copy()

        # ── FPS counter ────────────────────────────────────────────────────────
        fps_counter += 1
        now = time.time()
        if now - fps_time >= 1.0:
            fps_display = fps_counter / (now - fps_time)
            fps_counter = 0
            fps_time    = now

        # ── Record ─────────────────────────────────────────────────────────────
        if recording and h5_file is not None:
            ds_thermal.resize(frame_count + 1, axis=0)
            ds_ts.resize(frame_count + 1, axis=0)
            ds_thermal[frame_count] = thermal
            ds_ts[frame_count]      = timestamp_ms
            frame_count += 1

        # ── Display ────────────────────────────────────────────────────────────
        display = raw_to_colormap(thermal)

        ts_str = datetime.fromtimestamp(timestamp_ms / 1000).strftime("%H:%M:%S.%f")[:-3]

        # Top overlay: timestamp and FPS
        cv2.putText(display, ts_str, (10, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)
        cv2.putText(display, ts_str, (10, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cv2.putText(display, f"FPS: {fps_display:.1f}", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)
        cv2.putText(display, f"FPS: {fps_display:.1f}", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

        # Bottom overlay: recording status or controls
        if recording:
            elapsed = time.time() - rec_start
            cv2.putText(display, f"REC {elapsed:.1f}s  {frame_count} frames",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)
            cv2.putText(display, f"REC {elapsed:.1f}s  {frame_count} frames",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        else:
            cv2.putText(display, "R:Record  S:Stop  Q:Quit",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        cv2.imshow("Thermal Camera", display)

        # ── Keys ───────────────────────────────────────────────────────────────
        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break

        elif key == ord('r') and not recording:
            fname      = datetime.now().strftime("thermal_%Y%m%d_%H%M%S.h5")
            fpath      = os.path.join(args.output, fname)
            h5_file    = h5py.File(fpath, 'w')
            ds_thermal = h5_file.create_dataset(
                'thermal_raw',
                shape=(0, h, w),
                maxshape=(None, h, w),
                dtype=np.uint16,
                chunks=(1, h, w),
            )
            ds_ts = h5_file.create_dataset(
                'timestamp_ms',
                shape=(0,),
                maxshape=(None,),
                dtype=np.int64,
            )
            h5_file.attrs['pi_ip']  = args.host
            h5_file.attrs['width']  = w
            h5_file.attrs['height'] = h
            h5_file.attrs['note']   = 'Raw uint16. Conversion TBD.'
            recording   = True
            frame_count = 0
            rec_start   = time.time()
            print(f"Recording started -> {fpath}")

        elif key == ord('s') and recording:
            recording = False
            if h5_file:
                h5_file.close()
                h5_file = None
            print(f"Recording stopped. {frame_count} frames saved.")

    # ── Cleanup ────────────────────────────────────────────────────────────────
    if recording and h5_file:
        h5_file.close()
        print(f"Recording saved. {frame_count} frames.")

    cv2.destroyAllWindows()
    socket.close()
    context.term()
    print("Done.")


if __name__ == "__main__":
    main()
