#!/usr/bin/env python3
"""WS 连接测试脚本。

要求:
  1. 先运行 --manual-capture 获取 token 并保存到缓存
  2. 本脚本读取缓存的 token/backend/player_id
  3. 连接 WS 并接收消息

用法:
    uv run python test_ws.py --account default --table-id 2718
"""

import asyncio
import argparse
import sys
from pathlib import Path

# 添加项目路径
_PROJ_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJ_ROOT))

from hdata.auth.token_manager import TokenManager
from hdata.sources.leyu_ws import WSSource
from htools.utils.logger import setup_logging, get_logger

logger = get_logger(__name__)


async def test_ws(account: str, table_id: int, max_messages: int = 5):
    """测试 WS 连接。
    
    Args:
        account: 账号名（对应缓存文件）
        table_id: 桌台 ID（0 表示仅监听，不指定桌台）
        max_messages: 最多接收消息数
    """
    setup_logging()
    
    print("\n" + "=" * 70)
    print(f"  WS 连接测试")
    print(f"  Account: {account}")
    print(f"  Table ID: {table_id if table_id > 0 else '(监听所有桌台)'}")
    print("=" * 70 + "\n")

    tm = TokenManager(account=account)
    session = tm._load() or {}
    print("缓存检查:")
    print(f"  has game_token: {'yes' if bool(session.get('game_token')) else 'no'}")
    print(f"  game_player_id: {session.get('game_player_id', 0)}")
    print(f"  game_backend: {session.get('game_backend', '') or '(empty)'}")
    print(f"  backend: {session.get('backend', '') or '(empty)'}")
    print(f"  domain: {session.get('domain', '') or '(empty)'}")
    print()
    
    src = WSSource(table_id=table_id, account=account)
    
    try:
        count = 0
        async for tick in src.start():
            count += 1
            print(f"✅ 消息 #{count}:")
            print(f"   Counter ID: {tick.counter_id}")
            print(f"   Side: {tick.side}")
            print(f"   Trade Seq: {tick.trade_seq}")
            print(f"   Status: {tick.status}")
            print(f"   Confidence: {tick.confidence}")
            print(f"   Timestamp: {tick.timestamp}")
            print()
            
            if count >= max_messages:
                print(f"已接收 {max_messages} 条消息，测试完成")
                break
        
        if count > 0:
            print(f"\n✅ 成功接收 {count} 条消息")
            return 0
        else:
            print("\n⚠️ 未接收到任何消息（可能是表桌无活动）")
            return 1
        
    except Exception as e:
        print(f"\n❌ WS 连接失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await src.stop()


async def main():
    parser = argparse.ArgumentParser(description="WS 连接测试")
    parser.add_argument("--account", default="default", help="账号名")
    parser.add_argument("--table-id", type=int, default=2718, help="桌台 ID")
    parser.add_argument("--max-messages", type=int, default=5, help="最多接收消息数")
    args = parser.parse_args()
    
    return await test_ws(args.account, args.table_id, args.max_messages)


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
