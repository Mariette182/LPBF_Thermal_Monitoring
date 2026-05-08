#!/usr/bin/env python3
"""
Thermal Camera Stream Server
=============================
Runs on Raspberry Pi. Captures frames from Optris camera via OTC SDK
and streams them over ZeroMQ to any laptop on the same network.

Usage:
    python3 thermal_stream_pi.py
    python3 thermal_stream_pi.py --port 5555

Author: Andrea Rodriguez (WAAM Research)
"""

import sys
import time
import queue
import threading
import argparse
import logging
import numpy as np
import zmq
import optris.otcsdk as otc

# ── Config ─────────────────────────────────────────────────────────────────────
QUEUE_SIZE  = 32   # max frames in queue before dropping (capture never blocks)
PORT        = 5555
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("stream_pi")


class ThermalStreamer(otc.IRImagerClient):
    """
    Captures thermal frames from OTC SDK in a callback thread,
    pushes raw data into a queue, and streams to laptops via ZeroMQ PUB.

    Two threads:
      - OTC SDK thread  → onThermalFrame callback → queue
      - Stream thread   → queue → ZeroMQ send
    """

    def __init__(self, serial_number: int, port: int):
        super().__init__()

        # ZeroMQ PUB socket — any laptop can subscribe
        self._context = zmq.Context()
        self._socket  = self._context.socket(zmq.PUB)
        self._socket.setsockopt(zmq.SNDHWM, 1)      # keep only 1 frame in send buffer
        self._socket.setsockopt(zmq.LINGER, 0)      # don't wait on close
        self._socket.bind(f"tcp://*:{port}")
        log.info("ZeroMQ PUB bound on port %d", port)
        log.info("Laptops connect to: tcp://<PI_IP>:%d", port)

        # Frame queue between callback and stream thread
        self._queue = queue.Queue(maxsize=QUEUE_SIZE)

        # OTC SDK camera
        self._imager = otc.IRImagerFactory.getInstance().create('native')
        self._imager.addClient(self)
        self._imager.connect(serial_number)

        self._keep_running = threading.Event()
        self._sdk_thread   = None
        self._stream_thread = None

        # Frame dimensions (set on first frame)
        self._width  = 0
        self._height = 0


    def run(self):
        """Start SDK thread and stream thread, block until stopped."""
        self._keep_running.set()

        # Thread 1: OTC SDK frame grabbing (triggers onThermalFrame callbacks)
        self._sdk_thread = threading.Thread(
            target=self._imager.run,
            name="sdk_capture",
            daemon=True
        )
        self._sdk_thread.start()

        # Thread 2: Send frames from queue over ZeroMQ
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            name="zmq_stream",
            daemon=True
        )
        self._stream_thread.start()

        log.info("Streaming started. Press Ctrl+C to stop.")

        try:
            self._sdk_thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


    def stop(self):
        self._keep_running.clear()
        self._imager.stopRunning()
        self._socket.close()
        self._context.term()
        log.info("Streamer stopped.")


    def _stream_loop(self):
        """Dequeue frames and send over ZeroMQ."""
        dropped = 0
        while self._keep_running.is_set():
            try:
                timestamp_ms, thermal = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            h, w = thermal.shape

            # Metadata: [timestamp_ms, width, height]
            meta = np.array([timestamp_ms, w, h], dtype=np.int64)

            try:
                self._socket.send(meta.tobytes(),    zmq.SNDMORE | zmq.NOBLOCK)
                self._socket.send(thermal.tobytes(), zmq.NOBLOCK)
            except zmq.Again:
                dropped += 1
                if dropped % 100 == 0:
                    log.warning("Dropped %d frames (no subscribers or slow network)", dropped)


    # ── OTC SDK callbacks ──────────────────────────────────────────────────────

    def onThermalFrame(self, thermal: otc.ThermalFrame, meta):
        """Called by SDK thread for every new frame."""
        if thermal.isEmpty():
            return

        w = thermal.getWidth()
        h = thermal.getHeight()

        # Copy raw uint16 data into numpy array
        raw = np.empty(w * h, dtype=np.uint16)
        thermal.copyDataTo(raw)
        raw = raw.reshape(h, w)

        timestamp_ms = int(time.time() * 1000)

        # Drop oldest frame if queue is full (never block capture)
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass

        try:
            self._queue.put_nowait((timestamp_ms, raw))
        except queue.Full:
            pass


    def onFlagStateChange(self, flagState):
        log.info("Flag state: %s", otc.flagStateToString(flagState))

    def onConnectionLost(self):
        log.error("Connection to camera lost.")
        self._keep_running.clear()

    def onConnectionTimeout(self):
        log.error("Camera connection timeout.")
        self._keep_running.clear()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Thermal Camera Stream Server (OTC SDK)")
    parser.add_argument("--serial", type=int, default=0,
                        help="Camera serial number (0 = auto-detect)")
    parser.add_argument("--port",   type=int, default=PORT,
                        help=f"ZeroMQ port (default: {PORT})")
    args = parser.parse_args()

    otc.Sdk.init(otc.Verbosity_Info, otc.Verbosity_Off, sys.argv[0])
    otc.EnumerationManager.getInstance().addEthernetDetector("192.168.0.0/24")

    try:
        streamer = ThermalStreamer(args.serial, args.port)
    except otc.SDKException as ex:
        log.error("SDK error: %s", ex)
        sys.exit(1)

    try:
        streamer.run()
    except KeyboardInterrupt:
        log.info("Stopped by user.")


if __name__ == "__main__":
    main()
