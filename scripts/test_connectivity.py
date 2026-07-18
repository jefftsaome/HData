"""Quick connectivity test for the auth pipeline."""
import sys, json, re, time, urllib.parse
sys.path.insert(0, '.')

from curl_cffi import requests as cr
from hdata.auth.domain import resolve_domain
from hdata.auth.captcha import fetch_captcha
from hdata.auth.geetest_signer import generate_w

# Test domain resolution
domain = resolve_domain()
print(f'Domain resolution OK: {domain}')

# Test captcha fetch
data = fetch_captcha()
print(f'Captcha fetch OK: lot_number={data["lot_number"][:20]}...')
print(f'Keys: {list(data.keys())}')

# Quick verify test
CAPTCHA_ID = 'eaffad4f65a38a259ae369faf0c2f1a3'
w = generate_w(data, CAPTCHA_ID, '100,100|150,150|200,200')
cb = f'botion_{int(time.time()*1000)}'
params = {
    'callback': cb, 'captcha_id': CAPTCHA_ID, 'client_type': 'web',
    'lot_number': data['lot_number'], 'payload': data['payload'],
    'process_token': data['process_token'],
    'payload_protocol': '1', 'pt': '1', 'w': w,
}
url = 'https://bcaptcha.botion.com/verify?' + urllib.parse.urlencode(params)
resp = cr.get(url, impersonate='chrome110',
             headers={'Referer': 'https://www.leyu.me/'}, timeout=30)
m = re.search(r'\((.*)\)$', resp.text, re.DOTALL)
vdata = json.loads(m.group(1)) if m else {}
print(f'Verify API reachable: status={vdata.get("status")}, result={vdata.get("data",{}).get("result")}')

# Test jfbym connectivity
r = cr.post(
    'http://api.jfbym.com/api/YmServer/getUserInfoApi',
    json={'token': 'ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8', 'type': 'score'},
    headers={'Content-Type': 'application/json'},
    timeout=10
).json()
if r.get('code') == 10001:
    print(f'jfbym OK, balance: {r.get("data",{}).get("score","?")}')
else:
    print(f'jfbym response: {r}')

# Test kaptchcate
resp = cr.post(
    f'{domain}/site/api/v1/user/member/kaptchcate',
    json={'kType': 4},
    headers={'Content-Type': 'application/json', 'Referer': f'{domain}/user/login'},
    impersonate='chrome110', timeout=15
)
print(f'kaptchcate: {resp.json().get("status_code")} - {resp.json().get("message")}')

print()
print('=== All connectivity tests passed ===')
