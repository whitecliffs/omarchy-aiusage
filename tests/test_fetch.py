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

    def test_opencode_retries_with_current_private_server(self):
        unavailable = SimpleNamespace(returncode=0, stdout="HTTP 404 Not Found")
        live = SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"go": {"monthly": {"percent": 53}}}}))
        with patch.object(usage.subprocess, "run", side_effect=[unavailable, live]) as run:
            result = usage.opencode({})
        self.assertEqual(result["pct"], 53)
        self.assertIn("--standalone", run.call_args.args[0])
        self.assertFalse(result["stale"])

    def test_claude_renews_expired_login_using_native_cli(self):
        old = {"accessToken": "expired", "refreshToken": "fixture-refresh", "scopes": ["user:profile"], "expiresAt": 1}
        new = {**old, "accessToken": "renewed", "expiresAt": (usage.now() + 3600) * 1000}
        path = Path(self.directory.name) / ".credentials.json"
        path.write_text(json.dumps({"claudeAiOauth": old, "other": "preserved"}))
        def renew(*args, **kwargs):
            self.assertEqual(args[0], ["claude", "auth", "login", "--claudeai"])
            self.assertEqual(kwargs["env"]["CLAUDE_CODE_OAUTH_REFRESH_TOKEN"], "fixture-refresh")
            path.write_text(json.dumps({"claudeAiOauth": new, "other": "preserved"}))
            return SimpleNamespace(returncode=0)
        with patch.object(usage, "_claude_credentials_path", return_value=str(path)), patch.object(usage.subprocess, "run", side_effect=renew) as run:
            self.assertEqual(usage._claude_refresh_oauth(old), new)
            self.assertEqual(usage._claude_refresh_oauth(old), new)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(path.read_text())["other"], "preserved")

    def test_claude_retries_server_rejected_token(self):
        credentials = {"claudeAiOauth": {"accessToken": "revoked", "refreshToken": "fixture"}}
        data = {"five_hour": {"utilization": 12}}
        with patch.object(usage, "read_json", return_value=credentials), patch.object(usage, "_claude_refresh_oauth", return_value={"accessToken": "renewed"}) as renew, patch.object(usage, "http_json", side_effect=[(401, {}), (200, data)]):
            result = usage.claude({})
        self.assertEqual(result["pct"], 12)
        self.assertTrue(renew.call_args.kwargs["force"])

    def test_antigravity_keeps_argon_separate(self):
        groups = [{"displayName": name, "buckets": [{"window": "weekly", "remainingFraction": remaining}]}
                  for name, remaining in [("Gemini Models", .75), ("Claude and GPT models", .74), ("Gemini 4 Argon", .18)]]
        windows = usage._ag_parse_quota_groups(groups)
        self.assertEqual(len({w["label"] for w in windows}), 3)
        self.assertEqual(windows[2]["label"], "Gemini 4 Argon (Weekly)")
        self.assertEqual(usage.worst(windows), 82)

    def test_antigravity_pairs_windows_by_model_family(self):
        groups = [{"displayName": name, "buckets": [
            {"window": "weekly", "remainingFraction": weekly},
            {"window": "5h", "remainingFraction": session}]}
            for name, weekly, session in [("Gemini Models", .75, .92), ("Claude and GPT models", .74, 1)]]
        windows = usage._ag_parse_quota_groups(groups)
        self.assertEqual([w["label"] for w in windows], [
            "Gemini (5h)", "Gemini (Weekly)", "Claude/GPT (5h)", "Claude/GPT (Weekly)"])
        self.assertEqual(usage.worst(windows), 26)

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

    def test_conductor_reads_active_session_when_saved_login_expired(self):
        with patch.object(usage, "_conductor_token", return_value="expired"), patch.object(usage, "http_json", return_value=(401, {"error": "Invalid or expired token"})), patch.object(usage, "_conductor_active_account", return_value=(200, {"user": {"credits": 380.5}})):
            result = usage.conductor({})
        self.assertEqual(result["headline"], "380.50 cr")
        self.assertFalse(result["stale"])

    def test_conductor_reports_expired_saved_and_active_logins(self):
        with patch.object(usage, "_conductor_token", return_value="expired"), patch.object(usage, "http_json", return_value=(401, {})), patch.object(usage, "_conductor_active_account", return_value=(401, {})):
            result = usage.conductor({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "AI Junction login expired")

    def test_each_provider_can_reuse_a_fresh_reading(self):
        cached = usage.result(headline="10%", pct=10, quotaUserAgent=usage.ANTIGRAVITY_USER_AGENT)
        for name in ["claude", "openai", "opencode", "openrouter", "antigravity", "conductor"]:
            with self.subTest(provider=name), patch.object(usage, "read_json", return_value=cached), patch.object(usage, "http_json") as request:
                self.assertEqual(usage.PROVIDERS[name]({}), cached)
                request.assert_not_called()

    def test_manual_refresh_bypasses_recent_reading(self):
        cached = usage.result(headline="10.00 cr")
        with patch.object(usage, "read_json", return_value=cached), patch.object(usage, "_conductor_token", return_value="fixture"), patch.object(usage, "http_json", return_value=(200, {"user": {"credits": 9}})) as request:
            self.assertEqual(usage.conductor({})["headline"], "10.00 cr")
            request.assert_not_called()
            self.assertEqual(usage.conductor({"forceRefresh": True})["headline"], "9.00 cr")


if __name__ == "__main__":
    unittest.main()
