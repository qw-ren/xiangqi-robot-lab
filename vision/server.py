"""Local camera and observation service for the game runner and optional Web UI."""

import argparse
import base64
import logging
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, Thread

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

from .recognizer import BoardRecognizer


def default_camera_index():
    for link in sorted(Path("/dev/v4l/by-id").glob("*video-index0")):
        device = link.resolve()
        index = device.name[5:]
        if device.exists() and device.name.startswith("video") and index.isdecimal():
            return int(index)
    return 0


@contextmanager
def game_vision(camera_index, port=5001):
    """Own the camera and the optional local preview endpoint for one game."""
    recognizer = BoardRecognizer(camera_index=camera_index, vision_backend="aruco")
    server = None
    worker = None
    try:
        server = make_server("127.0.0.1", port, create_app(recognizer), threaded=True)
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


def create_app(recognizer):
    app = Flask(__name__)
    camera = recognizer.camera_manager
    camera_lock = Lock()
    source = {"camera_index": camera.camera_source, "source_label": camera.source_label,
              "source_type": "local", "network_url": None}

    def capture_frame():
        with camera_lock:
            if not camera.is_opened() and not recognizer.start():
                return None
            return camera.capture_frame()

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify(success=False, error=str(error)), 400

    @app.route("/api/cameras")
    def cameras():
        index = camera.camera_source
        return jsonify(success=True, cameras=[{"index": index, "label": f"本地摄像头 {index}",
                       "available": camera.is_opened(), "width": camera.width,
                       "height": camera.height}], current_camera_index=index, **source)

    @app.route("/api/camera/status")
    @app.route("/api/camera/start", methods=["POST"])
    def camera_status():
        if request.method == "POST":
            with camera_lock:
                opened = camera.is_opened() or recognizer.start()
        else:
            opened = camera.is_opened()
        return jsonify(success=opened if request.method == "POST" else True,
                       camera_opened=opened, **source)

    @app.route("/api/camera/frame")
    def frame():
        image = capture_frame()
        if image is None:
            return jsonify(success=False, error="无法获取图像"), 503
        ok, jpeg = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return jsonify(success=False, error="JPEG encode failed"), 500
        return Response(jpeg.tobytes(), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.route("/api/vision/observation", methods=["POST"])
    def observation():
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
        image = capture_frame()
        if image is None:
            return jsonify(success=False, error="无法获取图像"), 503
        return jsonify(success=True, observation=recognizer.recognize_observation(image))

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
