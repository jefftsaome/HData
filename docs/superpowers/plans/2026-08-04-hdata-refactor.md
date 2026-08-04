# hdata 堆叠屎山重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变任何对外行为的前提下,拆除 hdata 的结构性屎山:外置数据/资产、打破 auth 循环依赖、拆分 God 文件、收敛重复实现、补齐质量门禁。

**Architecture:** 按依赖图从下往上拆。先做零风险数据/死代码清理(P1),再抽底层原语打破 auth 循环(P2),接着为 client.py 建特征测试网后拆分(P3),最后收敛已分叉的重复实现(P4)并上卫生门禁(P5)。每一阶段结束时测试全绿,单独可发布。

**Tech Stack:** Python 3.13(.venv), pytest, setuptools, uv(已锁 uv.lock), 无 ruff/mypy/coverage(待 P5 加), 无 CI(待 P5 加)。

## Global Constraints

- 所有命令在 `D:\my-code-repo\myown\HData` 目录下执行;Python 解释器用 `& .venv\Scripts\python.exe`。
- **测试基线:266 tests collected / 0 errors**(`& .venv\Scripts\python.exe -m pytest -q`)。每任务结束必须回绿。
- **行为不可变**:任何重构不得改变函数签名、返回结构、对外导出;补丁分支(兜底/降级/兼容)是踩坑换来的存活逻辑,一律保留。
- **live 链路禁忌区(内部一行不动,只允许包壳/收敛拷贝)**:`auth/session.py:_get_login_inner`、`auth/session.py:408 _refresh_game_session_inner`、`protocol/schemacodec.py` 热更新逻辑、`client.py:1972 _gateway_request` HMAC 加签、`client.py:741 _WSConnection` 心跳/看门狗。
- 若重构触及活体服务器依赖行为(签名/验证码/WS 帧),不允许一次合并多个行为差异;每个差异独立提交、独立验证。
- 提交信息用中文描述式,风格对齐 git log(如 `refactor(schema): 外置 schema 基线为 JSON`)。
- 每阶段结束是一个 checkpoint:跑全套测试 + `import hdata.auth; import hdata.client; import hdata.sources.leyu_ws` 冒烟。

## File Structure

新建文件(目标职责):

| 文件 | 职责 |
|---|---|
| `hdata/protocol/schema_data.json` | schema 基线数据(原 2402 行 dict 字面量) |
| `hdata/capture/js/dynamic_extract.js` | 原内嵌 212 行 JS 提取脚本 |
| `hdata/auth/headers.py` | `build_api_headers()` 签名头唯一实现 |
| `hdata/auth/sign_table.py` | `decrypt_sign_table()` uuidToBase64 解密唯一实现 |
| `hdata/client/transport.py` / `hdata/client/tables.py` / `hdata/client/gateway.py` | 从 client.py 拆出的 WS 传输 / 状态机 / 网关加签(P3) |
| `tests/test_headers.py`、`tests/test_sign_table.py`、`tests/test_characterization_client.py`、`tests/test_import_smoke.py` | 各阶段特征/回归测试 |
| `scripts/migrate_schema_data.py` | 一次性迁移脚本(P1 后保留但标注 deprecated) |
| `.github/workflows/ci.yml`、`[tool.ruff]`、`[tool.mypy]`、`[tool.coverage]` | 质量门禁(P5) |

修改文件(核心节点):
- `hdata/protocol/_schema_data.py` — 缩为 SCHEMA_CONFIG 所有者 + JSON 加载器
- `hdata/auth/session.py`、`hdata/auth/token_manager.py`、`hdata/auth/http_login.py` — 改为从叶子模块导入,删除函数内懒 import
- `hdata/client.py` — P3 拆出后保留门面
- `hdata/capture/dom_extractor.py` — JS 改为读文件
- `pyproject.toml` — package-data 增加 json/js 分发;P5 加工具配置

删除文件: `hdata/protocol/round_tracker.py`、`hdata/auth/captcha.py`(待验证无引用)、`tests/test_round_tracker.py`。

---

## 阶段门禁(checkpoint)

每个 phase 结束执行:

```powershell
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth, hdata.client, hdata.sources.leyu_ws; print('smoke ok')"
git status --short
```

判定:266 tests passed + `smoke ok` + 无意外改动文件。

---

# P1 — 零风险前置清理(风险 ★)

## Task 1: 外置 schema 基线为 JSON

**Files:**
- Create: `scripts/migrate_schema_data.py`
- Create: `hdata/protocol/schema_data.json`
- Modify: `hdata/protocol/_schema_data.py`(重写为加载器)
- Modify: `pyproject.toml`(package-data 加 json)
- Test: `tests/test_schema_data_extract.py`

**Interfaces:**
- Consumes: `hdata.protocol._schema_data.SCHEMA_CONFIG`(可变 dict,被 `schemacodec.py:31` 导入并在 `:417/:444` 原地写)
- Produces: `SCHEMA_CONFIG` 保持为同模块同名可变 dict;新文件 `schema_data.json`

- [ ] **Step 1: 生成 JSON(一次性脚本)**

`scripts/migrate_schema_data.py`:

```python
"""一次性迁移: 将 _schema_data.SCHEMA_CONFIG 落盘为 schema_data.json。"""
import json
from pathlib import Path

from hdata.protocol._schema_data import SCHEMA_CONFIG

out = Path(__file__).resolve().parent.parent / "hdata" / "protocol" / "schema_data.json"
blob = json.dumps(SCHEMA_CONFIG, ensure_ascii=False, indent=1) + "\n"
out.write_text(blob, encoding="utf-8")
loaded = json.loads(blob)
assert loaded == SCHEMA_CONFIG, "round-trip mismatch"
print(f"written {out} ({out.stat().st_size} bytes), {len(SCHEMA_CONFIG)} keys")
```

运行:
```powershell
& .venv\Scripts\python.exe scripts/migrate_schema_data.py
```
Expected:`written ... (xxxxx bytes), N keys`(round-trip assert 不触发)。若 N < 5 则停止排查。

- [ ] **Step 2: 重写 `_schema_data.py`**

替换全文件内容为:

```python
"""二进制 schema 协议配置基线(数据外置为同目录 schema_data.json)。

原 2402 行 dict 字面量已迁移;本模块是 SCHEMA_CONFIG 唯一所有者,
运行时热更新(schemacodec.update_schema_config)仍原地修改此 dict。
"""
import json
from pathlib import Path

SCHEMA_CONFIG: dict = json.loads(
    (Path(__file__).with_name("schema_data.json")).read_text(encoding="utf-8")
)
```

- [ ] **Step 3: package-data 增加分发**

`pyproject.toml`:

```toml
[tool.setuptools.package-data]
"hdata.auth" = ["*.cjs", "*.wasm"]
"hdata.protocol" = ["*.json"]
```

- [ ] **Step 4: 写回归测试**

`tests/test_schema_data_extract.py`:

```python
"""外置后基线一致性 + 解码回归。"""
from hdata.protocol._schema_data import SCHEMA_CONFIG


def test_baseline_loaded_nonempty():
    assert len(SCHEMA_CONFIG) >= 5
    for v in SCHEMA_CONFIG.values():
        assert isinstance(v, dict)


def test_scalars_survive_round_trip():
    import json
    from pathlib import Path

    blob = json.loads(
        Path("hdata/protocol/schema_data.json").read_text(encoding="utf-8")
    )
    assert blob == SCHEMA_CONFIG
```

- [ ] **Step 5: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_schema_data_extract.py tests/test_schemacodec.py tests/test_schema_hotupdate.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "from hdata.protocol._schema_data import SCHEMA_CONFIG; print(len(SCHEMA_CONFIG))"
```
Expected:新增 2 通过;`test_schemacodec`(15)+`test_schema_hotupdate`(8)全过;全套 268 通过;`print` 输出 N>=5。

- [ ] **Step 6: Commit**

```bash
git add hdata/protocol/_schema_data.py hdata/protocol/schema_data.json scripts/migrate_schema_data.py tests/test_schema_data_extract.py pyproject.toml
git commit -m "refactor(schema): 外置 schema 基线 2402 行 dict 为 schema_data.json,保留 SCHEMA_CONFIG 可变语义"
```

**验收:** `_schema_data.py` ≤ 20 行;schema_data.json 已跟踪;268 测试绿。

---

## Task 2: 清理死代码

**pre-flight 修正(2026-08-04):** `hdata/auth/captcha.py` 的 `fetch_captcha()` 被 `http_login.py:36/614` 与 `token_manager.py:503/512` 活跃使用,**不能整文件删除**;只删其中的死函数 `solve()` 与 `ocr_ques()`。`decode_road_paper` 函数本体在 `roadpaper.py:142` 定义、`test_roadpaper.py:9/80` 使用,只删 `client.py:62` 的未用 import。`round_tracker.py` 除 `tests/test_round_tracker.py` 外还被 `test_roadpaper.py:91-96`(`test_round_tracker_feed_road_paper`)引用,需一并移除该测试函数。`build/` 为 gitignored 构建残留,扫描时排除。

**Files:**
- Delete: `hdata/protocol/round_tracker.py`、`tests/test_round_tracker.py`、`test_roadpaper.py:91-96`(`test_round_tracker_feed_road_paper`)
- Modify: `hdata/auth/captcha.py`(仅删死函数 `solve`/`ocr_ques`,保留 `fetch_captcha`/`_get_domain`/`BOTION_LOAD`/`CAPTCHA_ID`)
- Modify: `hdata/client.py:62`(仅删 `decode_road_paper` import)、`hdata/auth/api.py:27`(删 `Optional`)、`hdata/auth/token_manager.py:38`(删 `_extract_params_from_url`)、`hdata/sources/leyu_ws.py:55`(删死常量 `AES_KEY`)、`hdata/sources/leyu_cdp.py:242-243`(删调试 print)

**Interfaces:**
- Consumes: 无(纯删除)
- Produces: 无

- [ ] **Step 1: 先 grep 验证引用(仅预期命中项才算通过)**

```powershell
Get-ChildItem -LiteralPath hdata -Recurse -Filter *.py | Select-String -Pattern "round_tracker|RoundTracker|TableState|ocr_ques|def solve" -Encoding UTF8
Get-ChildItem -LiteralPath hdata -Recurse -Filter *.py | Select-String -Pattern "fetch_captcha" -Encoding UTF8
```
Expected:第一项仅命中 `hdata/protocol/round_tracker.py` 自身(待删);第二项命中 `http_login.py`(fetch_captcha 使用者,保留)。`fetch_captcha` 若只有 http_login/token_manager 两处使用,继续;若还有其它调用者,停下确认。

- [ ] **Step 2: 删除与瘦身**

1. 删除 `hdata/protocol/round_tracker.py`、`tests/test_round_tracker.py`。
2. 删除 `tests/test_roadpaper.py:91-96` 的 `test_round_tracker_feed_road_paper` 函数。
3. 编辑 `hdata/auth/captcha.py`:删除 `ocr_ques`(`:103`)与 `solve`(`:123`)两个函数及其不再使用的 import;保留 `fetch_captcha`/`_get_domain`/`BOTION_LOAD`/`CAPTCHA_ID`。
4. 删 `client.py:62` 的 `decode_road_paper` import(保留 `decode_bead_plate`);删 `auth/api.py:27` 的 `Optional`;删 `auth/token_manager.py:38` 的 `_extract_params_from_url`;删 `leyu_ws.py:55` 死常量 `AES_KEY`;删 `leyu_cdp.py:242-243` 两行调试 print。

- [ ] **Step 3: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth, hdata.client, hdata.sources.leyu_ws; print('smoke ok')"
```
Expected:全套通过(基线 268 减 round_tracker 6 减 test_roadpaper 1 = 261 项,其中已知预存在失败 1 项);`smoke ok`。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: 清除死代码 round_tracker/未用 import/调试 print;captcha.py 仅删死函数保留 fetch_captcha"
```

**验收:** 上述 Step 1 grep 第一项 0 命中(round_tracker 已删);`fetch_captcha` 仍被 http_login/token_manager 使用;全套通过;工作树无 `.pyc` 等杂项被提交。

---

## Task 3: 外置 DOM 提取 JS

**Files:**
- Create: `hdata/capture/js/dynamic_extract.js`(原 `dom_extractor.py:33-244` 的 `DYNAMIC_EXTRACT_JS` 字符串原文,逐字节复制)
- Modify: `hdata/capture/dom_extractor.py`(字面量改为读文件)
- Modify: `pyproject.toml`(package-data 加 js)
- Test: `tests/test_dom.py`(既有)+ 新增一个尺寸断言

**Interfaces:**
- Consumes: 原 `DYNAMIC_EXTRACT_JS: str`
- Produces: 同名 `DYNAMIC_EXTRACT_JS: str`,值逐字节相同

- [ ] **Step 1: 抽出 JS 文件**

用编辑器把 `dom_extractor.py:33-244` 三引号字符串内容原样写入 `hdata/capture/js/dynamic_extract.js`。字符串内的转义(如 `\n`、`\\`)按原 Python 字面量语义解码后再写文件——即最终 JS 文本应与原运行时字符串相等。

- [ ] **Step 2: 改造加载点**

`dom_extractor.py` 中替换为:

```python
from pathlib import Path

_JS_PATH = Path(__file__).parent / "js" / "dynamic_extract.js"
DYNAMIC_EXTRACT_JS = _JS_PATH.read_text(encoding="utf-8")
```

- [ ] **Step 3: package-data**

```toml
[tool.setuptools.package-data]
"hdata.auth" = ["*.cjs", "*.wasm"]
"hdata.protocol" = ["*.json"]
"hdata.capture" = ["js/*.js"]
```

- [ ] **Step 4: 回归测试**

`tests/test_dom.py` 末尾加:

```python
def test_dynamic_extract_js_loaded_from_file():
    from hdata.capture.dom_extractor import DYNAMIC_EXTRACT_JS

    assert len(DYNAMIC_EXTRACT_JS) > 10000
```

- [ ] **Step 5: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_dom.py -q
& .venv\Scripts\python.exe -m pytest -q
```
Expected:test_dom 全过(22+1);全套 260 通过。

- [ ] **Step 6: Commit**

```bash
git add hdata/capture/js/dynamic_extract.js hdata/capture/dom_extractor.py tests/test_dom.py pyproject.toml
git commit -m "refactor(capture): 内嵌 212 行提取 JS 外置为独立资产文件"
```

**验收:** `dom_extractor.py` 内无三引号 JS 大块;260 测试绿。

**P1 checkpoint:** 全绿 + smoke ok。

---

# P2 — 打破 auth 循环依赖(风险 ★★)

前置:TokenManager 与 session 的行为差异点已记录在案(见各任务"行为差异"注)。

## Task 4: 抽取签名头与解密表为叶子模块

**Files:**
- Create: `hdata/auth/headers.py`、`hdata/auth/sign_table.py`
- Modify: `hdata/auth/session.py:335` `_api_headers`、`hdata/auth/session.py:369`(改用 sign_table)、`hdata/auth/token_manager.py:705` `_api_headers`、`hdata/auth/token_manager.py` `_decrypt_sign_table`
- Test: `tests/test_headers.py`、`tests/test_sign_table.py`

**Interfaces:**
- Consumes: `session: dict`(键: `signatures`/`uuidToBase64`/`token`/`domain`/`cookies`/`account`)
- Produces:
  - `headers.resolve_api_xxx(session: dict, url: str, *, enable_wasm: bool = True) -> str` — 共享的 X-API-XXX 签名链(wasm → 手动签名表 → uuidToBase64 解密);`enable_wasm=False` 时跳过 wasm 层
  - `headers.build_api_headers(session: dict, url: str, *, enable_wasm: bool = True) -> dict` — session.py 版完整头构造(含 `_device_uuid_for`/`_ua_for` 私有助手,搬入 headers.py)
  - `sign_table.decrypt_sign_table(uuid_b64: str) -> dict`
  - `TokenManager._decrypt_sign_table` 改为委托别名(保留下划线签名,不动 `session.py:369` 调用)

**行为差异(必须用参数保留,不得顺手合并):** 两个 `_api_headers` 的差异**不止 wasm 一层**——session.py 版签名链 3 层(wasm→手动表→解密),token_manager.py 版 2 层(无 wasm);且两版的 **X-API-UUID / User-Agent 计算不同**(token_manager 版用 `self.account` 兜底、UA 多一层 `get_ua("")` 回退)。Task 4 只抽共享签名链 `resolve_api_xxx`:session 调 `enable_wasm=True`,token_manager 调 `enable_wasm=False`;**各调用方保留自己的 uuid/UA 逻辑不变**。wasm 差异是否合并留给 Task 10 决策。

- [ ] **Step 1: 写特征测试(先红)**

`tests/test_headers.py`:

```python
import pytest
from hdata.auth.headers import build_api_headers


def _sample_session(**over):
    s = {
        "token": "tk", "domain": "https://leyu.me", "cookies": "ck=1",
        "uuidToBase64": "", "signatures": {}, "device_uuid": "dev",
    }
    s.update(over)
    return s


def test_headers_structure():
    h = build_api_headers(_sample_session(), "https://leyu.me/game/api")
    assert set(h) >= {
        "X-API-TOKEN", "X-API-UUID", "X-API-XXX", "X-API-CLIENT",
        "X-API-SITE", "X-API-VERSION", "Content-Type", "Referer",
        "User-Agent", "Cookie",
    }


def test_manual_signature_fallback():
    h = build_api_headers(
        _sample_session(signatures={"/game/api": "SIG"}),
        "https://leyu.me/game/api",
    )
    assert h["X-API-XXX"] == "SIG"


def test_no_wasm_when_disabled(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("wasm path must not be taken")

    monkeypatch.setattr("hdata.auth.headers.api_sign.sign_path", boom)
    h = build_api_headers(
        _sample_session(signatures={"/game/api": "SIG"}),
        "https://leyu.me/game/api", enable_wasm=False,
    )
    assert h["X-API-XXX"] == "SIG"
```

`tests/test_sign_table.py`(用真实密钥样本,取自现有测试/缓存中的 uuidToBase64):

```python
from hdata.auth.sign_table import decrypt_sign_table


def test_decrypt_sample_returns_sig_map():
    # 样本取 tests/fixtures 或 .cache 中现成 uuidToBase64 值
    result = decrypt_sign_table("")
    assert isinstance(result, dict)
```

(若找不到现成样本,改为断言 `decrypt_sign_table` 与 `TokenManager._decrypt_sign_table` 对同一输入返回相同结果。)

- [ ] **Step 2: 运行确认红**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_headers.py tests/test_sign_table.py -q
```
Expected:`ModuleNotFoundError`(模块不存在)。

- [ ] **Step 3: 抽取实现**

`hdata/auth/headers.py`:
1. 把 `session.py:335-388` `_api_headers` 全文搬入,改名 `build_api_headers(session, url, *, enable_wasm=True)`,`enable_wasm` 包住 wasm 分支(`session.py:344-351`);`_device_uuid_for`/`_ua_for`(session.py:302-332)一并搬入为私有助手。
2. 抽出共享签名链 `resolve_api_xxx(session, url, *, enable_wasm=True) -> str`:内容是 wasm 分支 + 手动签名表 + uuidToBase64 解密三层,`enable_wasm=False` 时跳过 wasm 分支;`build_api_headers` 内部调用它。

`session.py` 中 `_api_headers` 改为:
```python
from hdata.auth.headers import build_api_headers

def _api_headers(session: dict, url: str) -> dict:
    return build_api_headers(session, url, enable_wasm=True)
```
(保留旧名兼容内部调用。)

`hdata/auth/sign_table.py`:把 `TokenManager._decrypt_sign_table` 方法体搬入为 `decrypt_sign_table(uuid_b64) -> dict`。`token_manager.py` 中:

```python
from hdata.auth.sign_table import decrypt_sign_table
...
class TokenManager:
    @staticmethod
    def _decrypt_sign_table(uuid_b64: str) -> dict:
        return decrypt_sign_table(uuid_b64)
```

`token_manager.py:704` `_api_headers` 改为:**保留其原有的 uuid/UA 计算与返回 dict 组装逻辑**,仅把其中的签名链两段(手动表 + `_decrypt_sign_table` 解密,见 706-723 行)替换为 `xxx = resolve_api_xxx(session, url, enable_wasm=False)`(从 headers 导入)。**该文件本次除"换签名链调用点"外不做任何逻辑改动,尤其不得改动 uuid/UA 计算。**

- [ ] **Step 4: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_headers.py tests/test_sign_table.py tests/test_http_captcha_login.py tests/test_token_lifecycle.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth, hdata.client; print('smoke ok')"
```
Expected:新测试绿;`test_http_captcha_login`(32)与 `test_token_lifecycle`(5)全过;全套绿;`smoke ok`。

- [ ] **Step 5: Commit**

```bash
git add hdata/auth/headers.py hdata/auth/sign_table.py hdata/auth/session.py hdata/auth/token_manager.py tests/test_headers.py tests/test_sign_table.py
git commit -m "refactor(auth): 抽 resolve_api_xxx/decrypt_sign_table 为叶子模块,enable_wasm 参数保留两调用方行为差异"
```

**验收:** `Select-String -Path ... -Pattern "def _api_headers"` 0 处(只剩 session.py 的薄包装);`TokenManager._decrypt_sign_table` 为一行委托;token_manager 的 uuid/UA 计算未被改动;全套绿。

---

## Task 5: 收敛域名解析

**pre-flight 修正(2026-08-04):** 三个"域名解析"实现各有**独特兜底,不能直接一行委托**,否则改变行为:
- `session.get_real_domain`(session.py:199-231)已是规范实现:`resolve_domain(validate=True)` → HDATA_DOMAIN → 抛 DomainError。**保持不动**,作唯一权威入口。
- `token_manager._resolve_domain`(token_manager.py:597-607):当前先 `DomainCache().get()` 再 `resolve_domain()` 再 env,**重复了 domain.py 内部已做的缓存访问**,且无 validate 探活、失败返回 None 而非抛错。收敛为:`return resolve_domain() or os.getenv("HDATA_DOMAIN", None)`,删除重复的 DomainCache 手动访问,行为完全一致(domain.py 内部已做 cache.get)。**不得**改用它版本(会引入 validate 探活差异)。
- `http_login._get_domain`(http_login.py:85-95):`resolve_domain(validate=True)` → 失败时**独特的 curl 重定向兜底**。这是踩坑换来的存活逻辑,保留原样,不委托。只在确认其 `_resolve_domain(validate=True)` 调用与 domain.resolve_domain 等价后,把别名收敛到 `from hdata.auth.domain import resolve_domain as _resolve_domain`(若已是则不动)。

**Files:**
- Modify: `hdata/auth/token_manager.py:597-607` `_resolve_domain`(去重复缓存访问)
- Modify: `hdata/auth/http_login.py`(仅核对别名,不委托)
- Verify: `hdata/auth/session.py:199`(确认已是规范,不动)

**Interfaces:**
- Consumes: `domain.resolve_domain(entry_url="", *, validate=False) -> str | None`(既有)
- Produces: `TokenManager._resolve_domain(self) -> str | None` 语义不变;`session.get_real_domain` 不变

- [ ] **Step 1: 收敛 token_manager._resolve_domain**

```python
async def _resolve_domain(self) -> str | None:
    """解析乐鱼域名(委托 domain.resolve_domain,失败回退 env)。"""
    from hdata.auth.domain import resolve_domain

    return resolve_domain() or os.getenv("HDATA_DOMAIN", None)
```

删除原来对 `DomainCache` 的手动 `cache.get()`(domain.py:155/164 内部已做)。若 `DomainCache` 因此在 token_manager.py 无其他用途,一并移除该 import。

- [ ] **Step 2: 核对 http_login 别名**

`http_login.py:87` 的 `_resolve_domain(validate=True)` 若来自 `from hdata.auth.domain import resolve_domain as _resolve_domain`,保持;若不是,收敛为该别名。**不改变函数体、不委托。**

- [ ] **Step 3: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py tests/test_token_lifecycle.py tests/test_login_trace.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth; print('smoke ok')"
```

- [ ] **Step 4: Commit**

```bash
git add hdata/auth/token_manager.py hdata/auth/http_login.py
git commit -m "refactor(auth): 域名解析去重复缓存访问;http_login 独特重定向兜底保留"
```

**验收:** `token_manager._resolve_domain` 无 `DomainCache` 手动访问;`http_login._get_domain` 的 curl 重定向兜底原样保留;`session.get_real_domain` 未改动;全套绿。

---

## Task 6: 拆除 auth 循环依赖(懒 import → 模块级)

**pre-flight 修正(2026-08-04,AST 实测):** 原计划行号已过时。当前 auth 包内函数级 `from hdata.auth.*` import 实测清单:
- **可安全提升为模块级的叶子依赖**(目标模块不反向 import 本模块): `session.py:278`(`params.build_auth_snapshot`)、`token_manager.py:111/501/760`(`captcha_solver.*`)、`token_manager.py:500`(`captcha.fetch_captcha`)、`token_manager.py:502`(`geetest_signer.generate_w`)、`token_manager.py:599`(`domain.resolve_domain`)、`token_manager.py:702/703/711`(`api_sign.get_uuid` / `fingerprint.get_ua`)、`headers.py:39`(`fingerprint.get_ua`)
- **必须保留函数内 import 的真环(编排回调)**: `session.py:868`(`token_manager.TokenManager`) ↔ `token_manager.py:152/:363`(`session.*`) 是唯一双向真环
- **单向编排(HTTP 登录降级 / 浏览器登录降级),保留函数内 import 并补注释**: `session.py:725`(`http_login.login`)、`session.py:823`(`browser_login.GameBrowserLogin`)、`token_manager.py:383`(`browser_login.GameBrowserLogin`)
- 其余函数级 import(`browser_login.py` 的 playwright/stdlib、`http_login.py:304` datetime、`token_manager.py` 的 stdlib/curl/playwright)为非 hdata.auth 依赖,不动

**Files:**
- Modify: `hdata/auth/session.py`、`hdata/auth/token_manager.py`、`hdata/auth/headers.py`
- Test: `tests/test_import_smoke.py`

**Interfaces:**
- Consumes: Task 4/5 产出的叶子模块
- Produces: 无(纯 import 结构调整)

- [ ] **Step 1: 逐个提升懒 import**

只提升上述"可安全提升"清单;每提升一处立刻跑:

```powershell
& .venv\Scripts\python.exe -c "import hdata.auth, hdata.client; print('ok')"
```

若出现 `ImportError`,回退该处(说明仍有隐藏环),标注为编排回环。

对真环与单向编排处(`session.py:725/:823/:868`、`token_manager.py:152/:363/:383`)补注释:
`# 编排回环:保持函数内导入以避免 import 死锁,见 P4 拆 orchestrator 方案`。

- [ ] **Step 2: 写导入顺序冒烟测试**

`tests/test_import_smoke.py`:

```python
import subprocess
import sys


def _fresh(expr: str) -> int:
    return subprocess.run([sys.executable, "-c", expr], capture_output=True).returncode


def test_import_in_leaf_first_order():
    assert _fresh("import hdata.client, hdata.sources.leyu_ws") == 0


def test_import_in_source_first_order():
    assert _fresh("import hdata.sources.leyu_ws, hdata.client") == 0


def test_import_auth_internals_direct():
    assert _fresh("import hdata.auth.session, hdata.auth.token_manager") == 0
```

- [ ] **Step 3: 验证 + Commit**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_import_smoke.py -q
& .venv\Scripts\python.exe -m pytest -q
```
```bash
git add hdata/auth/session.py hdata/auth/token_manager.py hdata/auth/headers.py tests/test_import_smoke.py
git commit -m "refactor(auth): 叶子依赖懒 import 提升为模块级,编排回环标注留 P4;新增导入顺序冒烟测试"
```

**验收:** 全部 3 个冒烟测试过;函数体内 `from hdata.auth.*` 仅剩上述 6 处编排回环(均带注释);全套绿。

**P2 checkpoint:** 全绿 + smoke ok。auth 包循环由"地雷区"变为"仅剩已标注的编排回环"。

---

# P3 — 拆 God 文件(风险 ★★★,必须先有特征测试网)

## Task 7: 为 client.py 建立特征测试网

**Files:**
- Test: `tests/test_characterization_client.py`

**Interfaces:**
- Consumes: `client.py` 的纯函数与叶子逻辑(不碰异步 WS 主循环)
- Produces: 行为锁定测试(后续 Task 8 拆分时的回归网)

- [ ] **Step 1: 锁定纯函数行为**

`tests/test_characterization_client.py`(框架,具体值按当前代码实读补齐):

```python
from hdata.client import round_result_token


def test_round_result_token_locks_current_mapping():
    assert round_result_token({"round": "A1", "result": [1, 2, 3]}) == "<当前实际输出>"


def test_table_info_from_snapshot_keeps_fields():
    from hdata.client import _table_info_from_snapshot  # 私有按需引用

    out = _table_info_from_snapshot({...样本...})
    assert set(out) >= {"table_id", "status", "round"}
```

(逐函数从 `client.py` 读当前实现、抄入样本输入与当前输出,任何断言值都是"现状",不是"期望"。)至少覆盖:`round_result_token`、`_table_info_from_snapshot`、事件分类(`103 NEW_BOOT/305 TABLEFAULT`)、`build_hall_switch_msg`。

- [ ] **Step 2: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_characterization_client.py -q
& .venv\Scripts\python.exe -m pytest -q
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_characterization_client.py
git commit -m "test(client): 为 client.py 纯函数建立特征测试网,锁定当前行为"
```

**验收:** 特征测试全绿;若有断言与现状不符,以现状为准修正断言(这是特征测试的纪律)。

---

## Task 8: 拆分 client.py

**Files:**
- Modify(模块转包): `git mv hdata/client.py hdata/client/__init__.py`(**pre-flight 修正**:模块与目录不能同名共存,否则 `import hdata.client` 解析二选一;转包后 `hdata.client` 名字完全不变)
- Create: `hdata/client/transport.py`、`hdata/client/tables.py`、`hdata/client/gateway.py`
- Modify: `hdata/client/__init__.py`(瘦身为门面 + re-export)
- Modify: `pyproject.toml`(`[tool.setuptools.packages.find]` include 已含 `hdata*`,无需改)

**Interfaces:**
- Consumes: `client.py` 现有 6 个类(`GameClient/_WSConnection/TableSession/MultiTableSession/TableMonitor/MultiplaySession`)与 `_gateway_request`
- Produces:
  - `hdata/client/transport.py`: `_WSConnection`(WS 传输 + 心跳/看门狗,原 741-1120)
  - `hdata/client/tables.py`: `TableSession/MultiTableSession/TableMonitor/MultiplaySession`(状态机)
  - `hdata/client/gateway.py`: `_gateway_request`(HMAC 加签,原 1972-2022)
  - `hdata/client.py`: `GameClient` 门面 + `from hdata.client.tables import ...` re-export,**保持 `hdata.client.<名字>` 全名可用**

**迁移纪律(只搬家不改逻辑):**
- 移动后类与函数体逐行一致;仅改 import。
- `client.py` 中跨类私有穿透(`shard._conn._session`、`target._tables` 等)本次**原样保留**(属 P4 处理),但每处加一行注释 `# NOTE: 跨对象私有访问,待 P4 收敛`。
- `_gateway_request` 硬编码 UA `"Chrome/149.0.0.0"` 与 `get_impersonate` 的矛盾本次只加注释,不改。

- [ ] **Step 1: 逐块搬运**

按 transport → gateway → tables 顺序搬,每搬完一块就跑 Task 7 特征测试 + 全套。

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_characterization_client.py tests/test_monitor_tables.py tests/test_refresh_cb.py tests/test_enter_pacer.py tests/test_shard_pacing.py tests/test_table_info.py -q
```

- [ ] **Step 2: 门面瘦身**

`client.py` 保留 `GameClient` 与其直接协作逻辑,其余改为 import。

- [ ] **Step 3: 验证 + Commit**

```powershell
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.client; print('smoke ok')"
```
```bash
git add -A
git commit -m "refactor(client): 拆分 2241 行门面为 transport/tables/gateway,行为逐行保留"
```

**验收:** `hdata.client` 全名引用全部可用;`client.py` 降至 <600 行;全套绿。

---

## Task 9: 拆分 TokenManager(632 行)

**pre-flight 修正(2026-08-04):** 用户选择**激进拆分到 <250 行**。方案:整类实现搬到 `login_orchestrator.py` 的 `LoginOrchestrator`,`TokenManager` 变薄为门面(仅构造 + public 方法委托 + 静态 `_decrypt_sign_table` 保留)。外部引用面(必须保留在 `TokenManager` 名上):构造签名 `(account="default", solver=None, user="", pwd="")`;方法 `get_token/diagnose/health/manual_capture/inject_tokens/import_token_file`;静态 `_decrypt_sign_table`(session.py:870、scripts/full_login.py:209、tests/test_sign_table.py:20 引用)。`_login_via_http` 迁入 `captcha_client.py` 作为模块级 `http_login_with_captcha(account, user, pwd, solver)`,为 P4 收敛 http_login 铺路。

**Files:**
- Create: `hdata/auth/login_orchestrator.py`(`LoginOrchestrator`,持有原 TokenManager 全部状态与方法)
- Create: `hdata/auth/captcha_client.py`(`http_login_with_captcha`)
- Modify: `hdata/auth/token_manager.py`(TokenManager 变薄门面)
- Test: `tests/test_token_lifecycle.py`、`tests/test_http_captcha_login.py`、`tests/test_browser_login_fallback.py`(既有)

**Interfaces:**
- Consumes: Task 4 叶子模块(`headers/sign_table/domain/params/captcha_solver/captcha/fingerprint`)
- Produces:
  - `LoginOrchestrator(account="default", solver=None, user="", pwd="")` — 原 TokenManager 全部实例方法原样迁移
  - `captcha_client.http_login_with_captcha(account, user, pwd, solver) -> dict | None` — 原 `_login_via_http` 方法体,`self.account` → 参数 `account`,`await self._resolve_domain()` → `resolve_domain() or os.getenv("HDATA_DOMAIN", None)`(等价)
  - `TokenManager` — 门面,内部持 `self._orch = LoginOrchestrator(...)`,public 方法一行委托

**迁移纪律:** 只搬家,行为不变。`_login_via_http` 方法体(原 499-599)逐行搬入 captcha_client,仅两处替换(self→参数)。其余全部方法(含 get_token L0-L4 链、diagnose、health、浏览器刷新三件套、缓存管理)原样搬入 LoginOrchestrator。CLI 的 `main()` 保持用 `TokenManager`。

- [ ] **Step 1: 创建 captcha_client.py**

把 `_login_via_http` 方法体(原 token_manager.py:499-599)搬为模块级函数 `http_login_with_captcha`,签名 `async def http_login_with_captcha(account: str, user: str, pwd: str, solver) -> dict | None`。imports:`curl_cffi`、`hashlib/urllib.parse/re`、`fetch_captcha`、`CaptchaChallenge`、`generate_w`、`get_impersonate`、`resolve_domain`、`logger`、`json/time`。函数内 `self.account` → `account`;`await self._resolve_domain()` → `resolve_domain() or os.getenv("HDATA_DOMAIN", None)`。

- [ ] **Step 2: 创建 login_orchestrator.py**

新建 `LoginOrchestrator` 类,构造签名 `(account="default", solver=None, user="", pwd="")`,body 为原 TokenManager `__init__` 原样。把原 TokenManager 全部方法(除 `__init__`、`_login_via_http`、静态 `_decrypt_sign_table`)原样搬入,`self._login_via_http(...)` 调用点改为 `from hdata.auth.captcha_client import http_login_with_captcha; await http_login_with_captcha(self.account, user, pwd, solver)`(get_token L3a 分支,原 token_manager.py:201)。保持 `# 编排回环` 注释。

- [ ] **Step 3: TokenManager 变薄门面**

`token_manager.py` 中 `class TokenManager` 替换为:

```python
class TokenManager:
    """多账号 Token 管理器门面 — 委托给 LoginOrchestrator。"""

    def __init__(self, account: str = "default",
                 solver=None, user: str = "", pwd: str = ""):
        self._orch = LoginOrchestrator(account, solver=solver, user=user, pwd=pwd)

    async def get_token(self, user: str = "", pwd: str = "") -> str:
        return await self._orch.get_token(user, pwd)

    def diagnose(self) -> dict:
        return self._orch.diagnose()

    def health(self) -> dict:
        return self._orch.health()

    async def manual_capture(self, entry_url: str = "https://leyu.me") -> str | None:
        return await self._orch.manual_capture(entry_url)

    def inject_tokens(self, game_token: str = "", game_player_id: int = 0,
                      game_backend: str = "", game_exp: int = 0,
                      source: str = "inject") -> dict:
        return self._orch.inject_tokens(game_token, game_player_id,
                                        game_backend, game_exp, source)

    def import_token_file(self, file_path: str) -> dict:
        return self._orch.import_token_file(file_path)

    @staticmethod
    def _decrypt_sign_table(b64: str) -> dict[str, str]:
        return decrypt_sign_table(b64)
```

`main()` CLI 不动(仍用 TokenManager)。

- [ ] **Step 4: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py tests/test_token_lifecycle.py tests/test_browser_login_fallback.py tests/test_sign_table.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth; from hdata.auth.token_manager import TokenManager; print(TokenManager._decrypt_sign_table is not None); print('smoke ok')"
```

Expected:相关测试全过;全套绿(仅已知预存在失败);TokenManager 门面可用。

- [ ] **Step 5: Commit**

```bash
git add hdata/auth/login_orchestrator.py hdata/auth/captcha_client.py hdata/auth/token_manager.py
git commit -m "refactor(auth): TokenManager 变薄门面,实现迁 LoginOrchestrator;HTTP 验证码登录迁 captcha_client(行为不变)"
```

**验收:** `TokenManager` 类 < 250 行(应为 ~100 行门面);`_login_via_http` 不再存在于 token_manager.py;`LoginOrchestrator` 持有全部原逻辑;全套绿。

**P3 checkpoint:** 全绿 + smoke ok。两大 God 文件已拆,私有穿透已标注。

---

# P4 — 收敛重复实现(风险 ★★★★,最高,需灰度)

## Task 10: 收敛登录流水线(降级版 → 完整版)

**pre-flight 修正(2026-08-04,Task 9 后结构已变):** 降级版现为 `captcha_client.http_login_with_captcha(account, user, pwd, solver)`,完整版为 `http_login.login(user, pwd, *, geepass_token, jfbym_token, max_retries, proxy)`。**用户决策:收敛到完整流水线**——`http_login_with_captcha` 从 solver 提取平台名与 token,委托 `http_login.login`,返回含 `uuid` 的完整 dict。

**solver→token 映射(已验证接口):** `JfbymSolver` 与 `GeepassSolver` 均持有 `self._token`(captcha_solver.py:202/377),`info().name` 返回 `"jfbym"`/`"geepass"`。映射规则:`name == "geepass"` → `geepass_token=solver._token`;否则 → `jfbym_token=solver._token`。

**Files:**
- Modify: `hdata/auth/captcha_client.py`(`http_login_with_captcha` 改为委托完整流水线)
- Modify: `hdata/auth/login_orchestrator.py`(L3a 调用点适配,若返回 dict 多 `uuid` 键无害)
- Test: `tests/test_http_captcha_login.py`(既有)

**Interfaces:**
- Consumes: `http_login.login(user, pwd, *, geepass_token="", jfbym_token="", max_retries=3, proxy="") -> Optional[dict]`(完整版,返回 `{token, uuid, domain, lot_number}`)
- Produces: `http_login_with_captcha(account, user, pwd, solver) -> dict | None` — 行为升级为完整流水线(含 kaptchcate 预注册、重试、域名失效切换、login_trace 埋点、user_ip/X-API-FINGER、uuid)

**行为差异决策(L3a 链路获得的新能力,均为完整版既有成熟逻辑,非新增代码):**
- D1 重试循环(max_retries=3): 完整版有 → 采纳
- D2 kaptchcate 预注册: 完整版有 → 采纳
- D3 域名 API 级失效自愈切换: 完整版有 → 采纳
- D4 login_trace 埋点: 完整版有 → 采纳
- D5 user_ip 计算 X-API-FINGER: 完整版有 → 采纳
- D6 返回含 uuid: 完整版有 → 采纳(L3a 的 `cache = session.copy()` 多存 uuid 键,`_save` 本就支持该字段)
- 上述均为"降级版缺的成熟补丁",收敛后 L3a 获得与 http_login 一致的能力;无反向行为损失。

- [ ] **Step 1: 重写 http_login_with_captcha**

`hdata/auth/captcha_client.py` 中 `http_login_with_captcha` 改为:

```python
async def http_login_with_captcha(account: str, user: str, pwd: str, solver) -> dict | None:
    """纯 HTTP 验证码登录 — 委托 http_login 完整流水线。

    从 solver 提取平台与 token 后调用 http_login.login(含 kaptchcate
    预注册/重试/域名失效切换/埋点/uuid)。返回完整 dict。
    """
    from hdata.auth.http_login import login as _login

    if solver is None:
        return None
    name = solver.info().name
    kwargs = (
        {"geepass_token": solver._token}
        if name == "geepass"
        else {"jfbym_token": solver._token}
    )
    return await _login(user, pwd, **kwargs)
```

若 `http_login.login` 的 `login_trace.bind(account=user, proxy=...)` 埋点上下文导致 L3a 调用方日志语义变化,确认无副作用后保留。删除 captcha_client 中不再使用的 imports(fetch_captcha/CaptchaChallenge/generate_w/get_impersonate/resolve_domain/json/os/time),保留 `get_logger` 或确认无引用后一并清理。

- [ ] **Step 2: 适配 L3a 调用点**

`login_orchestrator.py` get_token L3a 分支(现调用 `http_login_with_captcha(self.account, _user, _pwd, self._solver)`):返回 dict 现含 `uuid`,`cache = session.copy()` 直接继承,无需改动;确认 `_refresh_game_via_api(cache)` 对多出的 uuid 键无副作用(应为无)。

- [ ] **Step 3: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py tests/test_token_lifecycle.py tests/test_browser_login_fallback.py tests/test_import_smoke.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth; from hdata.auth.captcha_client import http_login_with_captcha; print('smoke ok')"
```

Expected:相关测试全过;全套绿(仅已知预存在失败);smoke ok。

- [ ] **Step 4: Commit**

```bash
git add hdata/auth/captcha_client.py hdata/auth/login_orchestrator.py
git commit -m "refactor(auth): 验证码登录收敛到 http_login 完整流水线(solver→token 委托),D1-D6 差异全部采纳"
```

**验收:** `http_login_with_captcha` 委托完整流水线,无重复流水线代码;`captcha_client.py` 无遗留未用 import;全套绿。

## Task 11: 弃用并删除 WSSource(生产未使用)

**pre-flight 修正(2026-08-04):** 经用户确认 + runlog 实证:`_customer_0804/runlog.txt` 显示生产 craw-bot(streak9)走 `hdata.client`(GameClient 门面)→ `_WSConnection` 链路,全程无 `WSSource`/`WSClient`/`CDPSource` 日志。**用户决策:弃用并删除 WSSource 相关文件**,保留 `_WSConnection`(生产在用)。`CDPSource` 保留不动(不在本次弃用范围)。

**Files:**
- Delete: `hdata/capture/direct_client.py`(`WSClient`,仅被 leyu_ws 使用)、`hdata/sources/leyu_ws.py`(`WSSource`)
- Modify: `hdata/sources/__init__.py`(去 WSSource import/__all__)
- Modify: `pyproject.toml`(删 `ws_source` entry-point,保留 `cdp_source`)
- Modify: `tests/test_sources.py`(删 TestWSSource 类 + 顶部 WSSource import)
- Modify: `tests/test_import_smoke.py`(两个 import 顺序测试原本用 `hdata.sources.leyu_ws`,改用具代表性的存活模块)

**Interfaces:**
- Consumes: 无
- Produces: 无(`WSSource`/`WSClient` 移出 hdata)

- [ ] **Step 1: 删文件 + 改引用**

1. 删除 `hdata/capture/direct_client.py`、`hdata/sources/leyu_ws.py`。
2. `hdata/sources/__init__.py`:删 `from .leyu_ws import WSSource`,`__all__` 只留 `["CDPSource"]`。
3. `pyproject.toml` `[project.entry-points."data_sources"]` 删 `ws_source` 行,保留 `cdp_source`。
4. `tests/test_sources.py`:删第 2 行 WSSource import、`class TestWSSource`(25-36 行)。
5. `tests/test_import_smoke.py`:原 `test_import_in_leaf_first_order`/`test_import_in_source_first_order` 用 `hdata.sources.leyu_ws`,改为用 `hdata.sources`(包级)与 `hdata.client` 两种顺序,仍验证 import 顺序无环。

- [ ] **Step 2: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_sources.py tests/test_import_smoke.py tests/test_monitor_tables.py tests/test_characterization_client.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.client; from hdata.sources import CDPSource; print('smoke ok')"
```

Expected:相关测试全过;全套绿(仅已知预存在失败);smoke ok。若出现因删 leyu_ws 导致的 ModuleNotFoundError,是漏改的引用,修复后重跑。

- [ ] **Step 3: Commit**

```bash
git add hdata/capture/direct_client.py hdata/sources/leyu_ws.py hdata/sources/__init__.py pyproject.toml tests/test_sources.py tests/test_import_smoke.py
git commit -m "refactor(sources): 弃用并删除生产未使用的 WSSource/WSClient(生产走 _WSConnection 链路),保留 CDPSource"
```

**验收:** `WSSource`/`WSClient`/`leyu_ws`/`direct_client` 全仓 0 引用;`hdata.sources` 仅导出 CDPSource;全套绿。

## Task 12: 收敛零散重复

**pre-flight 修正(2026-08-04,实现者 BLOCKED 复核确认):** 三处"时区偏移"**语义不等价,不合并**:
- `http_login.py:302` `_local_tz_offset` 用 `datetime.now().astimezone().utcoffset()`(动态,DST 正确)
- `_shared.py:111` / `codec.py:246` 用 `-time.timezone // 60 if time.daylight == 0 else -time.altzone // 60`(静态,取 altzone 当 DST"可能",非"实际")
- DST 时区在标准时两者不等 → 合并即行为改变。**标注为"有意保留的差异"**,不改。
- 只收敛两处安全项:参数提取(byte-equivalent)与 WS URL 唯一实现确认。

**Files:**
- Modify: `hdata/auth/browser_login.py:586-600`(删复制体,改调 `params.extract_params_from_url`)
- Verify: `hdata/auth/session.py` `build_ws_config`(确认 WS URL 唯一实现)
- No change: 时区偏移 3 处(语义不等,保留差异)

- [ ] **Step 1: browser_login 删复制体**

`browser_login.py:586-600` `_extract_params_ttl_from_url` 与 `params.extract_params_from_url`(params.py:159-181)逐行等价(含空 url 处理)。保留方法名(3 处调用 :377/:398/:480),改为薄委托:

```python
@staticmethod
def _extract_params_ttl_from_url(url: str) -> tuple[str, str]:
    """从 URL 提取 params/ttl(委托 params.extract_params_from_url)。"""
    from hdata.auth.params import extract_params_from_url

    return extract_params_from_url(url)
```

若 `unquote` 因此不再被 browser_login 使用,一并清理 import。

- [ ] **Step 2: 确认 WS URL 唯一实现**

`Select-String -Path hdata\**\*.py -Pattern "wss://wsproxy"` 应仅命中 session.py(或其调用的辅助)。leyu_ws.py 已删,无重复。无需改动。

- [ ] **Step 3: 验证 + Commit**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_browser_login_fallback.py tests/test_roadpaper.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth, hdata.client; print('smoke ok')"
```
```bash
git add hdata/auth/browser_login.py
git commit -m "refactor: 收敛参数提取复制体(时区偏移三处语义不等,保留差异不合并)"
```

**验收:** `_extract_params_ttl_from_url` 为薄委托;`wss://wsproxy` 仅 session.py 1 处;全套绿。

**P4 checkpoint:** 全绿 + smoke ok。重复实现收敛完成(可合并项已合并,不可合并项已记录差异)。

**P4 checkpoint:** 全绿 + smoke ok。重复实现收敛完成,差异决策记录在 commit history。

---

# P5 — 质量门禁与残留清理(风险 ★)

## Task 13: ruff/mypy/coverage 配置 + CI

**pre-flight 修正(2026-08-04):**
1. **发现 Task 8 引入的真实 bug**:`hdata/client/transport.py:207/209/210` 在 `_WSConnection._login` 中 `raise LoginError(...)` 但**从未 import `LoginError`**——这是 Task 8 拆分时漏掉的 import。测试全绿是因为这些路径只在真实 WS 登录被拒/被踢时触发(集成测试打不到)。**必须先补 import**(`from hdata.auth.session import LoginError` 或从现有 `build_ws_config` import 行合并),否则登录被拒时是 NameError 而非 LoginError。
2. **uv 环境**:venv 无 pip,ruff 用 `uv pip install --python .venv\Scripts\python.exe ruff` 安装(已装 0.16.1)。
3. **lint 规模实测**:357 个错误,89 个可自动修复(unsorted-imports/quoted-annotation 等无行为影响),268 个需人工/noqa。计划 `select = ["E","F","W","I","UP","B","SIM"]` 且 `--fix` 仅修 89 个自动项,不追求 0 error(否则 268 个手动项会让本任务失控)。
4. F821 的 4 处中 2 处是真实误报(schemacodec.py:397 字符串注解 `"Path | None"` 引用了从未 import 的 Path——是注解字符串,延迟求值,实际运行不取用它?——**需实现者确认**:若 `_override_cache_path` 返回类型注解导致运行时求值会 NameError;若仅注释引用则无害)。**只修 transport.py 的 LoginError,其他误报记 noqa 或确认后忽略。**

**Files:**
- Fix: `hdata/client/transport.py`(补 LoginError import,真实 bug)
- Modify: `pyproject.toml`(ruff/coverage 配置;ruff 加入 `[dependency-groups].dev`)
- Create: `.github/workflows/ci.yml`
- Verify: `hdata/protocol/schemacodec.py:397`(Path 注解确认)

- [ ] **Step 1: 修 transport.py LoginError 真实 bug**

`transport.py` 顶部 `from hdata.auth.session import build_ws_config` 处补 `LoginError`:
```python
from hdata.auth.session import LoginError, build_ws_config
```
确认无其他引用问题。

- [ ] **Step 2: 确认 schemacodec Path 注解**

`schemacodec.py:397` `_SCHEMA_OVERRIDE_CACHE: "Path | None" = None`。因模块有 `from __future__ import annotations`(schemacodec.py:25),字符串注解**不会**在运行时求值,F821 是误报。确认后不动,或在配置里 `extend-ignore = ["F821"]` 处理(若 E/F 全开)。

- [ ] **Step 3: pyproject 配置**

```toml
[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]

[tool.coverage.run]
source = ["hdata"]
omit = ["hdata/protocol/_schema_data.py"]
```
`[dependency-groups].dev` 加 `"ruff>=0.16.1"`。

- [ ] **Step 4: 一次性降噪(仅自动项)**

```powershell
& .venv\Scripts\python.exe -m ruff check hdata tests --fix
```
Expected:修掉 ~89 个自动项。**不追求 0 error**——剩余手动项记录在 ledger,不以 noqa 硬塞(否则语义噪声)。若 `--fix` 引入行为变化,回退并手动处理。

- [ ] **Step 5: CI**

`.github/workflows/ci.yml`:

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --group dev
      - run: uv run pytest -q
```

- [ ] **Step 6: 验证 + Commit**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_characterization_client.py tests/test_monitor_tables.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -m ruff check hdata tests  # 记录剩余数量,不要求 0
```
```bash
git add hdata/client/transport.py pyproject.toml .github/workflows/ci.yml
git commit -m "fix(client): 补 _WSConnection 缺失的 LoginError import(Task 8 拆分遗留真实 bug);chore: 接入 ruff/coverage 配置与 CI"
```

**验收:** `LoginError` 在 transport.py 有 import;全套绿;ruff 自动修复后无回归;剩余 lint 项记录在 ledger。

**验收:** `ruff check` 0 error;全套绿;`.github/workflows/ci.yml` 已提交。

## Task 14: 清理 git 垃圾与残留目录

**pre-flight 修正(2026-08-04,实测核实):**
1. `data/` 目录内容实为 **streak 爬虫运行时数据库**(`streak.db` 1.36GB、`proxy_test.db`、`streak_hunter.log` 等),已被 .gitignore 忽略,**非 git 垃圾**,删除会破坏 streak 数据。**不删。**
2. `probes/` 目录磁盘上为空(仅 git 跟踪 `profile_sqlite_types.py` 一个文件)。
3. 两个 wasm 副本 hash 相同(`4CDA85DA...`),保留 `hdata/auth` 那份,删 `scripts/wasm_api_sign_bg.wasm`。
4. git 跟踪垃圾确认:`.reasonix/`(2 文件,AI 研究残留)、`scripts/wasm_api_sign_bg.wasm`(重复副本)。
5. `viewer/` 磁盘上为空目录(git 不跟踪空目录,无需处理)。

**Files:**
- Modify: `.gitignore`(补 `dist_pyd/`、`probes/`、`.reasonix/`)
- Delete(git 跟踪): `.reasonix/`(2 文件)、`scripts/wasm_api_sign_bg.wasm`(重复副本,hash 已核一致)
- Move(git 跟踪): `probes/profile_sqlite_types.py` → `scripts/`(迁移后再删 probes 跟踪)
- No change: `data/`(运行时数据库,保留)

- [ ] **Step 1: .gitignore 补规则**

```gitignore
dist_pyd/
probes/
.reasonix/
```

- [ ] **Step 2: 迁移 probes/profile_sqlite_types.py 到 scripts/**

```powershell
git mv probes/profile_sqlite_types.py scripts/profile_sqlite_types.py
```

- [ ] **Step 3: 删除 git 跟踪垃圾**

```powershell
git rm -r --cached .reasonix
git rm scripts/wasm_api_sign_bg.wasm
git status --short
```

- [ ] **Step 4: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest -q
```

- [ ] **Step 5: Commit**

```bash
git commit -m "chore: 清理 git 跟踪垃圾(.reasonix/重复 wasm);probes 探测脚本迁 scripts;data 运行时库保留"
```

**验收:** `git status` 干净;`git ls-files` 无 `.wasm` 重复、无 `.reasonix`、无 `probes/`;全套绿。

**P5 checkpoint:** 全绿;CI 就绪。

---

## 执行顺序总览(依赖链)

```
P1(T1→T2→T3) → P2(T4→T5→T6) → P3(T7→T8→T9) → P4(T10→T11→T12) → P5(T13→T14)
```

- P1/P2 可在一个会话内连续执行(低风险)。
- 每个 T 的 commit 是独立 reviewable 单元。
- P4 的 T10 若遇到 `D?` 未知差异,允许暂停提交,把差异清单交给人工决策后再继续。

## 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 拆 schema 导致热更新写入新 dict 语义漂移 | 低 | 解码错乱 | 保留 `_schema_data.py` 所有权 + round-trip assert + 23 个既有测试 |
| 提升懒 import 触发真循环 | 中 | import 死锁 | 每处提升即冒烟;失败回退并标注编排回环 |
| client.py 拆分破坏 `hdata.client.*` 全名引用 | 中 | 外部调用方崩 | 门面 re-export + 特征测试网 |
| P4 合并丢失分叉补丁 | 高 | 登录/收帧回归 | 逐差异 D<n> 决策、独立提交、灰度验证 |
| 活体服务器侧行为漂移(签名/验证码) | 高 | 账号静默 | 只机械抽取、enable_wasm 参数保差异、不重写 live 链路 |
