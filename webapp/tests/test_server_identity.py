"""Multiple checkouts must never reuse each other's workspace service."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp.server import existing_service, workspace_identity


class ServerIdentityTests(unittest.TestCase):
    def test_another_workspace_is_not_reused(self):
        with tempfile.TemporaryDirectory() as root:
            wrong = json.dumps({"app": "tradingagents-workspace", "workspace_id": "other"}).encode()
            with (
                patch("webapp.server.DATA", Path(root)),
                patch(
                    "webapp.server.urllib.request.urlopen",
                    side_effect=lambda *a, **k: io.BytesIO(wrong),
                ),
            ):
                self.assertIsNone(existing_service(18000, 1))

    def test_matching_workspace_is_reused(self):
        with tempfile.TemporaryDirectory() as root:
            health = json.dumps(
                {"app": "tradingagents-workspace", "workspace_id": workspace_identity(root)}
            ).encode()
            with (
                patch("webapp.server.DATA", Path(root)),
                patch(
                    "webapp.server.urllib.request.urlopen",
                    side_effect=lambda *a, **k: io.BytesIO(health),
                ),
            ):
                self.assertEqual(existing_service(18000, 1), "http://127.0.0.1:18000")
