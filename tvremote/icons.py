"""Programmatically-drawn button icons (no bundled assets, no logos).

Control glyphs (power, arrows, volume, media…) are painted with QPainter and
cached as QIcon. App shortcuts get a brand-coloured rounded tile with the app's
initials — recognisable without reproducing any trademarked logo.

QApplication must exist before these are called (they build QPixmaps).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF,
)

FG = QColor("#e2e6ee")
BTN_BG = QColor("#2a2d36")     # matches button background for "cut-out" fills
MUTE_RED = QColor("#e06a63")
_cache: dict[str, QIcon] = {}


def _poly(pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _new():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    return pm, p


# ---- individual glyphs ---------------------------------------------------
def _fill_poly(p, pts):
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    p.drawPolygon(_poly(pts))


def _d_up(p):    _fill_poly(p, [(20, 42), (44, 42), (32, 20)])
def _d_down(p):  _fill_poly(p, [(20, 22), (44, 22), (32, 44)])
def _d_left(p):  _fill_poly(p, [(42, 20), (42, 44), (20, 32)])
def _d_right(p): _fill_poly(p, [(22, 20), (22, 44), (44, 32)])


def _d_power(p):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(FG, 5, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(QRectF(18, 20, 28, 28), 110 * 16, 320 * 16)
    p.drawLine(QPointF(32, 14), QPointF(32, 32))


def _speaker(p):
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    p.drawPolygon(_poly([(14, 27), (22, 27), (31, 18), (31, 46), (22, 37), (14, 37)]))


def _d_volup(p):
    _speaker(p)
    p.setPen(QPen(FG, 4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(43, 32), QPointF(55, 32))
    p.drawLine(QPointF(49, 26), QPointF(49, 38))


def _d_voldown(p):
    _speaker(p)
    p.setPen(QPen(FG, 4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(43, 32), QPointF(55, 32))


def _d_mute(p):
    _speaker(p)
    p.setPen(QPen(MUTE_RED, 4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(42, 26), QPointF(56, 40))
    p.drawLine(QPointF(56, 26), QPointF(42, 40))


def _chevrons(p, up):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(FG, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for y in (28, 40):
        if up:
            p.drawPolyline(_poly([(20, y + 6), (32, y - 6), (44, y + 6)]))
        else:
            p.drawPolyline(_poly([(20, y - 6), (32, y + 6), (44, y - 6)]))


def _d_chup(p):   _chevrons(p, True)
def _d_chdown(p): _chevrons(p, False)


def _d_play(p): _fill_poly(p, [(24, 18), (24, 46), (48, 32)])


def _d_pause(p):
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    p.drawRoundedRect(QRectF(22, 18, 8, 28), 2, 2)
    p.drawRoundedRect(QRectF(34, 18, 8, 28), 2, 2)


def _d_stop(p):
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    p.drawRoundedRect(QRectF(20, 20, 24, 24), 3, 3)


def _d_rewind(p):
    _fill_poly(p, [(34, 18), (34, 46), (16, 32)])
    _fill_poly(p, [(50, 18), (50, 46), (32, 32)])


def _d_ff(p):
    _fill_poly(p, [(30, 18), (30, 46), (48, 32)])
    _fill_poly(p, [(14, 18), (14, 46), (32, 32)])


def _d_home(p):
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    p.drawPolygon(_poly([(32, 14), (52, 32), (12, 32)]))
    p.drawRoundedRect(QRectF(18, 30, 28, 20), 2, 2)
    p.setBrush(BTN_BG)
    p.drawRect(QRectF(28, 38, 8, 12))


def _d_menu(p):
    p.setPen(QPen(FG, 5, Qt.SolidLine, Qt.RoundCap))
    for y in (24, 32, 40):
        p.drawLine(QPointF(18, y), QPointF(46, y))


def _d_back(p):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(FG, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline(_poly([(46, 22), (46, 34), (28, 34)]))
    _fill_poly(p, [(28, 26), (28, 42), (16, 34)])


def _d_source(p):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(FG, 4))
    p.drawRoundedRect(QRectF(12, 20, 26, 24), 3, 3)
    p.setPen(QPen(FG, 4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(54, 32), QPointF(40, 32))
    _fill_poly(p, [(44, 26), (44, 38), (34, 32)])


def _d_exit(p):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(FG, 4))
    p.drawRoundedRect(QRectF(12, 18, 20, 28), 3, 3)
    p.setPen(QPen(FG, 4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(28, 32), QPointF(52, 32))
    _fill_poly(p, [(46, 26), (46, 38), (56, 32)])


def _d_info(p):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(FG, 4))
    p.drawEllipse(QRectF(18, 18, 28, 28))
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    p.drawEllipse(QRectF(30, 23, 4, 4))
    p.drawRoundedRect(QRectF(30, 30, 4, 11), 1, 1)


def _d_tools(p):
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    for k in range(8):
        a = math.radians(k * 45)
        p.drawRoundedRect(QRectF(32 + 15 * math.cos(a) - 3, 32 + 15 * math.sin(a) - 3, 6, 6), 1, 1)
    p.drawEllipse(QRectF(22, 22, 20, 20))
    p.setBrush(BTN_BG)
    p.drawEllipse(QRectF(28, 28, 8, 8))


def _d_list(p):
    p.setPen(QPen(FG, 4, Qt.SolidLine, Qt.RoundCap))
    for y in (24, 32, 40):
        p.drawLine(QPointF(26, y), QPointF(46, y))
    p.setPen(Qt.NoPen)
    p.setBrush(FG)
    for y in (24, 32, 40):
        p.drawEllipse(QRectF(16, y - 2, 4, 4))


_DRAW = {
    "up": _d_up, "down": _d_down, "left": _d_left, "right": _d_right,
    "power": _d_power, "volup": _d_volup, "voldown": _d_voldown, "mute": _d_mute,
    "chup": _d_chup, "chdown": _d_chdown, "play": _d_play, "pause": _d_pause,
    "stop": _d_stop, "rewind": _d_rewind, "ff": _d_ff, "home": _d_home,
    "menu": _d_menu, "back": _d_back, "source": _d_source, "exit": _d_exit,
    "info": _d_info, "tools": _d_tools, "list": _d_list,
}


def get(name: str) -> QIcon:
    if name not in _cache:
        pm, p = _new()
        fn = _DRAW.get(name)
        if fn:
            fn(p)
        p.end()
        _cache[name] = QIcon(pm)
    return _cache[name]


# ---- app tiles -----------------------------------------------------------
_BRAND = {
    "youtube": "#FF0000", "netflix": "#E50914", "hulu": "#17b877", "disney": "#0C1B8C",
    "spotify": "#1aa64b", "paramount": "#0057FF", "peacock": "#2b2b2b", "espn": "#C8102E",
    "prime": "#1399DA", "amazon": "#1399DA", "apple": "#1A1A1A", "tubi": "#7A5CFF",
    "max": "#7B2FF7", "hbo": "#7B2FF7", "plex": "#c9860b", "discovery": "#0A6CBE",
    "pbs": "#2638C4", "freevee": "#2f8f45", "viaplay": "#E4177C", "google": "#4285F4",
    "internet": "#4A6DA7", "browser": "#4A6DA7", "twitch": "#9146FF",
    "jellyfin": "#7b3fd4",
}
_PALETTE = ["#3b6ea5", "#8a5cc4", "#3f9e6b", "#c0663a", "#5a7d9a", "#a44b6e"]


def _brand_color(name: str) -> QColor:
    low = name.lower()
    for key, col in _BRAND.items():
        if key in low:
            return QColor(col)
    return QColor(_PALETTE[sum(map(ord, name)) % len(_PALETTE)])


def _text_color(bg: QColor) -> QColor:
    lum = (0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()) / 255
    return QColor("#111111") if lum > 0.6 else QColor("#ffffff")


def _initials(name: str) -> str:
    words = [w for w in ''.join(c if c.isalnum() else ' ' for c in name).split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[1][0]).upper()


# ---- Steam Deck controller hint badges -----------------------------------
# Generic representations (lettered circles / labelled pills / shapes) of the
# Deck's inputs — drawn here, not copied from Valve's glyph artwork.
_BADGE_BG = QColor(22, 24, 30, 235)
_BADGE_BORDER = QColor("#6b7488")
_pad_cache: dict[str, QPixmap] = {}


def _badge_base(px, round_rect):
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(_BADGE_BORDER, 1.5))
    p.setBrush(_BADGE_BG)
    if round_rect:
        p.drawRoundedRect(QRectF(1, 4, px - 2, px - 8), 5, 5)
    else:
        p.drawEllipse(QRectF(2, 2, px - 4, px - 4))
    return pm, p


def _badge_text(p, px, text, size):
    f = QFont()
    f.setBold(True)
    f.setPixelSize(size)
    p.setFont(f)
    p.setPen(FG)
    p.drawText(QRectF(0, 0, px, px), Qt.AlignCenter, text)


def pad_badge(kind: str, px: int = 26) -> QPixmap:
    if kind in _pad_cache:
        return _pad_cache[kind]
    letters = {"A", "B", "X", "Y"}
    pills = {"L1", "R1", "L2", "R2", "L3", "R3"}
    if kind in letters:
        pm, p = _badge_base(px, round_rect=False)
        _badge_text(p, px, kind, int(px * 0.6))
    elif kind in pills:
        pm, p = _badge_base(px, round_rect=True)
        _badge_text(p, px, kind, int(px * 0.42))
    elif kind in ("start", "select"):
        pm, p = _badge_base(px, round_rect=True)
        p.setPen(QPen(FG, 1.6, Qt.SolidLine, Qt.RoundCap))
        if kind == "start":  # hamburger
            for y in (px * 0.38, px * 0.5, px * 0.62):
                p.drawLine(QPointF(px * 0.3, y), QPointF(px * 0.7, y))
        else:  # two overlapping rectangles (view)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(px * 0.28, px * 0.34, px * 0.30, px * 0.26), 1, 1)
            p.drawRoundedRect(QRectF(px * 0.42, px * 0.42, px * 0.30, px * 0.26), 1, 1)
    elif kind == "dpad":
        pm, p = _badge_base(px, round_rect=True)
        p.setPen(Qt.NoPen)
        p.setBrush(FG)
        c, arm, th = px / 2, px * 0.22, px * 0.16
        p.drawRoundedRect(QRectF(c - th / 2, c - arm, th, 2 * arm), 1, 1)
        p.drawRoundedRect(QRectF(c - arm, c - th / 2, 2 * arm, th), 1, 1)
    else:
        pm, p = _badge_base(px, round_rect=True)
    p.end()
    _pad_cache[kind] = pm
    return pm


# ---- brand icons (device list / header) ----------------------------------
# A coloured rounded tile with a 1-2 char abbreviation — distinct per brand,
# no reproduction of any manufacturer logo.
_BRAND_TILE = {
    "samsung":   ("#2b5bd7", "S"),
    "roku":      ("#6f42c1", "R"),
    "vizio":     ("#c0392b", "V"),
    "lg":        ("#a4133c", "LG"),
    "androidtv": ("#3ddc84", "A"),
    "philips":   ("#0b5ed7", "P"),
    "firetv":    ("#ff9900", "F"),
    "dlna":      ("#5a7d9a", "◈"),
    "generic":   ("#4a6da7", "?"),
}
_brand_cache: dict[str, QIcon] = {}


def brand_icon(brand: str, px: int = 40) -> QIcon:
    if brand in _brand_cache:
        return _brand_cache[brand]
    color, label = _BRAND_TILE.get(brand, _BRAND_TILE["generic"])
    bg = QColor(color)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    m = px * 0.08
    p.drawRoundedRect(QRectF(m, m, px - 2 * m, px - 2 * m), px * 0.24, px * 0.24)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(px * (0.42 if len(label) > 1 else 0.52)))
    p.setFont(f)
    p.setPen(_text_color(bg))
    p.drawText(QRectF(0, 0, px, px), Qt.AlignCenter, label)
    p.end()
    icon = QIcon(pm)
    _brand_cache[brand] = icon
    return icon


def app_tile(name: str, px: int = 96) -> QIcon:
    bg = _brand_color(name)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    m = px * 0.07
    p.drawRoundedRect(QRectF(m, m, px - 2 * m, px - 2 * m), px * 0.2, px * 0.2)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(px * 0.42))
    p.setFont(f)
    p.setPen(_text_color(bg))
    p.drawText(QRectF(0, 0, px, px), Qt.AlignCenter, _initials(name))
    p.end()
    return QIcon(pm)
