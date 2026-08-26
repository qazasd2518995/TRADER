"""Interactive Windows setup helper for the local LINE database.

This module never prints the database key or message content.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

from .discovery import DatabaseCandidate, discover_windows_databases
from .keys import default_key_provider, validate_database_key
from .sqlite_provider import SQLiteLineDatabaseProvider
from .windows_credentials import delete_generic_credential, write_generic_credential


def _candidates(explicit: str) -> list[DatabaseCandidate]:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"找不到資料庫：{path}")
        return [DatabaseCandidate(path, "explicit")]
    return discover_windows_databases()


def _print_candidates(values: list[DatabaseCandidate]) -> None:
    if not values:
        print("找不到 LINE .edb；請確認 Windows LINE 已登入並至少開啟過聊天室。")
        print(r"先手動查看：%LOCALAPPDATA%\LINE\Data\db")
        return
    print(f"找到 {len(values)} 個候選資料庫：")
    for index, candidate in enumerate(values, 1):
        info = candidate.public_dict()
        print(
            f"  {index}. {info['path']}\n"
            f"     來源={info['source']} 大小={info['size'] / 1024 / 1024:.1f} MB"
        )


def command_find(args) -> int:
    values = _candidates(args.database)
    _print_candidates(values)
    return 0 if values else 2


def command_store_key(args) -> int:
    if sys.platform != "win32":
        raise RuntimeError("store-key 只能在 Windows 執行")
    first = validate_database_key(getpass.getpass("貼上 32 位十六進位 LINE DB key（畫面不會顯示）："))
    second = validate_database_key(getpass.getpass("再輸入一次確認："))
    if first != second:
        raise RuntimeError("兩次輸入的 key 不一致")
    write_generic_credential(args.service, first)
    print(f"已存入目前使用者的 Windows Credential Manager：{args.service}")
    print("金鑰沒有寫入專案、設定 JSON 或命令列歷史。")
    return 0


def command_delete_key(args) -> int:
    if sys.platform != "win32":
        raise RuntimeError("delete-key 只能在 Windows 執行")
    removed = delete_generic_credential(args.service)
    print("已刪除安全金鑰。" if removed else "Credential Manager 中沒有這個金鑰。")
    return 0


def command_verify(args) -> int:
    values = _candidates(args.database)
    if not values:
        _print_candidates(values)
        return 2
    key_provider = default_key_provider(args.service)
    key_provider.get_key()  # fail once with a clear, non-secret credential error
    good: list[Path] = []
    for candidate in values:
        provider = SQLiteLineDatabaseProvider(candidate.path, key_provider)
        try:
            connection = provider.connect()
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            message_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info('_message')")
            } if "_message" in tables else set()
            required_message_columns = {
                "_id", "_chatId", "_createdTime", "_from", "_text",
                "_contentType", "_messageRelationType", "_relatedMessageId",
                "_rev", "_status", "_type", "_attribute", "_eventInfo",
                "_contentMetadata", "_reactionStatus",
            }
            required = (
                required_message_columns <= message_columns
                and bool({"_squareChat", "_groupChat"} & tables)
            )
            if not required:
                print(f"[略過] {candidate.path}：可開啟，但不是支援的 LINE 訊息 schema")
                continue
            integrity = provider.integrity_check()
            if integrity != "ok":
                print(f"[失敗] {candidate.path}：integrity={integrity}")
                continue
            good.append(candidate.path)
            print(f"[OK] {candidate.path}：解密、完整性與訊息 schema 均通過")
        except Exception as exc:
            print(f"[失敗] {candidate.path}：{type(exc).__name__}（key／codec／檔案可能不符）")
        finally:
            provider.close()

    if len(good) == 1:
        print("\n請把以下路徑填入 Web 控制台的「加密資料庫路徑」：")
        print(good[0])
        return 0
    if len(good) > 1:
        print("\n有多個可用資料庫；請依 LINE 的實際帳號與檔案更新時間選擇。")
        return 3
    print("\n沒有候選資料庫通過。請依 docs/windows-line-database.md 的版本紀錄表排查。")
    return 4


def main() -> int:
    parser = argparse.ArgumentParser(description="TRADER Windows LINE DB 設定工具")
    parser.add_argument("--service", default="line-db-research", help="Credential Manager target")
    parser.add_argument("--database", default="", help="明確指定 .edb 路徑")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("find", help="搜尋已知 LINE Desktop 資料目錄").set_defaults(run=command_find)
    subparsers.add_parser("store-key", help="互動式存入 Windows Credential Manager").set_defaults(run=command_store_key)
    subparsers.add_parser("delete-key", help="刪除 Credential Manager 中的 key").set_defaults(run=command_delete_key)
    subparsers.add_parser("verify", help="逐一驗證候選 DB，不輸出聊天內容").set_defaults(run=command_verify)
    args = parser.parse_args()
    try:
        return int(args.run(args))
    except Exception as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
