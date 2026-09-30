import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from agent_common import MAX_IMAGES, MAX_PIXELS
from twain_worker import FrameWriter, mock_job, twain_job


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def bmp(self):
        path = self.directory / "capture.bmp"
        with Image.new("RGB", (64, 48), "white") as image:
            image.save(path)
        return path

    def test_no_downsampling_and_no_filename_traversal(self):
        writer = FrameWriter(self.directory, {"jpegQuality": 92, "filePrefix": "../../patient"})
        writer.add(self.bmp())
        with Image.open(self.directory / "000.jpg") as image:
            self.assertEqual(image.size, (64, 48))
        self.assertNotIn("/", writer.frames[0]["name"])
        self.assertNotIn("\\", writer.frames[0]["name"])

    def test_frame_count_limit(self):
        writer = FrameWriter(self.directory, {"jpegQuality": 92})
        writer.frames = [{}] * MAX_IMAGES
        with self.assertRaisesRegex(ValueError, "IMAGE_LIMIT"):
            writer.add(self.bmp())

    def test_pixel_limit_checked_before_decode(self):
        writer = FrameWriter(self.directory, {"jpegQuality": 92})
        bmp = self.bmp()
        with patch("twain_worker.MAX_PIXELS", 10):
            with self.assertRaisesRegex(ValueError, "IMAGE_LIMIT"):
                writer.add(bmp)
        self.assertFalse((self.directory / "000.jpg").exists())

    def test_byte_limit_removes_oversized_jpeg(self):
        writer = FrameWriter(self.directory, {"jpegQuality": 92})
        with patch("twain_worker.MAX_IMAGE", 1):
            with self.assertRaisesRegex(ValueError, "IMAGE_LIMIT"):
                writer.add(self.bmp())
        self.assertFalse((self.directory / "000.jpg").exists())

    def test_total_byte_limit(self):
        writer = FrameWriter(self.directory, {"jpegQuality": 92})
        writer.total = 50 * 1024 * 1024
        with self.assertRaisesRegex(ValueError, "IMAGE_LIMIT"):
            writer.add(self.bmp())

    def test_bad_local_folder_does_not_lose_web_image(self):
        writer = FrameWriter(self.directory, {"jpegQuality": 92, "localFolder": str(self.directory / "missing")})
        writer.add(self.bmp())
        self.assertTrue(writer.result()["warning"])
        self.assertTrue((self.directory / "000.jpg").exists())

    def test_local_copy_names_do_not_overwrite(self):
        folder = self.directory / "copies"
        folder.mkdir()
        for index in range(2):
            work = self.directory / str(index)
            work.mkdir()
            mock_job("capture", work, {"jpegQuality": 92, "localFolder": str(folder), "filePrefix": "same"})
        self.assertEqual(len(list(folder.glob("*.jpg"))), 2)

    def twain(self, huge=False):
        testcase = self
        class Root:
            def withdraw(self): pass
            def destroy(self): pass
        class TwainImage:
            closed = False
            def save(self, path):
                with Image.new("RGB", (64, 48), "white") as image:
                    image.save(path)
            def close(self): self.closed = True
        frame = TwainImage()
        class Source:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def acquire_natively(self, *, after, before, show_ui, modal):
                testcase.assertTrue(show_ui)
                testcase.assertTrue(modal)
                before({"ImageWidth": MAX_PIXELS + 1 if huge else 64, "ImageLength": 48})
                after(frame, 0)
        class Manager:
            source_list = ["Exact Driver"]
            def __init__(self, root, *, ProductName, Manufacturer): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def open_source(self, name):
                testcase.assertEqual(name, "Exact Driver")
                return Source()
        return {"tkinter": types.SimpleNamespace(Tk=Root), "twain": types.SimpleNamespace(SourceManager=Manager)}, frame

    def test_current_twain_callback_api_and_native_handle_closed(self):
        modules, frame = self.twain()
        with patch.dict("sys.modules", modules):
            result = twain_job("capture", self.directory, {"sourceName": "Exact Driver", "jpegQuality": 92, "showUi": True})
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["images"]), 1)
        self.assertTrue(frame.closed)

    def test_twain_sources_are_enumerated_separately(self):
        modules, _ = self.twain()
        with patch.dict("sys.modules", modules):
            self.assertEqual(twain_job("sources", self.directory, {})["sources"], ["Exact Driver"])

    def test_twain_preflight_rejects_huge_dimensions_before_transfer(self):
        modules, frame = self.twain(huge=True)
        with patch.dict("sys.modules", modules):
            with self.assertRaisesRegex(ValueError, "IMAGE_LIMIT"):
                twain_job("capture", self.directory, {"sourceName": "Exact Driver", "jpegQuality": 92, "showUi": True})
        self.assertFalse((self.directory / "000.jpg").exists())


if __name__ == "__main__":
    unittest.main()
