"""WorkBuddy（腾讯 CodeBuddy）积分采集。

数据来源与本机 dsh-connect-workbuddy 插件一致：读取其凭据库
``$DSH_HOME/workbuddy-vault/<region>/<accountId>.json``，再用凭据向腾讯
官方计费接口查询积分余额。

本模块只读凭据、只发只读请求；绝不打印或记录 accessToken / refreshToken。
接口未公开且可能调整，所以任何一步失败都返回 None 由调用方降级，不抛异常。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

CN_BILLING_BASE = "https://www.codebuddy.cn"
HTTP_TIMEOUT_SECONDS = 20
# 凭据过期前多少秒就认为不可用
EXPIRY_MARGIN_SECONDS = 60


def dsh_home() -> Path:
    """定位 DeepSeek Harness 的 home（凭据库所在处）。"""
    configured = os.environ.get("DSH_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".dsh"


def region_of(domain: str) -> str:
    lowered = (domain or "").strip().lower()
    if lowered == "workbuddy.ai" or lowered.endswith(".workbuddy.ai"):
        return "global"
    if lowered == "codebuddy.ai" or lowered.endswith(".codebuddy.ai"):
        return "global"
    return "cn"


def _global_base(domain: str) -> str:
    lowered = (domain or "").strip().lower()
    if lowered in ("www.workbuddy.ai", "workbuddy.ai"):
        return "https://www.workbuddy.ai"
    if lowered == "codebuddy.ai" or lowered.endswith(".codebuddy.ai"):
        return "https://www.codebuddy.ai"
    return "https://www.workbuddy.ai"


def billing_base(domain: str) -> str:
    return _global_base(domain) if region_of(domain) == "global" else CN_BILLING_BASE


@dataclass(frozen=True)
class Credential:
    region: str
    access_token: str
    domain: str
    uid: str
    enterprise_id: str
    expires_at: int


@dataclass(frozen=True)
class CreditReading:
    total: float
    packages: list[dict[str, Any]]
    monthly_remain: float
    monthly_size: float
    nearest_expiry_ms: int | None


def _load_document(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def load_credentials(region: str = "cn") -> list[Credential]:
    """读取指定区域的全部可用凭据，按到期时间从晚到早排序。"""
    root = dsh_home() / "workbuddy-vault" / region
    if not root.is_dir():
        return []
    found: list[Credential] = []
    for path in sorted(root.glob("*.json")):
        document = _load_document(path)
        if document is None:
            continue
        auth = document.get("auth")
        account = document.get("account")
        if not isinstance(auth, dict) or not isinstance(account, dict):
            continue
        token = auth.get("accessToken")
        if not isinstance(token, str) or not token:
            continue
        expires_at = auth.get("expiresAt")
        found.append(
            Credential(
                region=region,
                access_token=token,
                domain=str(auth.get("domain") or ""),
                uid=str(account.get("uid") or ""),
                enterprise_id=str(auth.get("enterpriseId") or account.get("enterpriseId") or ""),
                expires_at=int(expires_at) if isinstance(expires_at, (int, float)) else 0,
            )
        )
    found.sort(key=lambda item: item.expires_at, reverse=True)
    return found


def _billing_headers(credential: Credential) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {credential.access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        # 腾讯计费网关会 403 掉 Python-urllib 的默认 UA，必须伪装成浏览器。
        # 实测（2026-11）：默认 UA → 403，浏览器 UA → 200。
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
        ),
    }
    if credential.uid:
        headers["X-User-Id"] = credential.uid
    if credential.enterprise_id:
        headers["X-Enterprise-Id"] = credential.enterprise_id
        headers["X-Tenant-Id"] = credential.enterprise_id
    if credential.domain:
        headers["X-Domain"] = credential.domain
    return headers


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = Request(url, data=payload, headers=headers, method="POST")
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8", "replace")
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("计费接口返回了非对象文档")
    return document


def _timestamp_format(moment: time.struct_time) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", moment)


def _parse_date(raw: Any) -> int | None:
    if isinstance(raw, (int, float)) and raw > 0xE8D4A51000:
        return int(raw)
    if isinstance(raw, str) and raw:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return int(time.mktime(time.strptime(raw, fmt)) * 1000)
            except ValueError:
                continue
    return None


def _precise(account: dict[str, Any], key: str) -> float:
    raw = account.get(f"{key}Precise")
    if isinstance(raw, str) and raw:
        try:
            return float(raw)
        except ValueError:
            pass
    if isinstance(raw, (int, float)):
        return float(raw)
    value = account.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def parse_credits(document: dict[str, Any]) -> CreditReading | None:
    """把计费接口响应解析成积分读数（个人账户口径）。"""
    code = document.get("code")
    if isinstance(code, (int, float)) and code != 0:
        return None
    wrapper = document.get("data") if isinstance(document.get("data"), dict) else {}
    response = wrapper.get("Response") if isinstance(wrapper.get("Response"), dict) else {}
    inner = response.get("Data") if isinstance(response.get("Data"), dict) else {}
    accounts = inner.get("Accounts")
    if not isinstance(accounts, list):
        return None

    now_ms = int(time.time() * 1000)
    total = 0.0
    monthly_remain = 0.0
    monthly_size = 0.0
    nearest_expiry: int | None = None
    packages: list[dict[str, Any]] = []
    for entry in accounts:
        if not isinstance(entry, dict):
            continue
        monthly = entry.get("CapacityType") == 4
        size = _precise(entry, "CycleCapacitySize") or _precise(entry, "CapacitySize")
        if "CycleCapacityRemain" in entry or "CycleCapacityRemainPrecise" in entry:
            remain = _precise(entry, "CycleCapacityRemain")
        else:
            remain = _precise(entry, "CapacityRemain")
        if remain < 0:
            remain = 0.0
        cycle_end = _parse_date(entry.get("CycleEndTime"))
        expires_at = None if monthly else (_parse_date(entry.get("ExpiredTime")) or cycle_end)
        # 一次性包过期或已用完的，不计入总额（与插件口径一致）
        if not monthly and (remain <= 0 or (expires_at is not None and expires_at <= now_ms)):
            continue
        total += remain
        if monthly:
            monthly_remain += remain
            monthly_size += size
        if expires_at is not None and (nearest_expiry is None or expires_at < nearest_expiry):
            nearest_expiry = expires_at
        packages.append(
            {
                "name": entry.get("PackageName") if isinstance(entry.get("PackageName"), str) else "",
                "remain": remain,
                "size": size,
                "monthly": monthly,
                "expires_at": expires_at,
            }
        )
    return CreditReading(
        total=total,
        packages=packages,
        monthly_remain=monthly_remain,
        monthly_size=monthly_size,
        nearest_expiry_ms=nearest_expiry,
    )


def fetch_credits(credential: Credential) -> CreditReading | None:
    """查询单个凭据的积分。失败返回 None（不抛异常，不含凭据日志）。"""
    now = time.localtime()
    body = {
        "PageNumber": 1,
        "PageSize": 100,
        "ProductCode": "p_tcaca",
        "Status": [0, 3],
        "PackageEndTimeRangeBegin": _timestamp_format(now),
        "PackageEndTimeRangeEnd": _timestamp_format(time.localtime(time.time() + 3185136e6 / 1000)),
    }
    url = f"{billing_base(credential.domain)}/v2/billing/meter/get-user-resource"
    try:
        document = _post_json(url, _billing_headers(credential), body)
    except (HTTPError, URLError, OSError, ValueError) as error:
        # 只记录类型与状态码，绝不记录凭据
        status = getattr(error, "code", None)
        LOGGER.warning("WorkBuddy 积分查询失败（%s）：%s", status or "network", type(error).__name__)
        return None
    return parse_credits(document)


def read_best_credits(region: str = "cn") -> CreditReading | None:
    """取该区域第一个能成功返回积分的凭据。"""
    now = time.time()
    for credential in load_credentials(region):
        if credential.expires_at and credential.expires_at - EXPIRY_MARGIN_SECONDS <= now:
            continue
        reading = fetch_credits(credential)
        if reading is not None:
            return reading
    return None
