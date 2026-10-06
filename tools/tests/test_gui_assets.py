import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
import server


class GuiAssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_versions_every_asset_and_is_not_cached(self):
        response = await server.index()
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        urls = re.findall(r'/static/([^" ]+)', response.body.decode())
        self.assertEqual(len(urls), 3)
        for name in ("app.js", "plots.js", "style.css"):
            self.assertTrue(any(re.fullmatch(re.escape(name) + r"\?v=[0-9a-f]{16}",
                                            url) for url in urls))

    async def test_updated_script_gets_new_url_without_server_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                '<script src="/static/app.js"></script>'
                '<script src="/static/plots.js"></script>'
                '<link href="/static/style.css">', encoding="utf-8")
            for name in ("app.js", "plots.js", "style.css"):
                (root / name).write_text("old", encoding="utf-8")
            with patch.object(server, "STATIC_DIR", root):
                before = (await server.index()).body
                (root / "plots.js").write_text("updated", encoding="utf-8")
                after = (await server.index()).body
            self.assertNotEqual(before, after)
            old_app = re.search(rb'app.js\?v=([0-9a-f]+)', before).group(1)
            new_app = re.search(rb'app.js\?v=([0-9a-f]+)', after).group(1)
            self.assertEqual(old_app, new_app)


if __name__ == "__main__":
    unittest.main()
