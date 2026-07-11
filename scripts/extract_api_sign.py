#!/usr/bin/env python3
"""提取乐鱼 X-API-XXX 签名密钥和算法。"""

import json, re, sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ_ROOT))


def find_module_at(text: str, pos: int) -> tuple[str, str, int]:
    """找到包含 pos 位置的 webpack 模块，返回 (模块ID, 模块体, 起始位置)。"""
    # 找所有模块起始: 数字:function(e,t,n){
    module_starts = [(m.start(), m.group(1))
                     for m in re.finditer(r'(\d+):function\(e,t,n\)\{', text)]
    # 找到 pos 之前最近的一个
    mod_start = 0
    mod_id = "?"
    for start, mid in module_starts:
        if start < pos:
            mod_start = start
            mod_id = mid
        else:
            break
    # 找下一个模块起始（或文件结束）
    next_start = len(text)
    for start, _ in module_starts:
        if start > mod_start:
            next_start = start
            break
    # 需要包含到下一个模块的 }, 之前
    body = text[mod_start:next_start]
    # 截断到最后一个 }
    last_brace = body.rfind('}')
    if last_brace > 0:
        body = body[:last_brace + 1]
    return mod_id, body, mod_start


def main():
    from curl_cffi import requests as curl_requests

    url = "https://www.94d9qm.vip:9023/_next/static/chunks/pages/_app-4476f53e629fb53f.js"
    print(f"📥 下载: {url}")
    resp = curl_requests.get(url, impersonate="chrome110", timeout=15)
    text = resp.text
    print(f"   大小: {len(text)} bytes")

    # 1. 找到 TC 导出
    tc_export = re.search(r'TC:function\(\)\{return (\w+)\}', text)
    if not tc_export:
        print("❌ 未找到 TC 导出")
        return

    fn_name = tc_export.group(1)  # 'y'
    tc_pos = tc_export.start()
    print(f"\n🎯 TC 导出位置: {tc_pos}, 返回变量: {fn_name}")

    # 2. 找到包含此导出的 webpack 模块
    mod_id, mod_body, mod_start = find_module_at(text, tc_pos)
    print(f"   模块 ID: {mod_id}, 起始: {mod_start}, 大小: {len(mod_body)} bytes")

    # 3. 在模块中找 fn_name 的定义
    # 搜索 y = 或 function y( 或 var y = 或 let y = 或 const y =
    def_match = re.search(
        rf'(?:^|\n)\s*(?:var|let|const)\s+{fn_name}\s*=\s*function\s*\(([^)]*)\)\s*\{{(.*?)\}}\s*(?:,|;|\n|$)',
        mod_body, re.DOTALL
    )
    if not def_match:
        def_match = re.search(
            rf'(?:^|\n)\s*(?:var|let|const)\s+{fn_name}\s*=\s*\(([^)]*)\)\s*=>\s*\{{(.*?)\}}\s*(?:,|;|\n|$)',
            mod_body, re.DOTALL
        )
    if not def_match:
        # 可能是: y=(e)=>{...}
        def_match = re.search(
            rf'{fn_name}\s*=\s*(?:\(?(\w*)\)?\s*=>|function\s*\((\w*)\))\s*\{{(.*?)\}}',
            mod_body, re.DOTALL
        )

    if def_match:
        params = def_match.group(1) or def_match.group(2) or ""
        body = def_match.group(3) or def_match.group(4) or ""
        print(f"\n📝 {fn_name}({params}) 函数体:")
        # 美化输出
        full_def = def_match.group(0)
        print(full_def[:2000])
    else:
        # 尝试搜所有的 y 引用
        print(f"\n⚠️ 未找到 {fn_name} 定义，尝试搜索所有引用...")
        refs = [m for m in re.finditer(rf'\b{fn_name}\b', mod_body)]
        print(f"   {fn_name} 在模块中出现 {len(refs)} 次")
        for ref in refs[:5]:
            ctx = mod_body[max(0, ref.start()-50):ref.end()+200]
            print(f"   ...{ctx}...")

    # 4. 找模块中所有可能的密钥
    print(f"\n{'='*60}")
    print("🔑 模块中的密钥候选:")
    print(f"{'='*60}")

    # hex 字符串 (16-128 chars)
    for m in re.finditer(r'["\']([0-9a-fA-F]{32,128})["\']', mod_body):
        val = m.group(1)
        ctx_start = max(0, m.start()-50)
        ctx_end = min(len(mod_body), m.end()+50)
        ctx = mod_body[ctx_start:ctx_end]
        # 排除明显的 webpack hash
        if not re.search(r'(chunks|static|buildId)', ctx):
            print(f"\n  hex: {val}")
            print(f"  上下文: ...{ctx}...")

    # 5. 找 HmacSHA256 或 createHmac 调用
    print(f"\n{'='*60}")
    print("🔐 HMAC/SHA256 调用:")
    print(f"{'='*60}")
    for m in re.finditer(r'.{0,50}(?:HmacSHA256|createHmac|sha256|SHA256).{0,300}', mod_body, re.DOTALL):
        print(f"\n  {m.group(0)[:300]}")

    # 6. 试图理解签名公式
    # 从之前的分析，TC 接受 URL path，返回 HMAC
    # 找 URL 提取逻辑 (/\w+/\w+/i 正则)
    print(f"\n{'='*60}")
    print("🔗 X-API-XXX 生成逻辑（完整上下文）:")
    print(f"{'='*60}")

    # 搜索 X-API-XXX 附近的完整代码
    xxx_match = re.search(r'.{0,500}X-API-XXX.{0,2000}', text, re.DOTALL)
    if xxx_match:
        snippet = xxx_match.group(0)
        # 格式化: 在关键位置换行
        snippet = re.sub(r'(X-API-\w+)', r'\n  \1', snippet)
        snippet = re.sub(r'(case\s+\d+)', r'\n\1', snippet)
        print(snippet[:3000])

    # 7. 尝试直接调用 TC 函数来验证
    print(f"\n{'='*60}")
    print("🧪 验证思路:")
    print(f"{'='*60}")
    print("""
要验证签名算法，需要:
1. 在 CDP 中直接调用 h.TC(path) 并比对返回值
2. 或者用 curl-cffi 发一个已知请求，从浏览器拦截 X-API-XXX，然后
   尝试 HMAC-SHA256(path, key) 比对

下一步: 用 CDP 调用 h.TC 获取已知输入的签名值
    """)

    # 8. 保存
    out = _PROJ_ROOT / "data" / "api_sign_module.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # 只保存模块的前后部分（太大）
    out.write_text(json.dumps({
        "module_id": mod_id,
        "size": len(mod_body),
        "head": mod_body[:5000],
        "tail": mod_body[-3000:],
    }, indent=2, ensure_ascii=False))
    print(f"\n💾 模块保存到: {out}")


if __name__ == "__main__":
    main()
