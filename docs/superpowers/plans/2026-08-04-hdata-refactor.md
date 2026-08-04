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
- Consumes: `session: dict`(键: `signatures`/`uuidToBase64`/`token`/`domain`/`cookies`)
- Produces:
  - `headers.build_api_headers(session: dict, url: str, *, enable_wasm: bool = True) -> dict` — 返回结构含 `X-API-TOKEN/X-API-UUID/X-API-XXX/X-API-CLIENT/X-API-SITE/X-API-VERSION/Content-Type/Referer/User-Agent/Cookie`
  - `sign_table.decrypt_sign_table(uuid_b64: str) -> dict`
  - `TokenManager._decrypt_sign_table` 改为委托别名(保留下划线签名,不动 `session.py:369` 调用)

**行为差异(必须用参数保留,不得顺手合并):** `session.py` 版签名链是 3 层(wasm→手动表→uuidToBase64);`token_manager.py` 版只有 2 层(无 wasm)。Task 4 只做机械抽取:`session` 调 `enable_wasm=True`,`token_manager` 调 `enable_wasm=False`。wasm 差异是否合并留给 Task 10 决策。

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

`hdata/auth/headers.py`:把 `session.py:335-388` `_api_headers` 全文搬入并改签名 `def build_api_headers(session, url, *, enable_wasm=True)`,`enable_wasm` 包住 wasm 分支(`session.py:344-351`);`_device_uuid_for`/`_ua_for` 一并搬入为私有助手。`session.py` 中替换为 `from hdata.auth.headers import build_api_headers` 并在 `_api_headers = build_api_headers` 保留旧名(兼容内部调用)。

`hdata/auth/sign_table.py`:把 `TokenManager._decrypt_sign_table` 方法体搬入为 `decrypt_sign_table(uuid_b64) -> dict`。`token_manager.py` 中:

```python
from hdata.auth.sign_table import decrypt_sign_table
...
class TokenManager:
    @staticmethod
    def _decrypt_sign_table(uuid_b64: str) -> dict:
        return decrypt_sign_table(uuid_b64)
```

`token_manager.py:705` `_api_headers` 改为:保留原 2 层逻辑但改调 `build_api_headers(..., enable_wasm=False)`;若其内部用了 `_decrypt_sign_table`,改调 `decrypt_sign_table`。**该文件本次除"换调用点"外不做任何逻辑改动。**

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
git commit -m "refactor(auth): 抽取 build_api_headers/decrypt_sign_table 为叶子模块,enable_wasm 参数保留两调用方行为差异"
```

**验收:** `Select-String -Path ... -Pattern "def _api_headers"` 0 处(只剩别名赋值);`TokenManager._decrypt_sign_table` 为一行委托;全套绿。

---

## Task 5: 收敛域名解析

**Files:**
- Modify: `hdata/auth/domain.py`(唯一实现,已有 `resolve_domain`)
- Modify: `hdata/auth/http_login.py:85-95` `_get_domain`、`hdata/auth/token_manager.py:600-610` `_resolve_domain`、`hdata/auth/session.py:199-231` `get_real_domain`(改为委托)

**Interfaces:**
- Consumes: `domain.resolve_domain(url=None) -> str`(既有)
- Produces: 各模块保留原函数名,内部一行委托:`return resolve_domain(...)`

- [ ] **Step 1: 写委托并保留兜底语义**

`http_login.py`:

```python
def _get_domain(url: str = "") -> str:
    return resolve_domain(url or "https://leyu.me")
```

`token_manager._resolve_domain` 与 `session.get_real_domain` 同理改为一行委托。**注意**:若原实现含 `HDATA_DOMAIN` 覆盖逻辑,确认 `resolve_domain` 已支持(否则在 `domain.py` 补 `HDATA_DOMAIN` 分支并加注释,行为不变)。

- [ ] **Step 2: 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py tests/test_token_lifecycle.py -q
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth; print('smoke ok')"
```

- [ ] **Step 3: Commit**

```bash
git add hdata/auth/domain.py hdata/auth/http_login.py hdata/auth/token_manager.py hdata/auth/session.py
git commit -m "refactor(auth): 域名解析收敛为 domain.resolve_domain 唯一实现,其余改委托"
```

**验收:** `Select-String ... "def _get_domain|def _resolve_domain|def get_real_domain"` 每处函数体 ≤3 行;全套绿。

---

## Task 6: 拆除 auth 循环依赖(懒 import → 模块级)

**Files:**
- Modify: `hdata/auth/token_manager.py`、`hdata/auth/session.py`、`hdata/auth/http_login.py`、`hdata/auth/browser_login.py`
- Test: `tests/test_import_smoke.py`

**Interfaces:**
- Consumes: Task 4/5 产出的叶子模块
- Produces: 无(纯 import 结构调整)

**目标:** 凡是在 Task 4/5 之后只依赖叶子模块(`headers`/`sign_table`/`domain`/`api_sign`/`fingerprint`/`params`)的函数体懒 import,一律提升为模块级 import。**编排类循环**(`session._get_login_inner` 懒加载 `http_login`/`browser_login` 做降级;`token_manager` 懒加载 `session`)属于"编排回调",本轮不动,只在原处补注释:`# 编排回环:保持函数内导入以避免 import 死锁,见 P4 拆 orchestrator 方案`。

- [ ] **Step 1: 逐个提升懒 import**

对 `token_manager.py:114/:155/:366/:386/:503-505/:602/:731-740`、`session.py:278/:310/:324/:345/:366/:948`、`http_login.py:50`、`browser_login.py:49` 中属于叶子依赖的,提到模块级并删掉函数内 `import`。每改一处立刻跑:

```powershell
& .venv\Scripts\python.exe -c "import hdata.auth, hdata.client; print('ok')"
```

若出现 `ImportError`,回退该处(说明仍是环),标注为编排回环。

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
git add -A
git commit -m "refactor(auth): 叶子依赖懒 import 提升为模块级,编排回环标注留 P4;新增导入顺序冒烟测试"
```

**验收:** 全部 3 个冒烟测试过;`Select-String -Path ... -Pattern "from hdata.auth import"` 仅剩编排回环注释处;全套绿。

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

## Task 9: 拆分 TokenManager(666 行)

**Files:**
- Create: `hdata/auth/captcha_client.py`(验证码求解封装)、`hdata/auth/login_orchestrator.py`(登录编排壳,可选)
- Modify: `hdata/auth/token_manager.py`

**Interfaces:**
- Consumes: Task 4 叶子模块
- Produces: `TokenManager` 瘦身为 token 生命周期 + 委托;验证码逻辑移入 `captcha_client.py`

**迁移纪律:** 只搬家。`get_token` 的 L0-L4 降级链逻辑逐行搬入 `login_orchestrator`(若做),`TokenManager` 保留 public 方法签名并委托。

- [ ] **Step 1-3: 搬验证码 + 搬降级链 + 瘦身(与 Task 8 同节奏)**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py tests/test_token_lifecycle.py tests/test_browser_login_fallback.py -q
& .venv\Scripts\python.exe -m pytest -q
```
```bash
git commit -m "refactor(auth): TokenManager 拆分验证码求解与登录编排,降级链行为不变"
```

**验收:** `TokenManager` < 250 行;全套绿。

**P3 checkpoint:** 全绿 + smoke ok。两大 God 文件已拆,私有穿透已标注。

---

# P4 — 收敛重复实现(风险 ★★★★,最高,需灰度)

## Task 10: 收敛登录流水线 + 域名 + 请求头

**Files:**
- Modify: `hdata/auth/http_login.py`、`hdata/auth/token_manager.py`、`hdata/auth/session.py`

**方法:** 每个差异独立合并。以 `http_login.py:510-714` 为基准,把 `token_manager.py:495-598 _login_via_http` 逐段 diff:
- 逐行列出差异(如 `_login_via_http` 缺 kaptchcate 预注册、缺 login_trace 埋点、缺域名切换)。
- 每个差异**单独决策**:是"有意降级"还是"漏补丁"。有意降级 → 加显式注释;漏补丁 → 补回并加对应测试。

- [ ] **Step 1: 产出两套流水线 diff 清单**

```powershell
& .venv\Scripts\python.exe -c "from hdata.auth.token_manager import TokenManager; import inspect; print(inspect.getsource(TokenManager._login_via_http))" > _tm_login.txt
& .venv\Scripts\python.exe -c "import inspect; from hdata.auth import http_login; print(inspect.getsource(http_login.login))" > _hl_login.txt
```
人工核对两份文本,把每个差异记为 `D<n>`(有意/漏补丁/未知)。

- [ ] **Step 2: 合并**

`token_manager._login_via_http` 改为调用 `http_login` 的公共流水线(按 `D<n>` 清单补齐差异),删除复制体。未知差异 `D?` 一律**保留不动**,记 TODO。

- [ ] **Step 3: 验证 + Commit**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py tests/test_token_lifecycle.py -q
& .venv\Scripts\python.exe -m pytest -q
```
```bash
git commit -m "refactor(auth): 登录流水线收敛到 http_login 单一实现,逐差异决策 D1..Dn"
```

**验收:** `_login_via_http` 不再存在;差异清单随 commit message 提交;全套绿。

## Task 11: 收敛 WS 客户端

**Files:**
- Modify: `hdata/capture/direct_client.py`、`hdata/client/transport.py`

**方法:** `transport._WSConnection` 与 `capture/direct_client.WSClient` 做同样 diff-merge。两者都是 `websockets.connect + encode_frame/decode_frame + 心跳`。把公共收发核心抽为一个私有函数供两处调用;心跳/看门狗差异(时间窗口不同)保留为参数。

- [ ] **Step 1-3: diff → 抽公共核心 → 验证**

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_sources.py tests/test_monitor_tables.py tests/test_characterization_client.py -q
& .venv\Scripts\python.exe -m pytest -q
```
```bash
git commit -m "refactor(ws): 两套 WS 客户端抽公共收发核心,心跳窗口参数化"
```

**验收:** `transport.py` 与 `direct_client.py` 各自 ≤150 行收发相关;全套绿。

## Task 12: 收敛零散重复

**Files:**
- Modify: `hdata/auth/http_login.py:302-308`、`hdata/protocol/codec.py:246`、`hdata/client.py:206`(时区偏移 3 处合一)
- Modify: `hdata/auth/browser_login.py:587-600` → 复用 `params.extract_params_from_url`
- Modify: `hdata/auth/session.py:621-672` / `hdata/sources/leyu_ws.py:192-228`(WS URL 拼接归一,抽 `build_ws_url`)

- [ ] **Step 1: 抽 `hdata/auth/params.py::local_tz_offset()`**,三处改为调用(逐字节相同才合并)。
- [ ] **Step 2: browser_login 删复制体**,改调 `extract_params_from_url`。
- [ ] **Step 3: 抽 `build_ws_url(...)`**(含 `WS_STATIC_KEY_SUFFIX`),两处改调。
- [ ] **Step 4: 验证 + Commit**

```powershell
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -c "import hdata.auth, hdata.client, hdata.sources.leyu_ws; print('smoke ok')"
```
```bash
git commit -m "refactor: 收敛时区偏移/参数提取/WS URL 拼接三组重复实现"
```

**验收:** `Select-String ... "-time.timezone // 60"` 1 处;`"wss://wsproxy"` 拼接仅 1 处;全套绿。

**P4 checkpoint:** 全绿 + smoke ok。重复实现收敛完成,差异决策记录在 commit history。

---

# P5 — 质量门禁与残留清理(风险 ★)

## Task 13: ruff/mypy/coverage 配置 + CI

**Files:**
- Modify: `pyproject.toml`、Create: `.github/workflows/ci.yml`、`.mypy.ini`(或并入 pyproject)

- [ ] **Step 1: pyproject 加配置**

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

- [ ] **Step 2: 一次性降噪**

```powershell
& .venv\Scripts\python.exe -m pip install ruff mypy
& .venv\Scripts\python.exe -m ruff check hdata tests --fix
```
(仅修自动可修项;手动项以 `# noqa` 标注并附原因。)

- [ ] **Step 3: CI**

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

- [ ] **Step 4: 验证 + Commit**

```powershell
& .venv\Scripts\python.exe -m ruff check hdata tests
& .venv\Scripts\python.exe -m pytest -q
```
```bash
git commit -m "chore: 接入 ruff/mypy/coverage 配置与 CI,存量错误一次性降噪"
```

**验收:** `ruff check` 0 error;全套绿;`.github/workflows/ci.yml` 已提交。

## Task 14: 清理 git 垃圾与残留目录

**Files:**
- Modify: `.gitignore`
- Delete(git 跟踪): `.reasonix/`、`hdata/auth/wasm_api_sign_bg.wasm` 的重复副本、`scripts/wasm_api_sign_bg.wasm`(仅当源码树与 auth 内同名文件 hash 一致时,保留 `hdata/auth` 那份)
- Delete(磁盘残留): `data/`(数百 MB Chrome profile)、`dist_pyd/`、`viewer/`(空目录)、`probes/` 一次性脚本(保留 `probes/profile_sqlite_types.py` 迁移到 `scripts/` 再删)

- [ ] **Step 1: 核对 wasm 副本 hash**

```powershell
Get-FileHash hdata/auth/wasm_api_sign_bg.wasm, scripts/wasm_api_sign_bg.wasm | Select-Object Path, Hash
```

- [ ] **Step 2: .gitignore 补规则**

```gitignore
dist_pyd/
probes/
.reasonix/
```

- [ ] **Step 3: 删除并验证**

```powershell
git rm -r --cached .reasonix
git rm scripts/wasm_api_sign_bg.wasm   # 仅当 hash 相同
git status --short
& .venv\Scripts\python.exe -m pytest -q
```

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: 清理 git 跟踪垃圾与工作区残留(profile/dist_pyd/一次性脚本)"
```

**验收:** `git status` 干净;`git ls-files` 无 `.wasm` 重复、无 `.reasonix`;全套绿。

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
