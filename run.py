#!/usr/bin/env python3
"""Set up and run the LINE diary bot with one command.

    python3 run.py            # check setup, start bot + Cloudflare tunnel, register webhook
    python3 run.py --setup    # re-enter the LINE secret / token / user ID
    python3 run.py --notion   # connect (or reconnect) Notion
    python3 run.py --check    # only run the setup checks, don't start anything

The bot stays online while this window is open. Press Ctrl+C to stop.
Only the standard library is used before switching into .venv.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
ENV_FILE = ROOT / ".env"
LINE_API = "https://api.line.me"
TUNNEL_URL_RE = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
SECRET_RE = re.compile(r"^[0-9a-f]{32}$")
# Notion data source ids made for this bot (the "LINE Diary Bot" page).
NOTION_DEFAULTS = {
    "NOTION_DIARY_DS": "b9f02a4c-a43c-45ff-9304-7642b7e4e6d0",
    "NOTION_SHOPPING_DS": "7fb0ccd1-a2f1-44d2-8b2e-b51827ca5bad",
    "NOTION_COOKING_DS": "3b233df3-86fe-47be-8161-1e59353e2bed",
    "NOTION_IDEAS_DS": "c28134b3-3c00-4b4a-9a08-b41d9fd533a7",
    "NOTION_LISTS_DS": "addc618b-26b7-41a3-a511-86da8030359e",
}

TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def say(msg: str) -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    say(_c("32", "✓ ") + msg)


def warn(msg: str) -> None:
    say(_c("33", "! ") + msg)


def fail(msg: str):
    say(_c("31", "✗ ") + msg)
    sys.exit(1)


# --- venv ------------------------------------------------------------------------

def ensure_venv() -> None:
    """Create .venv with the requirements, then re-run this script inside it."""
    if Path(sys.prefix).resolve() == (ROOT / ".venv").resolve():
        return
    if not VENV_PY.exists():
        say("Creating .venv (first run only)…")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    stamp = ROOT / ".venv" / ".requirements"
    reqs = (ROOT / "requirements.txt").read_text()
    if not stamp.exists() or stamp.read_text() != reqs:
        say("Installing requirements…")
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "-q", "-r",
                        str(ROOT / "requirements.txt")], check=True)
        stamp.write_text(reqs)
    os.execv(str(VENV_PY), [str(VENV_PY), str(ROOT / "run.py"), *sys.argv[1:]])


# --- .env ------------------------------------------------------------------------

def read_env() -> dict[str, str]:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env(key: str, value: str) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def ask(label: str, hidden: bool = False, default: str = "") -> str:
    prompt = f"{label}{f' [{default}]' if default else ''}: "
    value = (getpass.getpass(prompt) if hidden else input(prompt)).strip()
    return value or default


def http_json(method: str, url: str, token: str, body: dict | None = None,
              headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def setup_line(force: bool) -> dict:
    env = read_env()
    if force or not SECRET_RE.match(env.get("LINE_CHANNEL_SECRET", "")):
        say("\nLINE Developers Console → your channel → Basic settings → Channel secret")
        while not SECRET_RE.match(secret := ask("Channel secret", hidden=True)):
            warn("That doesn't look like a channel secret (32 hex characters).")
        write_env("LINE_CHANNEL_SECRET", secret)
    if force or not env.get("LINE_CHANNEL_ACCESS_TOKEN"):
        say("\nMessaging API tab → Channel access token (long-lived) → Issue")
        write_env("LINE_CHANNEL_ACCESS_TOKEN", ask("Channel access token", hidden=True))
    env = read_env()
    status, info = http_json("GET", f"{LINE_API}/v2/bot/info", env["LINE_CHANNEL_ACCESS_TOKEN"])
    if status != 200:
        fail(f"LINE rejected the access token ({status}). Run: python3 run.py --setup")
    ok(f"LINE bot: {info.get('displayName')} ({info.get('basicId')})")
    if not env.get("TZ"):
        sys.path.insert(0, str(ROOT))
        from app.config import system_timezone
        write_env("TZ", ask("Time zone", default=system_timezone()))
    if force:
        uid = ask("Your LINE user ID (empty = find it out by messaging the bot)",
                  default=env.get("OWNER_USER_ID", ""))
        write_env("OWNER_USER_ID", uid)
    env = read_env()
    if not env.get("OWNER_USER_ID"):
        write_env("OWNER_USER_ID", "")
        warn("Setup mode: send the bot any message on LINE; it replies with your user ID.\n"
             "  Then run: python3 run.py --setup   and paste it.")
    else:
        ok("Answers only your LINE account")
    return info


def setup_notion(force: bool) -> None:
    env = read_env()
    for key, value in NOTION_DEFAULTS.items():
        if not env.get(key):
            write_env(key, value)
    if force or not env.get("NOTION_TOKEN"):
        say("\nNotion: https://www.notion.so/profile/integrations → New integration\n"
            "  (type: Internal, any name, your workspace) → copy the Internal Integration Secret.\n"
            "  Then open the 'LINE Diary Bot' page in Notion → ⋯ → Connections → add it.")
        write_env("NOTION_TOKEN", ask("Notion integration secret (ntn_…)", hidden=True))
    env = read_env()
    for key in NOTION_DEFAULTS:
        status, info = http_json("GET", f"https://api.notion.com/v1/data_sources/{env[key]}",
                                 env["NOTION_TOKEN"], headers={"Notion-Version": "2025-09-03"})
        name = key.removeprefix("NOTION_").removesuffix("_DS").lower()
        if status == 200:
            ok(f"Notion {name}: '{''.join(t.get('plain_text', '') for t in info.get('title', []))}'")
        elif status in (401, 403):
            fail("Notion rejected the secret. Run: python3 run.py --notion")
        else:
            fail(f"Notion can't see the {name} database ({status}: {info.get('message', '')}).\n"
                 "  Open the 'LINE Diary Bot' page → ⋯ → Connections → add your integration.")


# --- running ------------------------------------------------------------------------

def find_cloudflared() -> str:
    path = shutil.which("cloudflared") or str(Path.home() / ".local/bin/cloudflared")
    if not Path(path).exists():
        fail("cloudflared not found. Install it: https://developers.cloudflare.com/"
             "cloudflare-one/connections/connect-networks/downloads/")
    return path


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read() == b"ok"
    except OSError:
        return False


def run(port: int) -> None:
    env = read_env()
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    bot = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "app.main:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, stdout=open(logs / "bot.log", "a"), stderr=subprocess.STDOUT)
    tunnel = subprocess.Popen(
        [find_cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def stop(*_):
        for p in (tunnel, bot):
            p.terminate()
        say("\nStopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    for _ in range(30):
        if http_ok(f"http://127.0.0.1:{port}/health"):
            break
        if bot.poll() is not None:
            fail(f"The bot didn't start. See {logs / 'bot.log'}")
        time.sleep(0.5)
    ok(f"Bot running on 127.0.0.1:{port}")

    url = None
    for line in tunnel.stderr:
        if m := TUNNEL_URL_RE.search(line):
            url = m.group(0)
            break
    if not url:
        bot.terminate()
        fail("The Cloudflare tunnel didn't start. Check your internet connection.")
    # Keep reading the tunnel output so its pipe never fills up.
    threading.Thread(target=lambda: [None for _ in tunnel.stderr], daemon=True).start()

    say("Waiting for the tunnel to come online (up to ~2 min)…")
    for _ in range(100):
        if http_ok(f"{url}/health"):
            break
        time.sleep(3)
    token = env["LINE_CHANNEL_ACCESS_TOKEN"]
    status, _ = http_json("PUT", f"{LINE_API}/v2/bot/channel/webhook/endpoint", token,
                          {"endpoint": f"{url}/callback"})
    if status != 200:
        warn(f"Couldn't set the webhook ({status}). Paste {url}/callback in the LINE console.")
    else:
        ok(f"Webhook: {url}/callback")
    say(_c("1", "\nThe bot is online. Message it on LINE. Ctrl+C to stop.")
        + f"\nLogs: {logs / 'bot.log'}")
    while True:
        time.sleep(5)
        if bot.poll() is not None or tunnel.poll() is not None:
            warn("The bot or tunnel stopped; restart with: python3 run.py")
            stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up and run the LINE diary bot.")
    ap.add_argument("--setup", action="store_true", help="re-enter the LINE settings")
    ap.add_argument("--notion", action="store_true", help="connect (or reconnect) Notion")
    ap.add_argument("--check", action="store_true", help="only run the setup checks")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    ensure_venv()
    setup_line(args.setup)
    setup_notion(args.notion)
    if args.check or args.setup or args.notion:
        say("\nSetup looks good. Start with: python3 run.py")
        return
    run(args.port)


if __name__ == "__main__":
    main()
