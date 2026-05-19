from __future__ import annotations

import re
from datetime import datetime, timedelta, date
from typing import Optional


# 日時キーワードと相対日付のマッピング
def _build_date_map() -> dict:
    now = datetime.now()
    today = now.date()

    # 今週末 = 直近の日曜日（今日が日曜なら来週日曜）
    days_to_sunday = (6 - now.weekday()) % 7 or 7
    this_week_end = today + timedelta(days=days_to_sunday)
    next_week_end = this_week_end + timedelta(days=7)

    return {
        "今日": today,
        "本日": today,
        "今夜": today,
        "今晩": today,
        "今夕": today,
        "明日": today + timedelta(days=1),
        "あした": today + timedelta(days=1),
        "明晩": today + timedelta(days=1),
        "明後日": today + timedelta(days=2),
        "あさって": today + timedelta(days=2),
        "今週中": this_week_end,
        "今週": this_week_end,
        "来週": next_week_end,
    }


# タスク実行を示す動詞・表現
ACTION_PATTERNS = [
    r"やる", r"やります", r"やっとく", r"やっておく", r"やっておきます",
    r"やってみる", r"やっておこう", r"やろう",
    r"する", r"します", r"しとく", r"しておく", r"しておきます", r"しよう",
    r"しなきゃ", r"しないと", r"せねば",
    r"終わらせる", r"終わらせます", r"終える", r"仕上げる", r"完成させる",
    r"提出する", r"提出します", r"送る", r"送ります", r"送付する",
    r"確認する", r"確認します", r"チェックする", r"レビューする",
    r"修正する", r"修正します", r"直す", r"直します",
    r"作る", r"書く", r"対応する", r"処理する",
    r"デプロイする", r"テストする", r"マージする", r"pushする", r"PRを出す",
    r"出す", r"出します", r"上げる", r"上げます",
    r"する予定", r"します予定",
    r"中に",  # 「今日中に」「今週中に」
    r"までに", r"まで[にで]",
]

_ACTION_RE = re.compile("|".join(ACTION_PATTERNS))

# 除外パターン（質問・感想・過去形など）
EXCLUDE_PATTERNS = [
    r"[？?]",           # 疑問文
    r"だった", r"でした", r"ました",  # 過去形
    r"ですね", r"だね",   # 感想
    r"どこ", r"なに", r"だれ", r"いつ",  # 5W1H の疑問
]
_EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS))


def detect_reminder_intent(text: str) -> tuple:
    """
    メッセージからリマインド意図を検出する。
    戻り値: (検出した, 対象日付, テキスト)
    """
    if _EXCLUDE_RE.search(text):
        return False, None, text

    date_map = _build_date_map()
    detected_date = None

    # より長いキーワードを先にマッチ（「今週中」が「今週」より先にヒットするよう sort）
    for keyword in sorted(date_map.keys(), key=len, reverse=True):
        if keyword in text:
            detected_date = date_map[keyword]
            break

    if detected_date is None:
        return False, None, text

    if not _ACTION_RE.search(text):
        return False, None, text

    return True, detected_date, text


# ---------- 自由テキストからの日時抽出（/remind 用） ----------

def extract_date_from_text(text: str) -> Optional[date]:
    """
    自由テキストから日付を抽出する。動作動詞は不要。
    キーワード → MM/DD形式 → YYYY/MM/DD の順で探す。
    """
    date_map = _build_date_map()
    for keyword in sorted(date_map.keys(), key=len, reverse=True):
        if keyword in text:
            return date_map[keyword]

    now = datetime.now()
    # YYYY/MM/DD または YYYY-MM-DD
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # MM/DD または MM-DD
    m = re.search(r"(\d{1,2})[/-](\d{1,2})", text)
    if m:
        try:
            candidate = date(now.year, int(m.group(1)), int(m.group(2)))
            if candidate < now.date():
                candidate = candidate.replace(year=now.year + 1)
            return candidate
        except ValueError:
            pass

    return None


def extract_time_from_text(text: str) -> Optional[str]:
    """
    自由テキストから時刻文字列（HH:MM）を抽出する。
    例: 「14時30分」「14:30」「午後2時」→ "14:30"
    """
    # HH:MM 形式
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"

    # HH時MM分 / HH時
    m = re.search(r"(\d{1,2})時(\d{1,2}分)?", text)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2).rstrip("分")) if m.group(2) else 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"

    return None


# ---------- コマンド用パーサー ----------

def parse_remind_datetime(
    date_str: str,
    time_str: Optional[str] = None,
    default_time: str = "23:30",
) -> Optional[datetime]:
    now = datetime.now()

    relative = {
        "今日": now.date(),
        "本日": now.date(),
        "明日": (now + timedelta(days=1)).date(),
        "明後日": (now + timedelta(days=2)).date(),
        "あした": (now + timedelta(days=1)).date(),
        "あさって": (now + timedelta(days=2)).date(),
    }

    if date_str in relative:
        target_date = relative[date_str]
    else:
        target_date = None
        for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%m/%d", "%m-%d"]:
            try:
                parsed = datetime.strptime(date_str, fmt)
                if fmt in ("%m/%d", "%m-%d"):
                    candidate = parsed.replace(year=now.year).date()
                    if candidate < now.date():
                        candidate = candidate.replace(year=now.year + 1)
                    target_date = candidate
                else:
                    target_date = parsed.date()
                break
            except ValueError:
                continue

        if target_date is None:
            return None

    if time_str:
        t = _parse_time(time_str)
        if t is None:
            return None
        return datetime.combine(target_date, t)

    return datetime.combine(target_date, datetime.strptime(default_time, "%H:%M").time())


def _parse_time(time_str: str) -> Optional[datetime]:
    for fmt in ["%H:%M", "%H時%M分", "%H時"]:
        try:
            return datetime.strptime(time_str, fmt).time()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})?", time_str)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return datetime.strptime(f"{h:02d}:{mi:02d}", "%H:%M").time()
    return None
