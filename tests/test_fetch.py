import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("usage", Path(__file__).resolve().parents[1] / "fetch.py")
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = patch.object(usage, "CACHE", self.directory.name)
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def test_claude_null_model_scopes(self):
        credentials = {"claudeAiOauth": {"accessToken": "fixture"}}
        data = {"five_hour": {"utilization": 5}, "seven_day": {"utilization": 99},
                "limits": [{"scope": None}, {"scope": {"model": None}}]}
        with patch.object(usage, "read_json", return_value=credentials), patch.object(usage, "http_json", return_value=(200, data)):
            result = usage.claude({})
        self.assertTrue(result["ok"])
        self.assertEqual(result["pct"], 99)

    def test_opencode_account_limits_and_reset_units(self):
        response = {"data": {"go": {"monthly": {"percent": 51, "resetsAt": 1794996338000}}}}
        process = SimpleNamespace(returncode=0, stdout=json.dumps(response))
        with patch.object(usage.subprocess, "run", return_value=process):
            result = usage.opencode({})
        self.assertEqual(result["pct"], 51)
        self.assertEqual(result["windows"][0]["resetsAt"], 1794996338)

    def test_antigravity_keeps_argon_separate(self):
        groups = [{"displayName": name, "buckets": [{"window": "weekly", "remainingFraction": remaining}]}
                  for name, remaining in [("Gemini Models", .75), ("Claude and GPT models", .74), ("Gemini 4 Argon", .18)]]
        windows = usage._ag_parse_quota_groups(groups)
        self.assertEqual(len({w["label"] for w in windows}), 3)
        self.assertEqual(windows[0]["label"], "Gemini 4 Argon (Weekly)")
        self.assertEqual(usage.worst(windows), 82)

    def test_missing_quota_is_unknown(self):
        windows = usage._ag_parse_quota_groups([{"displayName": "Gemini Models", "buckets": [{"window": "5h"}]}])
        self.assertEqual(windows[0]["pct"], -1)

    def test_antigravity_discovers_matching_native_client(self):
        responses = [(401, {"error": "invalid_client"}),
                     (200, {"access_token": "fixture-access", "expires_in": 3600})]
        with patch.object(usage, "_ag_oauth_clients", return_value=[("other", "other"), ("fixture", "fixture")]), patch.object(usage, "http_json", side_effect=responses) as request:
            token = usage._ag_refresh_token("fixture-refresh")
            self.assertEqual(token, "fixture-access")
            self.assertEqual(request.call_count, 2)
            self.assertEqual(usage._ag_refresh_token("fixture-refresh"), token)
            self.assertEqual(request.call_count, 2)
        path = Path(self.directory.name) / "antigravity-token.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_conductor_zero_and_stale_balance(self):
        with patch.object(usage, "_conductor_token", return_value="fixture"):
            with patch.object(usage, "http_json", return_value=(200, {"user": {"credits": 0}})):
                result = usage.conductor({})
            self.assertEqual(result["headline"], "0.00 cr")
            with patch.object(usage, "now", return_value=result["fetchedAt"] + 120), patch.object(usage, "http_json", return_value=(503, None)):
                result = usage.conductor({})
            self.assertTrue(result["stale"])
            self.assertEqual(result["headline"], "0.00 cr")


if __name__ == "__main__":
    unittest.main()
