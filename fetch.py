#!/usr/bin/env python3
"""Fetch live usage for one AI subscription and print ONE normalized JSON object.

    fetch.py detect
    fetch.py <claude|openai|opencode|antigravity|conductor|openrouter> [json-options]

Always exits 0 with JSON so the panel can render failures as state.

Result shape:
  ok        bool
  error     str      human-readable failure (empty when ok)
  hint      str      what to do about it
  plan      str      plan / tier label
  headline  str      short text for the bar ("98%", "$12.40")
  pct       number   0-100 of the most-constrained window used, or -1
  windows   [ {label, pct (0-100 used, -1 if n/a), resetsAt (epoch s, 0 if n/a), detail} ]
  rows      [ {label, value} ]    plain key/value lines
  sections  [ {title, windows, rows} ]   optional grouping (OpenCode Go / Zen)
  stale     bool     data came from the cache, not a live call
  fetchedAt epoch s

Security: tokens are read from the tools' own credential files, sent only to
that tool's own API, and never printed or logged.
"""
import base64
import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = os.path.expanduser("~")
CONFIG = os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config"))
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(HOME, ".cache")), "omarchy-aiusage")
SYS_AGENT_CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(HOME, ".cache")), "omarchy", "agent-usage")
TIMEOUT = 12

ANTIGRAVITY_API_HOST = "https://daily-cloudcode-pa.googleapis.com"
# Older/IDE user agents silently omit Gemini and Argon quota groups.
ANTIGRAVITY_USER_AGENT = "antigravity/cli/1.4.12"


def now():
    return int(time.time())


def result(**kw):
    base = {"ok": True, "error": "", "hint": "", "plan": "", "headline": "", "pct": -1,
            "windows": [], "rows": [], "sections": [], "stale": False, "fetchedAt": now()}
    base.update(kw)
    return base


def fail(error, hint=""):
    return result(ok=False, error=error, hint=hint)


def window(label, pct, resets_at=0, detail=""):
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = -1
    return {"label": label, "pct": round(pct, 1) if pct >= 0 else -1,
            "resetsAt": int(resets_at or 0), "detail": detail}


def worst(windows):
    vals = [w["pct"] for w in windows if w.get("pct", -1) >= 0]
    return max(vals) if vals else -1


def iso_to_epoch(s):
    if not s:
        return 0
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def http_json(url, headers=None, body=None, ctx=None):
    """Returns (status, parsed-json-or-None). Never raises."""
    req = urllib.request.Request(url, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception:
        return 0, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def err_for(status, name):
    if status == 0:
        return "Could not reach " + name
    if status in (401, 403):
        return name + " rejected the saved login"
    if status == 429:
        return "Rate limited by " + name
    return "%s returned HTTP %s" % (name, status)


# --------------------------------------------------------------------- Claude
def claude(_opts):
    os.makedirs(CACHE, exist_ok=True)
    cache_file = os.path.join(CACHE, "claude.json")
    sys_cache_file = os.path.join(SYS_AGENT_CACHE, "claude-limits.json")

    # If recent fresh cache exists (< 60s), reuse it to avoid Anthropic rate limits
    cached = read_json(cache_file)
    if cached and isinstance(cached, dict) and cached.get("ok") and not cached.get("stale"):
        if now() - cached.get("fetchedAt", 0) < 60:
            return cached

    cred = read_json(os.path.join(HOME, ".claude", ".credentials.json"))
    oauth = (cred or {}).get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    plan = str(oauth.get("subscriptionType") or "").capitalize()

    if not token:
        if cached and cached.get("windows"):
            cached["stale"] = True
            cached["hint"] = "Not signed in to Claude Code - showing last reading."
            return cached
        return fail("Not signed in to Claude Code", "Run `claude` and sign in with your Claude subscription.")

    exp = oauth.get("expiresAt") or 0
    if exp and exp / 1000 < now():
        if cached and cached.get("windows"):
            cached["stale"] = True
            cached["hint"] = "Claude login token has expired; start Claude Code once to refresh."
            return cached
        return fail("Claude login token has expired",
                    "Open Claude Code once (`claude`) - it refreshes the token itself.")

    status, data = http_json("https://api.anthropic.com/api/oauth/usage", {
        "Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.0",
    })

    if status == 200 and isinstance(data, dict):
        wins = []
        weekly_bucket = data.get("seven_day_oauth_apps") or data.get("seven_day")
        if isinstance(data.get("five_hour"), dict) and data["five_hour"].get("utilization") is not None:
            wins.append(window("Session (5h)", data["five_hour"]["utilization"],
                               iso_to_epoch(data["five_hour"].get("resets_at"))))
        if isinstance(weekly_bucket, dict) and weekly_bucket.get("utilization") is not None:
            wins.append(window("Weekly (7d)", weekly_bucket["utilization"],
                               iso_to_epoch(weekly_bucket.get("resets_at"))))
        for key, label in (("seven_day_opus", "Weekly Opus"), ("seven_day_sonnet", "Weekly Sonnet")):
            w = data.get(key)
            if isinstance(w, dict) and w.get("utilization") is not None:
                wins.append(window(label, w["utilization"], iso_to_epoch(w.get("resets_at"))))

        # Model-scoped limits
        for entry in (data.get("limits") or []):
            if not isinstance(entry, dict):
                continue
            scope = entry.get("scope") or {}
            model = (scope.get("model") or {}) if isinstance(scope, dict) else {}
            name = str(model.get("display_name") or model.get("id") or "").strip()
            kind = str(entry.get("kind") or "").strip()
            if not name:
                continue
            w_title = name
            if "week" in kind.lower() or "day" in kind.lower():
                w_title += " (7d)"
            elif "hour" in kind.lower() or "session" in kind.lower():
                w_title += " (5h)"
            pct_val = entry.get("percent")
            if pct_val is not None:
                wins.append(window(w_title, pct_val, iso_to_epoch(entry.get("resets_at"))))

        rows = []
        extra = data.get("extra_usage")
        if isinstance(extra, dict) and extra.get("is_enabled"):
            used, lim = extra.get("used_credits"), extra.get("monthly_limit")
            dec = extra.get("decimal_places") or 2
            cur = extra.get("currency") or "USD"
            if used is not None:
                val = "%.2f" % (used / (10 ** dec)) if isinstance(used, (int, float)) else str(used)
                if lim:
                    val += " / %.2f" % (lim / (10 ** dec))
                rows.append({"label": "Extra usage (%s)" % cur, "value": val})

        res = result(plan=plan, windows=wins, rows=rows, pct=worst(wins),
                     headline=("%d%%" % round(worst(wins))) if wins else "")
        write_json(cache_file, res)
        return res

    # Status != 200: Check fallbacks (rate limited, network issue, etc.)
    if cached and cached.get("windows"):
        cached["stale"] = True
        if status == 429:
            cached["hint"] = "Anthropic rate limits usage checks; showing last reading."
        else:
            cached["hint"] = f"{err_for(status, 'Anthropic')}; showing last reading."
        return cached

    sys_cached = read_json(sys_cache_file)
    if sys_cached and isinstance(sys_cached.get("limits"), list) and sys_cached["limits"]:
        wins = []
        for lim in sys_cached["limits"]:
            if isinstance(lim, dict):
                pct_val = lim.get("percent")
                if pct_val is not None:
                    pct_norm = pct_val * 100 if pct_val <= 1.0 else pct_val
                    wins.append(window(str(lim.get("label") or "Usage"), pct_norm, iso_to_epoch(lim.get("resetsAt"))))
        if wins:
            hint = "Anthropic rate limits usage checks; showing last reading." if status == 429 else err_for(status, "Anthropic")
            res = result(plan=plan, windows=wins, rows=[], pct=worst(wins),
                         headline=("%d%%" % round(worst(wins))), stale=True, hint=hint)
            write_json(cache_file, res)
            return res

    return fail(err_for(status, "Anthropic"),
                "Anthropic rate limits usage checks; will update automatically." if status == 429 else (
                    "Open Claude Code once to refresh the login." if status in (401, 403) else ""))


# --------------------------------------------------------------------- OpenAI
def openai(_opts):
    os.makedirs(CACHE, exist_ok=True)
    cache_file = os.path.join(CACHE, "openai.json")

    cached = read_json(cache_file)
    if cached and isinstance(cached, dict) and cached.get("ok") and not cached.get("stale"):
        if now() - cached.get("fetchedAt", 0) < 60:
            return cached

    auth = read_json(os.path.join(HOME, ".codex", "auth.json"))
    toks = (auth or {}).get("tokens") or {}
    token, acct = toks.get("access_token"), toks.get("account_id")
    if not token:
        if cached and cached.get("windows"):
            cached["stale"] = True
            cached["hint"] = "Not signed in to Codex - showing last reading."
            return cached
        return fail("Not signed in to Codex", "Run `codex login` with your ChatGPT account.")

    # JWT exp check (no signature work needed)
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
        if exp and exp < now():
            if cached and cached.get("windows"):
                cached["stale"] = True
                cached["hint"] = "ChatGPT login token has expired; run `codex` once to refresh."
                return cached
            return fail("ChatGPT login token has expired",
                        "Run `codex` once - it refreshes the token itself.")
    except Exception:
        pass

    headers = {"Authorization": "Bearer " + token, "User-Agent": "codex-cli"}
    if acct:
        headers["chatgpt-account-id"] = acct
    status, data = http_json("https://chatgpt.com/backend-api/wham/usage", headers)
    if status != 200 or not isinstance(data, dict):
        if cached and cached.get("windows"):
            cached["stale"] = True
            cached["hint"] = f"{err_for(status, 'OpenAI')}; showing last reading."
            return cached
        return fail(err_for(status, "OpenAI"), "Run `codex` once to refresh the login." if status in (401, 403) else "")

    rl = data.get("rate_limit") or {}
    wins = []
    for key, label in (("primary_window", "Session"), ("secondary_window", "Weekly")):
        w = rl.get(key)
        if isinstance(w, dict) and w.get("used_percent") is not None:
            secs = w.get("limit_window_seconds") or 0
            if secs and secs < 86400:
                label = "Session (%dh)" % round(secs / 3600)
            elif secs:
                label = "Weekly (%dd)" % round(secs / 86400)
            wins.append(window(label, w["used_percent"], w.get("reset_at") or 0))
    rows = []
    cr = data.get("credits") or {}
    if cr.get("has_credits") or (cr.get("balance") not in (None, "0", 0)):
        rows.append({"label": "Credits balance", "value": str(cr.get("balance"))})
    if rl.get("limit_reached"):
        rows.append({"label": "Status", "value": "Limit reached"})
    plan = str(data.get("plan_type") or "").capitalize()
    res = result(plan=plan, windows=wins, rows=rows, pct=worst(wins),
                 headline=("%d%%" % round(worst(wins))) if wins else "")
    write_json(cache_file, res)
    return res


# ------------------------------------------------------------------- OpenCode
def opencode(_opts):
    cache_file = os.path.join(CACHE, "opencode.json")
    cached = read_json(cache_file)
    if cached and cached.get("ok") and not cached.get("stale") and now() - cached.get("fetchedAt", 0) < 60:
        return cached
    try:
        proc = subprocess.run(
            ["opencode", "api", "get", "/api/experimental/usage/limits"],
            capture_output=True, text=True, timeout=30)
        payload = json.loads(proc.stdout) if proc.returncode == 0 else {}
        data = payload.get("data") or {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        data = {}

    wins = []
    go = data.get("go") or {}
    for key, label in (("rolling", "5-hour"), ("weekly", "Weekly"), ("monthly", "Monthly")):
        bucket = go.get(key)
        if not isinstance(bucket, dict) or bucket.get("percent") is None:
            continue
        reset = bucket.get("resetsAt") or 0
        reset = int(reset / 1000) if isinstance(reset, (int, float)) else iso_to_epoch(reset)
        wins.append(window(label, bucket["percent"], reset))
    sections = []
    budget_wins = []
    for budget in data.get("budgets") or []:
        if isinstance(budget, dict) and budget.get("percent") is not None:
            reset = budget.get("resetsAt") or 0
            reset = int(reset / 1000) if isinstance(reset, (int, float)) else iso_to_epoch(reset)
            budget_wins.append(window(str(budget.get("name") or budget.get("label") or "Budget"),
                                      budget["percent"], reset))
    if budget_wins:
        sections.append({"title": "CONSOLE BUDGETS", "windows": budget_wins, "rows": []})
    if wins or budget_wins:
        pct = worst(wins + budget_wins)
        res = result(plan="OpenCode Go" if wins else "OpenCode Console", windows=wins,
                     sections=sections, pct=pct, headline="%d%%" % round(pct),
                     hint="Account-wide usage across all machines.")
        write_json(cache_file, res)
        return res
    if cached and cached.get("windows"):
        cached.update(stale=True, hint="OpenCode account usage unavailable; showing last reading.")
        return cached
    return fail("OpenCode account usage unavailable",
                "Sign in to OpenCode Go or Console in `opencode`; no local usage estimate is shown.")


# --------------------------------------------------------------- OpenRouter
def openrouter(opts):
    os.makedirs(CACHE, exist_ok=True)
    cache_file = os.path.join(CACHE, "openrouter.json")

    cached = read_json(cache_file)
    if cached and isinstance(cached, dict) and cached.get("ok") and not cached.get("stale"):
        if now() - cached.get("fetchedAt", 0) < 60:
            return cached

    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY") or ""
    cmd = str(opts.get("keyCommand") or "")
    if cmd:
        try:
            key = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=10).stdout.strip().split("\n")[0]
        except Exception:
            key = ""
    if not key:
        for p in (os.path.join(CONFIG, "omarchy", "openrouter", "key"), os.path.join(CONFIG, "openrouter", "key")):
            try:
                for line in open(p):
                    line = re.sub(r"\s*#.*$", "", line).strip()
                    if line:
                        key = line
                        break
            except OSError:
                continue
            if key:
                break
    key = re.sub(r"\s", "", key)
    if not key:
        if cached and cached.get("windows"):
            cached["stale"] = True
            return cached
        return fail("No OpenRouter API key found",
                    "Save it to ~/.config/omarchy/openrouter/key, or set OPENROUTER_API_KEY.")

    h = {"Authorization": "Bearer " + key, "User-Agent": "omarchy-aiusage/1.0"}
    s1, credits = http_json("https://openrouter.ai/api/v1/credits", h)
    s2, keyinfo = http_json("https://openrouter.ai/api/v1/key", h)
    c = (credits or {}).get("data") if isinstance(credits, dict) else None
    k = (keyinfo or {}).get("data") if isinstance(keyinfo, dict) else None
    if not c and not k:
        if cached and cached.get("windows"):
            cached["stale"] = True
            cached["hint"] = f"{err_for(s2 or s1, 'OpenRouter')}; showing last reading."
            return cached
        return fail(err_for(s2 or s1, "OpenRouter"), "Check the key at openrouter.ai/settings/keys.")

    rows, wins, headline, pct = [], [], "", -1
    if c and isinstance(c.get("total_credits"), (int, float)):
        total, used = c["total_credits"], c.get("total_usage") or 0
        rem = total - used
        headline = "$%.2f" % rem
        pct = (used / total * 100) if total > 0 else -1
        wins.append(window("Credits used", pct, 0, "$%.2f of $%.2f  ($%.2f left)" % (used, total, rem)))
    if k:
        lim, lrem = k.get("limit"), k.get("limit_remaining")
        if lim:
            wins.append(window("Key cap", (lim - (lrem or 0)) / lim * 100 if lrem is not None else -1,
                                0, "$%.2f of $%.2f" % ((lim - (lrem or 0)), lim)))
            if not headline and lrem is not None:
                headline = "$%.2f" % lrem
        for lab, f in (("Key spend today", "usage_daily"), ("Key spend this week", "usage_weekly"),
                       ("Key spend this month", "usage_monthly")):
            if isinstance(k.get(f), (int, float)):
                rows.append({"label": lab, "value": "$%.2f" % k[f]})
        if not headline and isinstance(k.get("usage"), (int, float)):
            headline = "$%.2f used" % k["usage"]
    low = float(opts.get("lowBalance", 5))
    if c and isinstance(c.get("total_credits"), (int, float)) and (c["total_credits"] - (c.get("total_usage") or 0)) <= low:
        pct = max(pct, 95)
    res = result(plan="Credits", windows=wins, rows=rows, pct=pct, headline=headline,
                 hint="" if c else "Account balance needs a management key; showing this key's own usage.")
    write_json(cache_file, res)
    return res


# ---------------------------------------------------------------- Antigravity
def _ag_oauth_clients(cached):
    client_id = os.environ.get("ANTIGRAVITY_CLIENT_ID")
    client_secret = os.environ.get("ANTIGRAVITY_CLIENT_SECRET")
    if client_id and client_secret:
        return [(client_id, client_secret)]
    if cached.get("clientId") and cached.get("clientSecret"):
        return [(cached["clientId"], cached["clientSecret"])]
    # Native-client OAuth configuration belongs to the installed CLI. Keep it
    # out of the plugin source and never print it. Scan in chunks because agy
    # is a large executable; overlaps cover strings crossing chunk boundaries.
    executable = shutil.which("agy")
    if not executable:
        return []
    ids, secrets = [], []
    try:
        with open(executable, "rb") as f:
            tail = b""
            while chunk := f.read(1024 * 1024):
                data = tail + chunk
                for value in re.findall(rb"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", data):
                    if value not in ids:
                        ids.append(value)
                for value in re.findall(rb"GOCSPX-[A-Za-z0-9_-]{28}", data):
                    if value not in secrets:
                        secrets.append(value)
                tail = data[-256:]
    except OSError:
        return []
    return [(client.decode("ascii"), secret.decode("ascii"))
            for client in ids[:5] for secret in secrets[:5]]


def _ag_refresh_token(refresh_tok):
    if not refresh_tok:
        return ""
    # Keep refreshed access tokens private and separate from Gemini CLI's login.
    token_file = os.path.join(CACHE, "antigravity-token.json")
    identity = hashlib.sha256(refresh_tok.encode()).hexdigest()
    cached = read_json(token_file) or {}
    if cached.get("identity") == identity and cached.get("expiresAt", 0) > now() + 60:
        return cached.get("accessToken", "")
    for client_id, client_secret in _ag_oauth_clients(cached):
        body = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_tok,
            "grant_type": "refresh_token",
        }).encode("utf-8")
        status, data = http_json("https://oauth2.googleapis.com/token", body=body)
        if status == 200 and isinstance(data, dict) and data.get("access_token"):
            token = str(data["access_token"])
            os.makedirs(CACHE, exist_ok=True)
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump({"identity": identity, "accessToken": token,
                           "clientId": client_id, "clientSecret": client_secret,
                           "expiresAt": now() + int(data.get("expires_in", 3600))}, f)
            return token
        # Other native clients embedded in agy may not match this login.
        if status not in (400, 401) or not isinstance(data, dict) or data.get("error") not in ("invalid_client", "unauthorized_client"):
            break
    return ""


def _ag_get_token():
    token = os.environ.get("ANTIGRAVITY_TOKEN", "").strip()
    if token:
        return token
    # Antigravity CLI owns this keyring entry. Gemini CLI's oauth_creds.json
    # uses a different OAuth client and must not shadow or be overwritten by it.
    try:
        raw = subprocess.check_output(
            ["secret-tool", "lookup", "service", "gemini", "username", "antigravity"],
            stderr=subprocess.DEVNULL, timeout=4)
        data = json.loads(raw)
        token_info = data.get("token") or {}
        access_token = token_info.get("access_token", "")
        refresh_tok = token_info.get("refresh_token", "")
        expiry = iso_to_epoch(token_info.get("expiry"))
        if refresh_tok and (not access_token or (expiry and expiry < now() + 60)):
            return _ag_refresh_token(refresh_tok)
        return access_token
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return ""


def _ag_parse_quota_groups(groups):
    wins = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        g_name = group.get("displayName", "")
        if "gemini" in g_name.lower():
            prefix = g_name if "argon" in g_name.lower() else "Gemini"
        elif "claude" in g_name.lower() or "gpt" in g_name.lower():
            prefix = "Claude/GPT"
        else:
            prefix = g_name or "Model"
        for b in group.get("buckets", []):
            if not isinstance(b, dict):
                continue
            w = b.get("window", "")
            win_label = "5h" if "5h" in w.lower() else ("Weekly" if "week" in w.lower() else w)
            label = f"{prefix} ({win_label})"
            rem = b.get("remainingFraction")
            pct = max(0.0, min(100.0, round((1.0 - float(rem)) * 100, 1))) if rem is not None else -1
            reset_epoch = iso_to_epoch(b.get("resetTime"))
            wins.append(window(label, pct, reset_epoch))
    wins.sort(key=lambda x: -x["pct"])
    return wins


def _ag_cached_result():
    # Check local plugin cache
    cache_file = os.path.join(CACHE, "antigravity.json")
    c = read_json(cache_file)
    if c and isinstance(c, dict) and c.get("windows"):
        c["stale"] = True
        return c

    # Check system omarchy agent limits cache
    sys_limits = read_json(os.path.join(SYS_AGENT_CACHE, "antigravity-limits.json"))
    if sys_limits and isinstance(sys_limits, dict) and sys_limits.get("result"):
        res = sys_limits["result"]
        wins = []
        for lim in res.get("limits", []):
            if isinstance(lim, dict):
                pct_val = lim.get("percent", 0)
                pct_norm = pct_val * 100 if pct_val <= 1.0 else pct_val
                wins.append(window(str(lim.get("title") or lim.get("label")), pct_norm, iso_to_epoch(lim.get("resetsAt"))))
        if wins:
            plan = str(res.get("tierLabel") or "Google AI Pro")
            return result(plan=plan, windows=wins, rows=[], pct=worst(wins),
                          headline=("%d%%" % round(worst(wins))), stale=True,
                          hint="Showing last known Antigravity quotas.")

    # Check system state usage
    state_file = os.path.join(HOME, ".local", "state", "omarchy", "agents", "usage", "antigravity.json")
    st = read_json(state_file)
    if st and isinstance(st, dict) and st.get("limits"):
        wins = []
        for lim in st["limits"]:
            if isinstance(lim, dict):
                pct_val = lim.get("percent", 0)
                pct_norm = pct_val * 100 if pct_val <= 1.0 else pct_val
                wins.append(window(str(lim.get("title") or lim.get("label")), pct_norm, iso_to_epoch(lim.get("resetsAt"))))
        if wins:
            plan = str(st.get("tierLabel") or "Google AI Pro")
            return result(plan=plan, windows=wins, rows=[], pct=worst(wins),
                          headline=("%d%%" % round(worst(wins))), stale=True,
                          hint="Showing last known Antigravity quotas.")
    return None


def antigravity(_opts):
    os.makedirs(CACHE, exist_ok=True)
    cache_file = os.path.join(CACHE, "antigravity.json")

    # If recent fresh cache exists (< 60s), reuse it
    cached = read_json(cache_file)
    if cached and isinstance(cached, dict) and cached.get("ok") and not cached.get("stale") and cached.get("quotaUserAgent") == ANTIGRAVITY_USER_AGENT:
        if now() - cached.get("fetchedAt", 0) < 60:
            return cached

    token = _ag_get_token()
    if not token:
        fallback = _ag_cached_result()
        if fallback:
            return fallback
        return fail("Not signed in to Antigravity", "Run `agy` to sign in to Antigravity.")

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": ANTIGRAVITY_USER_AGENT,
    }

    # 1. Probe Google Cloud Code endpoint
    tier_name = "Google AI Pro"
    s_tier, tier_data = http_json(
        f"{ANTIGRAVITY_API_HOST}/v1internal:loadCodeAssist",
        headers, body=b'{"metadata":{"ideType":"IDE_UNSPECIFIED"}}'
    )
    if s_tier == 200 and isinstance(tier_data, dict):
        paid = tier_data.get("paidTier") or {}
        curr = tier_data.get("currentTier") or {}
        tier_name = paid.get("name") or curr.get("name") or "Google AI Pro"

    s_quota, quota_data = http_json(
        f"{ANTIGRAVITY_API_HOST}/v1internal:retrieveUserQuotaSummary",
        headers, body=b"{}"
    )

    if s_quota == 200 and isinstance(quota_data, dict) and "groups" in quota_data:
        wins = _ag_parse_quota_groups(quota_data.get("groups", []))
        res = result(plan=tier_name, windows=wins, rows=[], pct=worst(wins),
                     quotaUserAgent=ANTIGRAVITY_USER_AGENT,
                     headline=("%d%%" % round(worst(wins))) if wins else "")
        write_json(cache_file, res)
        return res

    # 2. Local Language Server fallback if running
    log_dir = Path(HOME) / ".gemini" / "antigravity-cli" / "log"
    logs = sorted(log_dir.glob("cli-*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.is_dir() else []
    for log_path in logs[:3]:
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            port_match = re.search(r"Language server listening on random port at (\d+) for HTTP\b", text)
            if port_match:
                port = int(port_match.group(1))
                s, q_resp = http_json(f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
                                      {"Content-Type": "application/json"}, body=b"{}")
                if s == 200 and isinstance(q_resp, dict):
                    resp_obj = q_resp.get("response", {})
                    wins = _ag_parse_quota_groups(resp_obj.get("groups", []))
                    if wins:
                        res = result(plan=tier_name, windows=wins, rows=[], pct=worst(wins),
                                     headline=("%d%%" % round(worst(wins))))
                        write_json(cache_file, res)
                        return res
        except Exception:
            continue

    # 3. Fallback to cached readings
    fallback = _ag_cached_result()
    if fallback:
        return fallback

    if s_quota in (401, 403):
        return fail("Antigravity sign-in expired", "Run `agy` to sign in and refresh the token.")

    return fail("Antigravity quota unreachable", "Could not reach Antigravity quota endpoint. Will retry automatically.")


# ------------------------------------------------------- Conductor / AI Junction
def _conductor_token():
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "service", "dev.conductor.desktop", "username", "aijunction"],
            capture_output=True, text=True, timeout=4)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def conductor(_opts):
    cache_file = os.path.join(CACHE, "conductor.json")
    cached = read_json(cache_file)
    if cached and cached.get("ok") and not cached.get("stale") and now() - cached.get("fetchedAt", 0) < 60:
        return cached
    token = _conductor_token()
    status, data = http_json("https://api.aijunction.dev/account",
                             {"Authorization": "Bearer " + token}) if token else (401, None)
    user = data.get("user") if isinstance(data, dict) else None
    credits = user.get("credits") if isinstance(user, dict) else None
    try:
        balance = float(credits) if credits is not None else None
    except (ValueError, TypeError):
        balance = None
    if status == 200 and balance is not None:
        res = result(plan="AI Junction", headline="%.2f cr" % balance,
                     rows=[{"label": "AI Junction balance", "value": "%.2f credits" % balance}],
                     hint="Subscription and top-up credits share this balance.")
        write_json(cache_file, res)
        return res
    if cached and cached.get("ok"):
        cached.update(stale=True, hint="AI Junction balance unavailable; showing last reading.")
        return cached
    if not token:
        return fail("No saved AI Junction login", "Sign in to AI Junction in Conductor and save the login.")
    return fail(err_for(status, "AI Junction") if status != 200 else "AI Junction returned no balance",
                "Check your AI Junction login in Conductor.")


# ------------------------------------------------------------------- detect
def detect(_opts):
    def has(p):
        return os.path.exists(os.path.join(HOME, p))
    cl = (read_json(os.path.join(HOME, ".claude", ".credentials.json")) or {}).get("claudeAiOauth") or {}
    ag = has(".gemini/oauth_creds.json") or has(".gemini/antigravity") or has(".local/bin/agy") or os.path.exists("/usr/bin/antigravity")
    return {
        "claude": bool(cl.get("accessToken")),
        "openai": bool(((read_json(os.path.join(HOME, ".codex", "auth.json")) or {}).get("tokens") or {}).get("access_token")),
        "opencode": has(".local/share/opencode/opencode.db"),
        "antigravity": bool(ag),
        "conductor": bool(_conductor_token()),
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")) or has(".config/omarchy/openrouter/key")
                      or has(".config/openrouter/key"),
    }


PROVIDERS = {"claude": claude, "openai": openai, "opencode": opencode,
             "antigravity": antigravity, "conductor": conductor, "openrouter": openrouter, "detect": detect}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        opts = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        if not isinstance(opts, dict):
            opts = {}
    except ValueError:
        opts = {}
    fn = PROVIDERS.get(name)
    try:
        out = fn(opts) if fn else fail("Unknown provider: %s" % name)
    except Exception as e:
        out = fail("Internal error in %s helper" % name, "%s: %s" % (type(e).__name__, str(e)[:100]))
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
