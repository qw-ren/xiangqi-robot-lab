"""网页摄像头恢复回归测试；不打开任何真实设备。"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from web_simulation import app as web_app, services
from web_simulation.routes import camera


def fake_manager():
    return SimpleNamespace(
        _frame_lock=threading.Lock(), last_frame=np.ones((4, 4, 3)),
        last_frame_time=time.monotonic(), is_network_source=False,
        stop=Mock(), start=Mock(return_value=True),
        is_opened=Mock(return_value=True), capture_frame=Mock(return_value='fresh'))


def test_scan_releases_active_camera_before_probe_and_clears_stale_frame():
    manager = fake_manager()
    def probe(index):
        manager.stop.assert_called_once()
        assert manager.last_frame is None
        assert manager.last_frame_time == 0
        return {'width': 640, 'height': 480} if index == 0 else None
    with patch.object(web_app, 'recognizer', SimpleNamespace(camera_manager=manager)), \
            patch.object(web_app, 'camera_probe_cache', None), \
            patch.object(web_app, 'active_stream_token', 0), \
            patch.object(services, 'get_windows_camera_names', return_value=[]), \
            patch.object(services, 'probe_camera_index', side_effect=probe):
        cameras = services.list_available_cameras(max_cameras=2, refresh=True)
        assert [item['index'] for item in cameras] == [0]
        assert web_app.active_stream_token == 1


def test_cached_list_does_not_interrupt_live_camera():
    with patch.object(web_app, 'camera_probe_cache', [{'index': 2}]), \
            patch.object(services, 'release_web_camera') as release:
        assert services.list_available_cameras() == [{'index': 2}]
        release.assert_not_called()


def test_closed_camera_reopens_same_source_instead_of_returning_cached_frame():
    manager = fake_manager()
    manager.is_opened.return_value = False
    with patch.object(camera, 'get_recognizer', return_value=SimpleNamespace(camera_manager=manager)):
        assert camera.read_web_camera_frame(0) == 'fresh'
    manager.start.assert_called_once()
    assert manager.last_frame is None


def test_stale_open_handle_is_reopened_and_failed_restart_returns_no_frame():
    manager = fake_manager()
    manager.last_frame_time = time.monotonic() - 10
    manager.start.return_value = False
    with patch.object(camera, 'get_recognizer', return_value=SimpleNamespace(camera_manager=manager)):
        assert camera.read_web_camera_frame(0) is None
    manager.stop.assert_called_once()
    manager.capture_frame.assert_not_called()


def test_frame_endpoint_busy_device_returns_uncached_error():
    lock = threading.Lock()
    lock.acquire()
    with patch.object(web_app, 'camera_lock', lock):
        response = web_app.app.test_client().get('/api/camera/frame?camera_index=0')
    lock.release()
    assert response.status_code == 503
    assert 'no-store' in response.headers['Cache-Control']


def test_failed_frame_is_not_cached():
    with patch.object(camera, 'read_web_camera_frame', return_value=None):
        response = web_app.app.test_client().get('/api/camera/frame?camera_index=0')
    assert response.status_code == 503
    assert 'no-store' in response.headers['Cache-Control']
