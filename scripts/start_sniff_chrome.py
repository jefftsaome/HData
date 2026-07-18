"""启动带 CDP 调试端口的 Chrome 并打开登录页（供嗅探用）。"""
import subprocess
import sys
import time
import urllib.request

CHROME = r"G:\Applications\Scoop\apps\googlechrome\current\chrome.exe"
import os
if not os.path.exists(CHROME):
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]:
        if os.path.exists(p):
            CHROME = p
            break

URL = "https://www.ylsvq5.vip:9003/user/login"
PROFILE = os.path.abspath(".cache/browser_profiles/sniff")

subprocess.Popen([
    CHROME,
    "--remote-debugging-port=9222",
    f"--user-data-dir={PROFILE}",
    "--no-first-run",
    "--no-default-browser-check",
    URL,
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

for _ in range(30):
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=1)
        print("CDP 就绪: http://127.0.0.1:9222")
        sys.exit(0)
    except Exception:
        time.sleep(1)
print("Chrome 启动超时")
sys.exit(1)
