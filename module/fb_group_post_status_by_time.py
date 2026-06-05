from __future__ import annotations

import re
import time
import logging
from enum import Enum
from typing import Optional, Tuple, Dict, List


# ============================================================
#  Group post time parsing utilities
#  (No dump_hierarchy / no WEditor dependency)
# ============================================================

class GroupPostStatus(str, Enum):
    PENDING = "pending"
    POSTED = "posted"
    REJECTED = "rejected"
    REMOVED = "removed"
    UNKNOWN = "unknown"


# Relative time patterns (VN/EN)
TIME_REGEX = re.compile(
    r"(\d+\s*(phút|giờ|ngày|tuần))|(vừa xong)|(hôm qua)|"
    r"(just now)|(yesterday)|(\d+\s*(minute|hour|day|week)s?)",
    re.IGNORECASE,
)

# Date-like patterns that FB sometimes shows in place of relative time
# Examples: "10 thg 9, 2024", "10 thg 9", "30 tháng 12, 2025"
DATE_REGEX = re.compile(
    r"\b(\d{1,2})\s*(thg|tháng)\s*(\d{1,2})(?:\s*,\s*(\d{4}))?\b",
    re.IGNORECASE,
)


def parse_age_minutes(text: str) -> Optional[int]:
    """
    Convert *relative* time label -> age minutes.
    Returns None if cannot parse as relative time.

    Examples:
      - "Vừa xong" -> 0
      - "2 phút" -> 2
      - "1 giờ" -> 60
      - "3 ngày" -> 4320
      - "Hôm qua" -> 1440
      - "2 hours" -> 120
    """
    if not text:
        return None

    t = text.strip().lower()

    # Just now / vừa xong
    if "vừa xong" in t or "just now" in t:
        return 0

    # yesterday / hôm qua
    if "hôm qua" in t or "yesterday" in t:
        return 24 * 60

    # minutes
    m = re.search(r"(\d+)\s*(phút|minute|minutes)\b", t)
    if m:
        return int(m.group(1))

    # hours
    m = re.search(r"(\d+)\s*(giờ|hour|hours)\b", t)
    if m:
        return int(m.group(1)) * 60

    # days
    m = re.search(r"(\d+)\s*(ngày|day|days)\b", t)
    if m:
        return int(m.group(1)) * 24 * 60

    # weeks
    m = re.search(r"(\d+)\s*(tuần|week|weeks)\b", t)
    if m:
        return int(m.group(1)) * 7 * 24 * 60

    return None


def is_date_like_label(text: str) -> bool:
    """
    True if the label looks like an absolute date (e.g., "10 thg 9, 2024").
    Date-like labels are treated as "old" in the 0-6 minutes logic.
    """
    if not text:
        return False
    t = text.strip().lower()
    # quick reject: avoid matching clock in status bar like "5:01"
    if re.search(r"\b\d{1,2}:\d{2}\b", t):
        return False
    return bool(DATE_REGEX.search(t))


def _section_y_band_for_group_profile(driver, header_titles: Tuple[str, ...]) -> Optional[Tuple[int, int]]:
    """
    Compute a y-band under the "Bài viết trong nhóm" header to scan only the post list area.
    No swiping/scrolling here: only works within current viewport.

    Returns (y0, y1) or None if header not found.
    """
    header_el = None
    for ht in header_titles:
        el = driver(textContains=ht)
        if el.exists:
            header_el = el
            break

    if not header_el:
        return None

    hb = (header_el.info or {}).get("bounds") or {}
    if "bottom" not in hb:
        return None

    y0 = int(hb["bottom"]) + 1
    w, h = driver.window_size()

    # In practice, first visible post card is usually within next ~55% of screen.
    y1 = min(h, y0 + int(0.55 * h))
    return (y0, y1)


def find_latest_post_time_in_group_profile_view(
    driver,
    recent_max_minutes: int = 6,
    header_titles: Tuple[str, ...] = ("Bài viết trong nhóm", "Posts in group"),
) -> Dict:
    """
    Find the *newest* post time label within the "Bài viết trong nhóm" area (group profile page).
    - No dump_hierarchy/XML parsing.
    - No scrolling. Works on current viewport.

    Returns a dict:
      {
        "ok": bool,                # True if newest post is in [0..recent_max_minutes]
        "age": Optional[int],      # age minutes if relative time; None if not found
        "kind": "relative"|"date"|"none",
        "time_text": Optional[str],
        "time_bounds": Optional[dict],  # bounds dict from uiautomator2
        "debug": {...}
      }

    Decision logic:
    - If we find relative time <= recent_max_minutes => ok=True (eligible to copy link).
    - If we find only date-like OR relative time > recent_max_minutes => ok=False (treat as rejected by your rule).
    - If we find nothing => ok=False (treat as rejected).
    """
    band = _section_y_band_for_group_profile(driver, header_titles)
    debug = {"band": band, "candidates": []}

    if not band:
        return {"ok": False, "age": None, "kind": "none", "time_text": None, "time_bounds": None, "debug": {"reason": "header_not_found", **debug}}

    y0, y1 = band

    best = None  # (age, text, bounds)
    best_old = None  # (age, text, bounds) for relative but older than recent_max
    best_date = None  # (text, bounds)

    # Scan visible TextViews
    try:
        for el in driver(className="android.widget.TextView").all():
            info = el.info or {}
            b = info.get("bounds") or {}
            if "top" not in b or "bottom" not in b:
                continue
            cy = int((b["top"] + b["bottom"]) / 2)
            if not (y0 <= cy <= y1):
                continue

            s = (info.get("text") or "").strip()
            if not s:
                continue

            # ignore status bar clock
            if re.fullmatch(r"\d{1,2}:\d{2}", s):
                continue

            if TIME_REGEX.search(s):
                age = parse_age_minutes(s)
                debug["candidates"].append({"text": s, "age": age, "cy": cy})

                if age is not None:
                    if age <= recent_max_minutes and (best is None or age < best[0]):
                        best = (age, s, b)
                    elif age > recent_max_minutes and (best_old is None or age < best_old[0]):
                        best_old = (age, s, b)
                else:
                    # TIME_REGEX matched but parse failed (rare). ignore.
                    pass

            elif is_date_like_label(s):
                debug["candidates"].append({"text": s, "age": None, "cy": cy, "date_like": True})
                # pick the top-most date-like as representative if needed
                if best_date is None:
                    best_date = (s, b)

    except Exception as e:
        return {"ok": False, "age": None, "kind": "none", "time_text": None, "time_bounds": None, "debug": {"reason": f"scan_error:{e}", **debug}}

    if best:
        age, s, b = best
        return {"ok": True, "age": age, "kind": "relative", "time_text": s, "time_bounds": b, "debug": debug}

    # No recent relative time found -> treat as rejected by your rule
    if best_old:
        age, s, b = best_old
        return {"ok": False, "age": age, "kind": "relative", "time_text": s, "time_bounds": b, "debug": debug}

    if best_date:
        s, b = best_date
        return {"ok": False, "age": None, "kind": "date", "time_text": s, "time_bounds": b, "debug": debug}

    return {"ok": False, "age": None, "kind": "none", "time_text": None, "time_bounds": None, "debug": debug}


def detect_latest_post_status_from_group_profile_view(
    driver,
    recent_max_minutes: int = 6,
) -> Tuple[GroupPostStatus, Dict]:
    """
    Your new concept on group profile page:
    - If newest post in "Bài viết trong nhóm" is 0..recent_max_minutes => POSTED
    - Else => REJECTED (includes not-found or old)
    """
    info = find_latest_post_time_in_group_profile_view(driver, recent_max_minutes=recent_max_minutes)
    if info.get("ok"):
        st = GroupPostStatus.POSTED
    else:
        st = GroupPostStatus.REJECTED
    logging.info(f"[GROUP_PROFILE_LATEST] status={st.value} info={info}")
    return st, info


# Backward-compatible names (if other code imports these)
def detect_recent_status_no_scroll(driver, recent_min: int = 0, recent_max: int = 6, **kwargs) -> Tuple[GroupPostStatus, Dict]:
    """
    Kept for compatibility. In the new flow (group profile), we ignore recent_min and only use recent_max.
    """
    return detect_latest_post_status_from_group_profile_view(driver, recent_max_minutes=recent_max)

