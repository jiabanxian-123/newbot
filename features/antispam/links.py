# -*- coding: utf-8 -*-
"""② 链接域名白名单

白名单内的链接不删（子域名自动放行）；关掉开关时全部按普通链接处理。
"""

from core import hub

import re


def _link_domains():
    """白名单域名（归一：去协议、去路径、去点前缀、小写）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    out = []
    for d in (sget("LINK_WHITELIST") or []):
        d = str(d).strip().lower()
        if not d:
            continue
        d = re.sub(r"^https?://", "", d).split("/")[0].lstrip(".")
        if d:
            out.append(d)
    return out


def _link_whitelisted(text):
    """文本里出现的每一个域名都在白名单里才放行；解析不出域名或有域名不在名单 → 不放行。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _URL_HOST_RE = hub._URL_HOST_RE
    _link_domains = hub._link_domains
    hosts = [h.lower().rstrip(".") for h in _URL_HOST_RE.findall(str(text or ""))]
    if not hosts:
        return False
    domains = _link_domains()
    if not domains:
        return False
    for h in hosts:
        if not any(h == d or h.endswith("." + d) for d in domains):
            return False
    return True
