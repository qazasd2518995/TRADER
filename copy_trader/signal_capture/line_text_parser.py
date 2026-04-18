"""
LINE Desktop 剪貼板文字解析器

依使用者實測 (LINE Desktop，繁中版) 的實際複製格式切分訊息。

實測格式範例：
    2026.04.16 星期四
    10:45 乘   乘XAUUSD 黃金

    BUY ：4815
    止損：4806
    止盈 : 4828

    （純粹個人投資分享）
    12:21 Y/944873加入聊天
    12:21 Auto-reply 進來的朋友
    記得到記事本看開單策略
    還有其他報單群可以加入🔥
    2026.04.17 星期五
    09:33 乘   乘XAUUSD 黃金
    ...

規則：
  1. 日期分隔行：`YYYY.MM.DD 星期X` 或 `YYYY/MM/DD`、`YYYY-MM-DD` →
     只更新 current_date，不開新訊息。
  2. 訊息首行：`HH:MM ` 開頭（緊接空白）然後是「發送者 ... 第一行內容」。
     發送者與內容之間通常多個空白；若整行只有發送者也成立。
  3. 後續行（不以 `HH:MM ` 開頭、也不是日期）一律歸屬前一則訊息。
  4. 空白行：保留為訊息內部換行，不用來切訊息。
  5. 「加入聊天」「Auto-reply」等系統訊息依然會被切出來，
     由下游用 is_system 旗標過濾掉。

本 parser 只做「切訊息」，不解析交易內容 — 那交給 RegexSignalParser。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import List, Optional, Set, Tuple


# -------------------- 正則 --------------------

# 2026.04.16 星期四 / 2026/04/16 / 2026-04-16 / 2026年4月16日
_RE_DATE_LINE = re.compile(
    r'^\s*(?P<y>\d{4})[./\-年](?P<mo>\d{1,2})[./\-月](?P<d>\d{1,2})日?'
    r'(?:\s+星期[一二三四五六日天])?\s*$'
)

# HH:MM 開頭，後面跟「空白 + 任意文字」或「僅 HH:MM」
_RE_MSG_HEAD = re.compile(
    r'^(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s+(?P<rest>.*))?$'
)

# 系統訊息特徵（用來設置 is_system 旗標）
_SYSTEM_PATTERNS = [
    re.compile(r'.+加入聊天\s*$'),
    re.compile(r'^Auto[- ]?reply\b', re.IGNORECASE),
    re.compile(r'^.+離開聊天\s*$'),
    re.compile(r'^.+邀請\s+.+\s+加入群組\s*$'),
    re.compile(r'^您已通話\s'),
    re.compile(r'^通話時間\s'),
]

# LINE 匯出時可能出現的多餘頭尾（中文版）
_EXPORT_NOISE = [
    re.compile(r'^\[LINE\]'),
    re.compile(r'^保存日期[:：]'),
    re.compile(r'^Saved on[:：]', re.IGNORECASE),
]


# -------------------- 資料結構 --------------------

@dataclass
class LineMessage:
    index: int                        # 批次內序號
    time_str: str                     # "HH:MM"
    timestamp: Optional[datetime]     # 以 current_date + time_str 組成
    sender: str                       # 發送者（可能為空；"乘" 這類就是 sender）
    body: str                         # 多行內容（含空白行合併後）
    is_system: bool = False           # 加入聊天 / Auto-reply 等
    raw_lines: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str, str]:
        """去重 key — 以 timestamp + sender + body 前 120 字。"""
        ts = self.timestamp.isoformat() if self.timestamp else ''
        return (ts, self.sender, (self.body or '')[:120])


@dataclass
class ParseResult:
    messages: List[LineMessage]
    current_date: Optional[date]
    raw_len: int
    raw_line_count: int

    @property
    def non_system_messages(self) -> List[LineMessage]:
        return [m for m in self.messages if not m.is_system]


# -------------------- 工具 --------------------

def _match_date_line(line: str) -> Optional[date]:
    m = _RE_DATE_LINE.match(line)
    if not m:
        return None
    try:
        return date(int(m.group('y')), int(m.group('mo')), int(m.group('d')))
    except ValueError:
        return None


def _is_export_noise(line: str) -> bool:
    for pat in _EXPORT_NOISE:
        if pat.match(line):
            return True
    return False


def _split_sender_and_body(rest: str) -> Tuple[str, str]:
    """
    把 "HH:MM " 後的 rest 切成 (sender, first_body_line)。

    LINE Desktop 在發送者與訊息之間放多個半形空白（實測 3 格），
    中間若只有 1 個空白，通常是「簡短系統訊息」（例： "乘 取消"、"Y/944873加入聊天"）
    — 這類視為 sender='乘' / body='取消' 或 sender='系統'/body=全文。
    我們的規則：

      * 以「連續 2 個以上空白」為分隔，若存在 → 前半 sender、後半 body
      * 若只有 1 個空白分隔：若第 1 段符合「暱稱樣式」則 sender=前半、body=後半；
        否則整段當 body、sender 空
      * 沒有空白 → sender=整行、body=''（極少見）
    """
    if rest is None:
        return '', ''
    # 規則 1：≥ 2 個空白
    m = re.match(r'^(?P<s>\S+(?:[·\-/][^\s]+)*)\s{2,}(?P<b>.*)$', rest)
    if m:
        return m.group('s').strip(), m.group('b').strip()
    # 規則 2：1 個空白
    parts = rest.split(' ', 1)
    if len(parts) == 2:
        s, b = parts[0].strip(), parts[1].strip()
        # 暱稱樣式：長度 ≤ 20，不含 XAUUSD/Buy/Sell/止損...
        if s and _looks_like_nickname(s):
            return s, b
        return '', rest.strip()
    return rest.strip(), ''


def _looks_like_nickname(text: str) -> bool:
    if not text or len(text) > 20:
        return False
    t = text.lower()
    for kw in ('xauusd', '黃金', 'buy', 'sell', '止損', '止盈', 'sl', 'tp', '多', '空'):
        if kw in t:
            return False
    return True


def _is_system_body(body: str) -> bool:
    for pat in _SYSTEM_PATTERNS:
        if pat.search(body):
            return True
    return False


def _is_system_sender(sender: str) -> bool:
    if not sender:
        return False
    if re.search(r'加入聊天\s*$', sender):
        return True
    if re.search(r'離開聊天\s*$', sender):
        return True
    if re.match(r'^Auto[- ]?reply$', sender, re.IGNORECASE):
        return True
    return False


# -------------------- 主解析器 --------------------

class LineTextParser:
    """把 LINE Desktop 複製文字切成訊息。"""

    def parse(
        self,
        text: str,
        default_date: Optional[date] = None,
    ) -> ParseResult:
        if not text:
            return ParseResult([], default_date, 0, 0)

        cur_date: Optional[date] = default_date or datetime.now().date()
        # LINE 複製通常只用 \n；保險起見都正規化
        lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

        messages: List[LineMessage] = []
        pending: Optional[LineMessage] = None
        pending_lines: List[str] = []
        idx = 0

        def flush():
            nonlocal pending, pending_lines, idx
            if pending is None:
                pending_lines = []
                return
            body = '\n'.join(pending_lines).strip('\n').strip()
            pending.body = body
            pending.raw_lines = list(pending_lines)
            pending.is_system = (
                pending.is_system
                or _is_system_body(body)
                or _is_system_sender(pending.sender)
            )
            messages.append(pending)
            idx += 1
            pending = None
            pending_lines = []

        for raw in lines:
            line = raw.rstrip()

            if _is_export_noise(line):
                continue

            d = _match_date_line(line)
            if d is not None:
                # 遇到日期行：收尾目前訊息，切換日期
                flush()
                cur_date = d
                continue

            head = _RE_MSG_HEAD.match(line)
            if head:
                # 新訊息：先收尾舊訊息
                flush()
                hh = int(head.group('h'))
                mm = int(head.group('m'))
                try:
                    ts = datetime.combine(cur_date, dtime(hh % 24, mm % 60))
                except (ValueError, TypeError):
                    ts = None
                rest = head.group('rest') or ''
                sender, first_body = _split_sender_and_body(rest)
                pending = LineMessage(
                    index=idx,
                    time_str=f"{hh:02d}:{mm:02d}",
                    timestamp=ts,
                    sender=sender,
                    body='',
                )
                if first_body:
                    pending_lines.append(first_body)
                continue

            # 非 head / 非 date → 當作前一訊息的續行；pending 為 None 時略過開頭碎片
            if pending is not None:
                pending_lines.append(line)
            # else: 整體開頭沒有時間行，屬於不完整截取，略過

        flush()

        return ParseResult(
            messages=messages,
            current_date=cur_date,
            raw_len=len(text),
            raw_line_count=len(lines),
        )


# -------------------- 增量工具 --------------------

def diff_new_messages(
    messages: List[LineMessage],
    seen_keys: Set[Tuple[str, str, str]],
) -> List[LineMessage]:
    """依原順序回傳尚未在 seen_keys 裡的訊息。"""
    return [m for m in messages if m.key not in seen_keys]


# -------------------- 自測 --------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            sample = f.read()
    else:
        sample = """2026.04.16 星期四
10:45 乘   乘XAUUSD 黃金

BUY ：4815
止損：4806
止盈 : 4828

（純粹個人投資分享）
18:37 乘 取消
2026.04.17 星期五
10:12 乘 撤"""
    p = LineTextParser()
    r = p.parse(sample)
    print(f"parsed {len(r.messages)} messages ({len(r.non_system_messages)} non-system)")
    for m in r.messages:
        tag = "SYS" if m.is_system else "MSG"
        ts = m.timestamp.isoformat() if m.timestamp else "?"
        body_preview = (m.body or '').replace('\n', ' | ')[:100]
        print(f"  [{m.index}] [{tag}] {ts} sender={m.sender!r} body={body_preview!r}")
