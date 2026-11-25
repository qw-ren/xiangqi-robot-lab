"""Local camera and observation service for the game runner and optional Web UI."""

import argparse
import base64
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, Thread

import cv2
import numpy as np
from flask import Flask, Response, g, jsonify, request
from werkzeug.serving import make_server

from .recognizer import BoardRecognizer
from .runtime_diagnostics import logger as diagnostics


def default_camera_link():
    for link in sorted(Path("/dev/v4l/by-id").glob("*video-index0")):
        device = link.resolve()
        index = device.name[5:]
        if device.exists() and device.name.startswith("video") and index.isdecimal():
            return link
    return None


@contextmanager
def game_vision(camera_index, port=5001, camera_link=None):
    """Own the camera and the optional local preview endpoint for one game."""
    demo_profile = os.getenv("CHRO_VISION_DEMO_PROFILE") == "1"
    if demo_profile and not sys.platform.startswith("linux"):
        raise RuntimeError("CHRO_VISION_DEMO_PROFILE requires Linux V4L2")
    options = dict(width=800, height=600, fps=30, demo_profile=True) if demo_profile else {}
    recognizer = BoardRecognizer(camera_index=camera_index, vision_backend="aruco", **options)
    recognizer.camera_manager.usb_device_link = camera_link
    recognizer.camera_manager.demo_profile = demo_profile
    server = None
    worker = None
    try:
        server = make_server("127.0.0.1", port, create_app(recognizer, allow_live_fallback=False),
                             threaded=True)
        if not recognizer.start():
            raise RuntimeError(f"Could not open camera {camera_index}")
        worker = Thread(target=server.serve_forever, name="game-vision", daemon=True)
        worker.start()
        yield
    finally:
        if worker is not None and worker.is_alive():
            server.shutdown()
            worker.join()
        if server is not None:
            server.server_close()
        recognizer.stop()


def create_app(recognizer, *, allow_live_fallback=True):
    app = Flask(__name__)
    camera = recognizer.camera_manager
    camera_lock = Lock()
    observation_lock = Lock()
    preview_lock = Lock()
    latest_observation = None
    observed_at = float("-inf")
    preview_jpeg = b""
    preview_at = float("-inf")
    def source():
        return {"camera_index": camera.camera_source, "source_label": camera.source_label,
                "source_type": "local", "network_url": None}

    def camera_ready():
        if camera.usb_device_link is not None:
            return camera.latest_frame(max_age=1.5) is not None
        return camera.is_opened()

    def capture_frame():
        started = time.monotonic()
        with camera_lock:
            if camera.usb_device_link is not None:
                image = camera.latest_frame(max_age=1.5)
            else:
                image = camera.capture_frame() if camera.is_opened() or recognizer.start() else None
        elapsed = time.monotonic() - started
        if image is None or elapsed >= 1.0:
            diagnostics.warning("Camera capture index=%s success=%s elapsed=%.2fs",
                                camera.camera_source, image is not None, elapsed)
        return image

    @app.before_request
    def start_request_timer():
        g.request_started = time.monotonic()

    @app.after_request
    def log_slow_request(response):
        elapsed = time.monotonic() - g.request_started
        if elapsed >= 1.0 or response.status_code >= 500:
            diagnostics.warning("HTTP %s %s status=%s elapsed=%.2fs",
                                request.method, request.path, response.status_code, elapsed)
        return response

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify(success=False, error=str(error)), 400

    @app.route("/api/cameras")
    def cameras():
        index = camera.camera_source
        return jsonify(success=True, cameras=[{"index": index, "label": f"本地摄像头 {index}",
                       "available": camera_ready(), "width": camera.width,
                       "height": camera.height}], current_camera_index=index, **source())

    @app.route("/api/camera/status")
    @app.route("/api/camera/start", methods=["POST"])
    def camera_status():
        if request.method == "POST" and camera.usb_device_link is None:
            with camera_lock:
                opened = camera.is_opened() or recognizer.start()
        else:
            opened = camera_ready()
        return jsonify(success=opened if request.method == "POST" else True,
                       camera_opened=opened, **source())

    @app.route("/api/camera/frame")
    def frame():
        nonlocal preview_jpeg, preview_at
        with preview_lock:
            if time.monotonic() - preview_at >= 0.35:
                image = capture_frame()
                if image is None:
                    return jsonify(success=False, error="无法获取图像"), 503
                ok, jpeg = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not ok:
                    return jsonify(success=False, error="JPEG encode failed"), 500
                preview_jpeg = jpeg.tobytes()
                preview_at = time.monotonic()
            return Response(preview_jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.route("/api/vision/observation", methods=["POST"])
    def observation():
        nonlocal latest_observation, observed_at
        data = request.get_json(silent=True) or {}
        uploaded = data.get("image")
        if uploaded:
            image = cv2.imdecode(np.frombuffer(base64.b64decode(uploaded, validate=True),
                                               dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("无法解码图片")
            # Uploaded snapshots must not interrupt the live observation stream.
            image_recognizer = BoardRecognizer(vision_backend="aruco")
            return jsonify(success=True, observation=image_recognizer.recognize_observation(image))
        with observation_lock:
            if time.monotonic() - observed_at >= 1.0:
                image = capture_frame()
                if image is None:
                    return jsonify(success=False, error="无法获取图像"), 503
                started = time.monotonic()
                diagnostics.info("Live recognition started")
                latest_observation = recognizer.recognize_observation(image, allow_fallback=allow_live_fallback)
                observed_at = time.monotonic()
                diagnostics.info("Live recognition finished frame=%s source=%s reasons=%s elapsed=%.2fs",
                                 latest_observation.get("frame_id"), latest_observation.get("source"),
                                 latest_observation.get("reasons"), observed_at - started)
            return jsonify(success=True, observation=latest_observation)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    recognizer = BoardRecognizer(camera_index=args.camera, vision_backend="aruco")
    try:
        create_app(recognizer).run(host="127.0.0.1", port=args.port, threaded=True)
    finally:
        recognizer.stop()


if __name__ == "__main__":
    main()
