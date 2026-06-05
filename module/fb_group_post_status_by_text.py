# module/fb_group_post_status_by_text.py
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional
import xml.etree.ElementTree as ET


# Thứ tự section trên màn "Nội dung của bạn"
# (Có thể có thêm "Đã gỡ" ở dưới, ta coi là removed nếu match vào đó)
STATUS_SECTIONS = [
    ("Đang chờ", "pending"),
    ("Chờ duyệt", "pending"),
    ("Đã đăng", "posted"),
    ("Bị từ chối", "rejected"),
    ("Đã gỡ", "removed"),
]

EMPTY_LABEL = "Không có bài viết nào để hiển thị"

IGNORE_TEXTS_NORM = {
    "xem tat ca",
    "noi dung cua ban",
    "tat ca",
    "moi nhat truoc",
    "xem them",
    "chinh sua",
    "xoa",
    "xem trong nhom",
    # empty label sẽ xử lý riêng theo section
    "khong co bai viet nao de hien thi",
}


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _to_plain_keep_newlines(s: str) -> str:
    """
    Bỏ icon/emoji + ký tự đặc biệt -> text thuần.
    - Giữ newline để tách dòng tiêu đề/body như command CRM.
    - Giữ chữ/số/khoảng trắng.
    """
    if not s:
        return ""
    out = []
    for ch in s:
        if ch == "\n":
            out.append("\n")
            continue
        # giữ chữ/số/space, còn lại đổi thành space (emoji/icon/dấu câu/…)
        if ch.isalnum() or ch.isspace():
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def _norm_text(s: str, *, strip_accents: bool = True) -> str:
    if not s:
        return ""
    s = _to_plain_keep_newlines(s)
    # newline -> space cho norm
    s = s.replace("\n", " ")
    s = s.lower()
    if strip_accents:
        s = _strip_accents(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _make_snippets_from_command(content: str, *, max_chars: int = 200) -> List[str]:
    """
    Lấy 200 ký tự đầu (sau khi bỏ icon) từ command CRM, rồi tạo nhiều snippet
    để match ổn định với UI (UI thường chỉ hiện tiêu đề + 1 dòng đầu).
    """
    if not content:
        return []

    head_raw = _to_plain_keep_newlines(content).strip()
    head_raw = head_raw[:max_chars].strip()

    # tách dòng theo command (giữ newline từ CRM)
    lines = [ln.strip() for ln in head_raw.splitlines() if ln.strip()]

    # fallback nếu không có newline
    if not lines:
        lines = [head_raw] if head_raw else []

    first_line = lines[0] if lines else ""
    longest_line = max(lines, key=len) if lines else ""

    # join 200 chars đầu thành 1 câu (phòng UI gộp dòng)
    joined = " ".join(lines)

    candidates = [
        longest_line,
        first_line,
        joined,
    ]

    # thêm dạng 12 từ đầu của longest_line (phòng UI bị rút gọn)
    lt = _norm_text(longest_line, strip_accents=True)
    if lt:
        w = lt.split()
        candidates.append(" ".join(w[:12]))

    # normalize + unique
    out: List[str] = []
    seen = set()
    for c in candidates:
        n = _norm_text(c, strip_accents=True)
        if not n:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _similarity(snippet_norm: str, candidate_norm: str) -> float:
    if not snippet_norm or not candidate_norm:
        return 0.0
    if snippet_norm in candidate_norm:
        return 1.0
    cand = candidate_norm[: max(len(snippet_norm) + 40, 240)]
    return SequenceMatcher(None, snippet_norm, cand).ratio()


def _get_bounds(info: dict) -> Optional[dict]:
    b = (info or {}).get("bounds")
    if not b:
        return None
    if not all(k in b for k in ("left", "top", "right", "bottom")):
        return None
    return b

def _parse_bounds_str(bounds: str) -> Optional[dict]:
    """
    bounds trong dump_hierarchy có dạng: "[0,294][1080,334]"
    """
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    left, top, right, bottom = map(int, m.groups())
    return {"left": left, "top": top, "right": right, "bottom": bottom}

def _iter_text_nodes_from_xml(xml: str):
    """
    Yield (raw_text, bounds_dict) từ cả text và content-desc trong XML.
    """
    root = ET.fromstring(xml)
    for node in root.iter():
        b = _parse_bounds_str(node.attrib.get("bounds", ""))
        if not b:
            continue

        txt = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or node.attrib.get("contentDescription") or "").strip()

        if txt:
            yield txt, b
        if desc and desc != txt:
            yield desc, b

def detect_group_post_status_by_text(
    driver,
    target_content: str,
    *,
    min_score: float = 0.60,
    command_max_chars: int = 200,
) -> Dict:
    """
    Chạy trên màn "Nội dung của bạn" (1 màn hình, không scroll).

    Logic:
    1) Tạo snippet từ 200 ký tự đầu của content (bỏ icon/emoji)
    2) Xác định bounds từng section theo header: Đang chờ/Đã đăng/Bị từ chối/(Đã gỡ)
    3) Quét TextView, gán vào section theo cy
    4) Match snippet trong từng section -> status của section đó
    5) Nếu pending/posted/rejected đều có EMPTY_LABEL -> removed
    """

    snippets = _make_snippets_from_command(target_content, max_chars=command_max_chars)
    debug = {
        "snippets": snippets,
        "sections": [],
        "empty_sections": {},
        "best_hits": [],
    }

    if not snippets:
        return {
            "found": False,
            "status": "removed",
            "score": 0.0,
            "debug": {"reason": "empty_content", **debug},
        }

    # --- 1) Lấy header bounds để dựng section ranges ---
    headers: List[Tuple[int, int, str, str]] = []  # (top,bottom,ui_text,key)
    for ui_text, key in STATUS_SECTIONS:
        try:
            el = driver(textContains=ui_text)
            if el.exists:
                b = _get_bounds(el.info or {})
                if b:
                    headers.append((int(b["top"]), int(b["bottom"]), ui_text, key))
        except Exception:
            pass

    headers.sort(key=lambda x: x[0])

    # build sections: y0 = header_bottom, y1 = next_header_top (hoặc +inf)
    sections = []
    for i, (top, bottom, ui_text, key) in enumerate(headers):
        y0 = bottom
        y1 = headers[i + 1][0] if i + 1 < len(headers) else 10**9
        sections.append({"key": key, "ui_text": ui_text, "y0": y0, "y1": y1, "top": top, "bottom": bottom})

    debug["sections"] = sections

    # Nếu không thấy header nào (UI chưa load) -> fallback removed/no_match
    if not sections:
        return {
            "found": False,
            "status": "removed",
            "score": 0.0,
            "debug": {"reason": "no_headers", **debug},
        }

    # --- 2) Quét TextView ---
        # --- 2) Quét XML dump_hierarchy (text + content-desc) ---
    try:
        xml = driver.dump_hierarchy()
    except Exception:
        xml = ""

    # trạng thái empty từng section
    empty_map = {s["key"]: False for s in sections}

    # best hit từng section
    best_by_section = {s["key"]: None for s in sections}  # (score, cy, raw, bounds)

    def _section_for_cy(cy: int) -> Optional[dict]:
        for s in sections:
            if s["y0"] <= cy < s["y1"]:
                return s
        return None

    header_norms = set(_norm_text(s["ui_text"]) for s in sections)
    empty_norm = _norm_text(EMPTY_LABEL)  # "khong co bai viet nao de hien thi"

    # debug thêm để nhìn xem XML có gì
    debug["xml_ok"] = bool(xml)
    debug["xml_scanned"] = 0

    if xml:
        for raw, b in _iter_text_nodes_from_xml(xml):
            debug["xml_scanned"] += 1

            cy = int((b["top"] + b["bottom"]) / 2)
            sec = _section_for_cy(cy)
            if not sec:
                continue

            cand_norm = _norm_text(raw, strip_accents=True)
            if not cand_norm:
                continue

            # ✅ EMPTY LABEL: dùng contains thay vì == (để tránh lệch do UI/space)
            if empty_norm in cand_norm:
                empty_map[sec["key"]] = True
                continue

            # ignore texts + header
            if cand_norm in IGNORE_TEXTS_NORM:
                continue
            if cand_norm in header_norms:
                continue

            # score = max similarity với các snippet
            score = 0.0
            for sn in snippets:
                sc = _similarity(sn, cand_norm)
                if sc > score:
                    score = sc

            if score < min_score:
                continue

            prev = best_by_section.get(sec["key"])
            if prev is None:
                best_by_section[sec["key"]] = (score, cy, raw, b)
            else:
                pscore, pcy, _, _ = prev
                if score > pscore + 1e-6 or (abs(score - pscore) <= 1e-6 and cy < pcy):
                    best_by_section[sec["key"]] = (score, cy, raw, b)

    debug["empty_sections"] = empty_map

    # --- 3) Chọn hit tốt nhất ---
    hits = []
    for sec in sections:
        k = sec["key"]
        if best_by_section.get(k):
            score, cy, raw, bounds = best_by_section[k]
            hits.append(
                {"status": k, "score": score, "cy": cy, "text": raw, "bounds": bounds, "header": sec["ui_text"]}
            )

    debug["best_hits"] = hits

    if hits:
        # chọn hit có score cao nhất; nếu bằng -> ưu tiên theo thứ tự section (pending->posted->rejected->removed)
        order = {"pending": 0, "posted": 1, "rejected": 2, "removed": 3}
        hits.sort(key=lambda x: (-x["score"], order.get(x["status"], 99), x["cy"]))
        best = hits[0]
        return {
            "found": True,
            "status": best["status"],
            "status_header": best["header"],
            "match_text": best["text"],
            "match_bounds": best["bounds"],
            "score": float(best["score"]),
            "debug": debug,
        }

    # --- 4) Nếu 3 trạng thái đầu đều empty -> removed ---
    # chỉ xét đúng 3 trạng thái đầu theo yêu cầu của bạn
    pending_empty = empty_map.get("pending", False)
    posted_empty = empty_map.get("posted", False)
    rejected_empty = empty_map.get("rejected", False)

    if pending_empty and posted_empty and rejected_empty:
        return {
            "found": False,
            "status": "removed",
            "score": 0.0,
            "debug": {"reason": "all_three_sections_empty", **debug},
        }

    return {
        "found": False,
        "status": "removed",
        "score": 0.0,
        "debug": {"reason": "no_match", **debug},
    }
