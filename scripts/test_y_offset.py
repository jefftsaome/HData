#!/usr/bin/env python3
"""测试 jfbym 坐标 + Y轴偏移 → verify。
定向测试 Y 偏移 0~30px，看哪个能通过。
"""
import asyncio, json, os, random, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import binascii, hashlib
from Crypto.Cipher import AES as AES_C
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad
from curl_cffi import requests as cr
from hdt.auth.captcha import fetch_captcha
from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
from hdt.auth.geetest_signer import LotParser, _generate_pow, _rand_uid

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)

def make_w(ld, pts, pt_ms):
    ln=ld["lot_number"]; pd=ld["pow_detail"]; lp=LotParser()
    eo={**_generate_pow(ln,CAPTCHA_ID,pd['hashfunc'],pd['version'],pd['bits'],pd['datetime']),
        **lp.get_dict(ln),'biht':'1426265548','em':{},
        'gee_guard':{'auh':'3','aup':'3','cdc':'3','egp':'3','res':'3','rew':'3','sep':'3','snh':'3'},
        'geetest':'captcha','lang':'zh','lot_number':ln,
        'userresponse':pts,'passtime':pt_ms}
    rk=_rand_uid(); ej=json.dumps(eo,separators=(',',':'))
    c=AES_C.new(rk.encode(),AES_C.MODE_CBC,b'0000000000000000')
    ee=c.encrypt(pad(ej.encode(),AES_C.block_size))
    rc=PKCS1_v1_5.new(construct((RSA_N,RSA_E))); ek=rc.encrypt(rk.encode())
    return binascii.hexlify(ee).decode()+binascii.hexlify(ek).decode()

async def main():
    solver = JfbymSolver(api_token=JFBYM_TOKEN)
    
    # 每个 Y 偏移单独一个 captcha
    y_offsets = [0, 10, 15, 18, 20, 22, 25, 30, -5, -10]
    
    print(f"Y轴偏移测试: {len(y_offsets)} 种偏移")
    print(f"{'Y偏移':>6s}  {'结果':>6s}  {'坐标':>30s}")
    print("-" * 50)
    
    for dy in y_offsets:
        data = fetch_captcha()
        if not data: print(f"{dy:+5d}px  ❌ captcha失败"); continue
        
        challenge = CaptchaChallenge(
            lot_number=data["lot_number"], payload=data["payload"],
            process_token=data["process_token"], bg_url=data["bg_url"],
            ques_urls=data["ques_urls"], captcha_id=CAPTCHA_ID)
        sol = await solver.solve(challenge)
        
        # 只偏移 Y
        pts = [[x, max(0, min(199, y+dy))] for x,y in sol.pts]
        
        w = make_w(data, pts, random.randint(800,3000))
        
        cb=f"botion_{int(time.time()*1000)}"
        params={"callback":cb,"captcha_id":CAPTCHA_ID,"client_type":"web",
            "lot_number":data["lot_number"],"payload":data["payload"],
            "process_token":data["process_token"],
            "payload_protocol":data.get("payload_protocol","1"),
            "pt":data.get("pt","1"),"w":w}
        url="https://bcaptcha.botion.com/verify?"+urllib.parse.urlencode(params)
        
        resp=cr.get(url,impersonate="chrome110",
            headers={"Referer":"https://www.leyu.me/","User-Agent":"Mozilla/5.0"},timeout=30)
        m=re.search(r'\((.*)\)$',resp.text,re.DOTALL)
        if m:
            d=json.loads(m.group(1)); r2=d.get('data',{})
            icon="✅" if r2.get('result')=='success' else "❌"
            print(f"{dy:+5d}px  {icon} {str(r2.get('result','?')):>6s}  {str(pts):>30s}")
            if r2.get('result')=='success':
                print(f"\n🎉 找到! Y偏移={dy}px")
        else:
            print(f"{dy:+5d}px  ❌ ERR")
        
        time.sleep(1)

if __name__=='__main__':
    asyncio.run(main())
