"""Forward preview requests when another process owns the camera."""

import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Response, jsonify, request


CAMERA_ROUTES = frozenset(("/api/cameras", "/api/camera/start", "/api/camera/status", "/api/camera/frame"))
OBSERVATION_ROUTES = frozenset(("/api/recognize", "/api/recognize/dynamic", "/api/vision/observation"))
EXCLUSIVE_CAMERA_ROUTES = frozenset(("/api/camera/stream", "/api/capture", "/api/test/camera",
                                    "/api/network_camera/connect", "/api/network_camera/disconnect"))


def forward_vision():
    base = os.getenv("CHRO_VISION_SERVICE_URL")
    if not base:
        return None
    path = request.path
    if path in EXCLUSIVE_CAMERA_ROUTES:
        return jsonify(success=False, error="当前摄像头由独立视觉服务管理"), 409
    if path not in CAMERA_ROUTES and path not in OBSERVATION_ROUTES:
        return None
    observation = path in OBSERVATION_ROUTES
    target = "/api/vision/observation" if observation else path
    upstream = Request(base.rstrip("/") + target, method=request.method,
                       data=request.get_data() if request.method == "POST" else None,
                       headers={"Content-Type": "application/json"})
    try:
        with urlopen(upstream, timeout=20 if observation else 8) as response:
            data = response.read()
            if observation:
                from .recognition import aruco_preview_payload
                payload = json.loads(data)
                return jsonify(aruco_preview_payload(payload["observation"]))
            return Response(data, status=response.status,
                            content_type=response.headers.get("Content-Type"),
                            headers={"Cache-Control": "no-store"})
    except HTTPError as exc:
        return Response(exc.read(), status=exc.code, content_type=exc.headers.get("Content-Type"))
    except (URLError, socket.timeout):
        return jsonify(success=False, error="独立视觉服务未响应"), 503
