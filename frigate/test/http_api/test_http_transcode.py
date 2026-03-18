"""Unit tests for the transcode API endpoint and ffmpeg command builder."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import Request

from frigate.api.auth import get_allowed_cameras_for_filter, get_current_user
from frigate.api.media import _build_transcode_cmd
from frigate.config import FrigateConfig
from frigate.models import Recordings
from frigate.test.http_api.base_http_test import AuthTestClient, BaseTestHttp


class TestBuildTranscodeCmd(unittest.TestCase):
    """Test _build_transcode_cmd selects correct hwaccel pipeline."""

    def _make_config(self, hwaccel_args=""):
        config = MagicMock(spec=FrigateConfig)
        config.ffmpeg.ffmpeg_path = "/usr/bin/ffmpeg"
        config.ffmpeg.hwaccel_args = hwaccel_args
        return config

    def test_software_fallback(self):
        config = self._make_config("")
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        assert "libx264" in cmd
        assert "-hwaccel" not in cmd
        assert "/input.mp4" in cmd
        assert "/output.mp4" in cmd

    def test_qsv_preset(self):
        config = self._make_config("preset-intel-qsv-h264")
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        assert "qsv" in cmd
        assert "h264_qsv" in cmd
        assert "scale_qsv=w=854:h=480" in cmd

    def test_vaapi_preset(self):
        config = self._make_config("preset-vaapi")
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        assert "vaapi" in cmd
        assert "h264_vaapi" in cmd
        assert "scale_vaapi=w=854:h=480" in cmd

    def test_nvidia_preset(self):
        config = self._make_config("preset-nvidia-h264")
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        assert "cuda" in cmd
        assert "h264_nvenc" in cmd
        assert "scale_cuda=w=854:h=480" in cmd

    def test_unknown_preset_falls_back_to_software(self):
        config = self._make_config("preset-rpi-64-h264")
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        assert "libx264" in cmd

    def test_non_string_hwaccel_args_falls_back(self):
        config = self._make_config(["-hwaccel", "custom"])
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        assert "libx264" in cmd

    def test_movflags_faststart(self):
        config = self._make_config("")
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        assert "+faststart" in cmd

    def test_audio_copy(self):
        config = self._make_config("")
        cmd = _build_transcode_cmd(config, "/input.mp4", "/output.mp4")
        idx = cmd.index("-c:a")
        assert cmd[idx + 1] == "copy"


class TestTranscodeEndpoint(BaseTestHttp):
    """Test the /vod/transcode endpoint path validation and fallback."""

    def setUp(self):
        super().setUp([Recordings])
        self.app = super().create_app()

        async def mock_get_current_user(request: Request):
            return {
                "username": request.headers.get("remote-user"),
                "role": request.headers.get("remote-role"),
            }

        async def mock_get_allowed_cameras_for_filter(request: Request):
            return ["front_door"]

        self.app.dependency_overrides[get_current_user] = mock_get_current_user
        self.app.dependency_overrides[get_allowed_cameras_for_filter] = (
            mock_get_allowed_cameras_for_filter
        )

    def tearDown(self):
        self.app.dependency_overrides.clear()
        super().tearDown()

    def test_rejects_path_traversal(self):
        """Path traversal attempts should be rejected with 403."""
        with AuthTestClient(self.app) as client:
            response = client.get(
                "/vod/transcode",
                params={"file": "/etc/passwd"},
            )
            assert response.status_code == 403

    def test_rejects_relative_path_traversal(self):
        """Relative path traversal should be rejected."""
        with AuthTestClient(self.app) as client:
            response = client.get(
                "/vod/transcode",
                params={"file": "/media/frigate/recordings/../../etc/shadow"},
            )
            assert response.status_code == 403

    def test_rejects_nonexistent_file(self):
        """Valid directory but missing file returns 404."""
        with AuthTestClient(self.app) as client:
            response = client.get(
                "/vod/transcode",
                params={"file": "/media/frigate/recordings/nonexistent.mp4"},
            )
            assert response.status_code == 404


class TestVodTranscodeParam(BaseTestHttp):
    """Test that the transcode query parameter controls VOD manifest format."""

    def setUp(self):
        super().setUp([Recordings])
        self.app = super().create_app()

        async def mock_get_current_user(request: Request):
            return {
                "username": request.headers.get("remote-user"),
                "role": request.headers.get("remote-role"),
            }

        async def mock_get_allowed_cameras_for_filter(request: Request):
            return ["front_door"]

        self.app.dependency_overrides[get_current_user] = mock_get_current_user
        self.app.dependency_overrides[get_allowed_cameras_for_filter] = (
            mock_get_allowed_cameras_for_filter
        )

        # Insert a recording
        now = 1700000000.0
        Recordings.insert(
            id="rec1",
            path="/media/frigate/recordings/2024-01-01/front_door/01.00.mp4",
            camera="front_door",
            start_time=now,
            end_time=now + 10,
            duration=10,
            motion=0,
        ).execute()
        self.start_ts = now
        self.end_ts = now + 10

    def tearDown(self):
        self.app.dependency_overrides.clear()
        super().tearDown()

    def test_vod_default_no_transcode(self):
        """Without transcode param, VOD uses filesystem source type."""
        with AuthTestClient(self.app) as client:
            response = client.get(
                f"/vod/front_door/start/{self.start_ts}/end/{self.end_ts}/index.m3u8"
            )
            assert response.status_code == 200
            data = response.json()
            clip = data["sequences"][0]["clips"][0]
            assert "sourceType" not in clip
            assert clip["path"] == "/media/frigate/recordings/2024-01-01/front_door/01.00.mp4"

    def test_vod_with_transcode(self):
        """With transcode=true, VOD uses http source type with encoded path."""
        with AuthTestClient(self.app) as client:
            response = client.get(
                f"/vod/front_door/start/{self.start_ts}/end/{self.end_ts}/index.m3u8",
                params={"transcode": "true"},
            )
            assert response.status_code == 200
            data = response.json()
            clip = data["sequences"][0]["clips"][0]
            assert clip["sourceType"] == "http"
            # Path should be URL-encoded
            assert clip["path"].startswith("/")
            assert "front_door" not in clip["path"] or "%2F" in clip["path"]
