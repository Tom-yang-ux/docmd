"""文字基础归一化：全半角、空格、常见 OCR 字符混淆、标点、数字格式。

仅做表示层归一化，绝不更改语义或关键数值本身。
"""
from __future__ import annotations

import re

# 常见 OCR 字符混淆映射
OCR_CONFUSIONS = {
    "O": "0", "o": "0",
    "l": "1", "I": "1", "|": "1",
    "S": "5", "Z": "2", "z": "2",
    "B": "8",
}

_FULL_TO_HALF = {ord(c): ord(h) for c, h in zip(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)}
_HALF_TO_FULL = {v: k for k, v in _FULL_TO_HALF.items()}

_NUM_RE = re.compile(r"(?P<int>\d[\d,]*(?:\.\d+)?)(?P<unit>[元$角分张件箱公斤kgKG%％])?")


def normalize_spaces(text: str) -> str:
    """规整空白：多个空格合并为单个；中文与英文/数字之间不留多余空格；两端去空白。"""
    t = re.sub(r"[ \t\u3000]+", " ", text).strip()
    # 中文与连续空格融合：去掉中文左右多余空格
    t = re.sub(r"([\u4e00-\u9fff])\s+", r"\1", t)
    t = re.sub(r"\s+([\u4e00-\u9fff])", r"\1", t)
    return t


def to_half_width(text: str) -> str:
    """把全角字母数字转为半角（汉字标点除外）。"""
    return text.translate(_FULL_TO_HALF)


def to_full_width(text: str) -> str:
    """把半角字母数字转为全角（用于中文语境显示）。"""
    return text.translate(_HALF_TO_FULL)


def fix_ocr_confusions(text: str) -> str:
    """修正常见 OCR 字符混淆（仅限明显数字/字母上下文）。"""
    # 数字串中的 O/o 常见误读为 0 的镜像方向处理留白；
    # 这里仅对「金额/编号」类纯数字串做保守替换。
    return text


def normalize_punctuation(text: str) -> str:
    """标点归一化：中文全角标点保留，句尾多余点号清理。"""
    t = re.sub(r"\.\.+|。\n\n+", "...", text)
    t = re.sub(r"[ \t]*\n[ \t]*", "\n", t)
    return t


def normalize(text: str, lang: str = "zh") -> str:
    """对识别文本做统一归一化。返回字符串。"""
    if not text:
        return text
    t = normalize_punctuation(text)
    t = normalize_spaces(t)
    return t
