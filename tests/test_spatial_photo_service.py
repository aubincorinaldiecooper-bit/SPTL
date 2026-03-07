import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.spatial_photo_service import SpatialPhotoConversionError, SpatialPhotoService

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None
PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None

if FASTAPI_AVAILABLE:
    from fastapi.testclient import TestClient
    from app.main import app, service

if PIL_AVAILABLE:
    from PIL import Image


class SpatialPhotoServiceTests(unittest.TestCase):
    def test_convert_image_returns_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "output"
            image = Path(tmp_dir) / "image.jpg"
            image.write_bytes(b"fake-image")

            svc = SpatialPhotoService("ml-sharp", output_root=output_root)

            def fake_run(command, capture_output, text, check):
                self.assertIn("--input", command)
                self.assertIn("--output", command)
                job_dir = Path(command[command.index("--output") + 1])
                (job_dir / "photo.usdz").write_bytes(b"spatial")

                class Completed:
                    returncode = 0
                    stdout = "done"
                    stderr = ""

                return Completed()

            with patch("subprocess.run", side_effect=fake_run):
                result = svc.convert_image(image, user_id="abc")

            self.assertEqual("abc", result.metadata["user_id"])
            self.assertEqual(1, len(result.artifacts))
            self.assertEqual("photo.usdz", result.artifacts[0].name)

    def test_convert_image_raises_on_ml_sharp_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "output"
            image = Path(tmp_dir) / "image.jpg"
            image.write_bytes(b"fake-image")

            svc = SpatialPhotoService("ml-sharp", output_root=output_root)

            class Completed:
                returncode = 1
                stdout = ""
                stderr = "boom"

            with patch("subprocess.run", return_value=Completed()):
                with self.assertRaises(SpatialPhotoConversionError):
                    svc.convert_image(image)

    def test_sanitize_filename_removes_path_traversal(self) -> None:
        svc = SpatialPhotoService("ml-sharp", output_root=Path(tempfile.mkdtemp()))
        sanitized = svc._sanitize_filename("../../etc/passwd")
        self.assertNotIn("..", sanitized)
        self.assertNotIn("/", sanitized)
        self.assertTrue(sanitized)


@unittest.skipUnless(FASTAPI_AVAILABLE and PIL_AVAILABLE, "FastAPI and Pillow are required")
class SpatialPhotoApiValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @staticmethod
    def _make_image_bytes(width: int, height: int, fmt: str = "JPEG") -> bytes:
        buf = io.BytesIO()
        img = Image.new("RGB", (width, height), color=(123, 20, 220))
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_valid_jpeg_upload_succeeds(self) -> None:
        data = self._make_image_bytes(512, 512, fmt="JPEG")
        with patch("app.main.service.process_job", return_value=None):
            response = self.client.post(
                "/api/spatial-photos",
                files={"image": ("photo.jpg", data, "image/jpeg")},
            )
        self.assertEqual(202, response.status_code)
        body = response.json()
        self.assertEqual("pending", body["status"])
        self.assertIn("job_id", body)

    def test_file_exceeding_20mb_rejected_with_413(self) -> None:
        huge_payload = b"\xff\xd8\xff" + b"0" * (20 * 1024 * 1024 + 1)
        response = self.client.post(
            "/api/spatial-photos",
            files={"image": ("huge.jpg", huge_payload, "image/jpeg")},
        )
        self.assertEqual(413, response.status_code)
        self.assertEqual("File too large. Maximum size is 20MB.", response.json()["error"])

    def test_disguised_txt_rejected_with_415(self) -> None:
        response = self.client.post(
            "/api/spatial-photos",
            files={"image": ("fake.jpg", b"not-an-image", "image/jpeg")},
        )
        self.assertEqual(415, response.status_code)
        self.assertEqual(
            "Unsupported file type. Accepted: JPEG, PNG, WebP, HEIC/HEIF",
            response.json()["error"],
        )

    def test_image_below_minimum_size_rejected_with_422(self) -> None:
        data = self._make_image_bytes(255, 400, fmt="JPEG")
        response = self.client.post(
            "/api/spatial-photos",
            files={"image": ("small.jpg", data, "image/jpeg")},
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("Image too small. Minimum size is 256x256 pixels.", response.json()["error"])

    def test_image_above_maximum_size_rejected_with_422(self) -> None:
        data = self._make_image_bytes(4097, 512, fmt="JPEG")
        response = self.client.post(
            "/api/spatial-photos",
            files={"image": ("large.jpg", data, "image/jpeg")},
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("Image too large. Maximum size is 4096x4096 pixels.", response.json()["error"])

    def test_filename_path_traversal_is_sanitized(self) -> None:
        data = self._make_image_bytes(512, 512, fmt="JPEG")
        captured = {}

        original = service.validate_and_stage_upload

        def wrapped(filename, image_bytes, content_length):
            staged_path = original(filename, image_bytes, content_length)
            captured["name"] = staged_path.name
            return staged_path

        with patch("app.main.service.validate_and_stage_upload", side_effect=wrapped), patch(
            "app.main.service.process_job", return_value=None
        ):
            response = self.client.post(
                "/api/spatial-photos",
                files={"image": ("../../etc/passwd", data, "image/jpeg")},
            )

        self.assertEqual(202, response.status_code)
        self.assertIn("name", captured)
        self.assertNotIn("..", captured["name"])
        self.assertNotIn("/", captured["name"])


if __name__ == "__main__":
    unittest.main()
