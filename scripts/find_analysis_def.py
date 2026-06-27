"""在 assets chunk 中搜索 AnalysisUrlUtils 定义"""
import re

with open("/tmp/chunk_assets.js") as f:
    c = f.read()

print(f"Total size: {len(c)} bytes")

# 搜 checkUrlParams 和 getLoginData 的函数定义
for func_name in ["checkUrlParams", "getLoginData"]:
    print(f"\n=== 搜索 {func_name} ===")
    idx = c.find(func_name)
    if idx < 0:
        # 可能在变量赋值中（变量名被 minify）
        print(f"  {func_name} 直接搜索未找到")
        continue

    print(f"  首次出现: {idx}")
    # 搜索所有出现
    positions = []
    start = 0
    while True:
        pos = c.find(func_name, start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + 1
    print(f"  共 {len(positions)} 处")

    # 找函数定义的上下文
    for pos in positions[:3]:
        # 向前 300 字符找定义
        before = c[max(0, pos - 500) : pos]
        # 找最近的函数定义或对象赋值
        # 关键词: function, =>, :, =
        ctx = c[max(0, pos - 300) : pos + 300]
        # 检查是否是定义（包含 function 或 =>，且不是简单的引用）
        after = c[pos : pos + 200]
        if "function" in after[:50] or "=>" in after[:30]:
            print(f"\n  定义位置 {pos}:")
            print(f"  {ctx[:500]}")
        else:
            # 可能是引用，检查前文是否有定义
            # 搜 "AnalysisUrlUtils" 附近的定义
            au_pos = c.rfind("AnalysisUrlUtils", max(0, pos - 500), pos)
            if au_pos >= 0:
                print(f"\n  引用位置 {pos} (AnalysisUrlUtils 在 {au_pos}):")
                print(f"  {c[max(0, au_pos - 200) : au_pos + 200]}")
            else:
                # 搜 checkUrlParams 出处
                # 看前文是不是 var xxx = function 或类似
                assign_match = re.search(
                    r"(\w+)\s*[:=]\s*function\s*$",
                    c[max(0, pos - 200) : pos],
                )
                print(f"\n  引用位置 {pos} (可能只是调用):")
                print(f"  {before[-100:]}{c[pos:pos+100]}")

# 找 aesDecrypt 或 decrypt 函数
for func in ["aesDecrypt", "decrypt", "_aesDecrypt"]:
    if func in c:
        idx = c.find(func)
        print(f"\n=== {func} at {idx} ===")
        print(f"{c[max(0,idx-100):idx+200]}")
