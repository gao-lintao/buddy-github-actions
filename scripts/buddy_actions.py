#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy · Buddy加油站 云端自动任务客户端（纯标准库，零第三方依赖）

设计要点（对应需求）：
  1. 云端没有本机登录态：从 secret WORKBUDDY_REFRESH_TOKEN（长效令牌）换取接口令牌，
     每次运行都实时刷新，无需常开设备、无需读本地登录态。
  2. 同一次运行内完成：签到 + 派猫猫旅行（先领取已到家的旅行积分，再判断今天能否派新趟）。
  3. 微信推送：签到结论 与 猫猫状态 分两段（## 一、签到 / ## 二、猫猫旅行）展示。
  4. 猫猫失败不影响签到结论：签到成功即整体成功（退出码 0）。
  5. 令牌明文绝不打印、绝不入库；所有接口原始返回在输出前递归脱敏。
  6. 令牌到期提醒：根据 TOKEN_ISSUED + TOKEN_VALID_DAYS 估算剩余天数，剩余 ≤ TOKEN_WARN_DAYS
     时，在微信推送标题与正文加醒目续签提醒（双保险，避免忘记续签导致任务静默失效）。

接口名称均以实际请求返回为准（见下方 *_PATH 常量，已对照线上接口核对）。
"""

import os
import sys
import json
import time
import random
import urllib.request
import urllib.error
import urllib.parse

# ===================== 域名与接口（以实际请求返回为准）=====================
PLUGIN_API = "https://copilot.tencent.com"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"          # 长效令牌 → 接口令牌

BILLING_DOMAIN = "www.codebuddy.cn"
CHECKIN_STATUS_PATH = "/v2/billing/meter/checkin-activity-status"   # 状态查询（POST）
CHECKIN_PATH = "/v2/billing/meter/daily-checkin"                   # 签到（POST）

TRAVEL_DOMAIN = "www.workbuddy.cn"                                 # 旅行接口不带 /v2 前缀
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"        # GET
TRAVEL_CLAIM_PATH = "/activity/growth/buddy/travel/claim"          # POST 领取
TRAVEL_DEPART_PATH = "/activity/growth/buddy/travel/depart"        # POST 派遣
TRAVEL_CONFIG_PATH = "/activity/growth/buddy/travel/config"        # GET 地点配置

HTTP_TIMEOUT = 15

# 令牌有效期与到期提醒（续签时由 setup.py 自动回写为当天日期）
TOKEN_ISSUED = "2026-10-27"   # 最近一次写入 WORKBUDDY_TOKEN 的日期（估算基准）
TOKEN_VALID_DAYS = 28          # access token 有效期约 28 天
TOKEN_WARN_DAYS = 7            # 剩余天数 ≤ 此值时，微信推送加醒目续签提醒

# 需脱敏的字段（接口返回里绝不该出现的明文令牌/隐私）
_SENSITIVE_KEYS = ("accesstoken", "refreshtoken", "token", "authorization",
                   "x-refresh-token", "cookie", "openid", "unionid", "mobile", "email")


def log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stdout.flush()


def desensitize(obj):
    """递归脱敏：遮蔽敏感字段，返回可安全打印的对象。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(s in str(k).lower() for s in _SENSITIVE_KEYS):
                if isinstance(v, str) and len(v) > 12:
                    out[k] = v[:6] + "****" + v[-4:]
                else:
                    out[k] = "****"
            else:
                out[k] = desensitize(v)
        return out
    if isinstance(obj, list):
        return [desensitize(x) for x in obj]
    return obj


def beijing_hm(ts):
    """把秒级时间戳格式化为北京时 HH:MM（云端 runner 多为 UTC，统一 +8）。"""
    if not isinstance(ts, (int, float)):
        return ""
    return time.strftime("%H:%M", time.gmtime(ts + 8 * 3600))


def token_days_left():
    """根据 TOKEN_ISSUED + TOKEN_VALID_DAYS 估算令牌剩余天数（仅作提醒参考）。"""
    try:
        issued = time.mktime(time.strptime(TOKEN_ISSUED, "%Y-%m-%d"))
        return int(TOKEN_VALID_DAYS - (time.time() - issued) / 86400)
    except Exception:
        return None


def _http(domain, path, token, method="POST", payload=None):
    url = "https://" + domain + path
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    else:
        body = b"{}" if method == "POST" else None
    req = urllib.request.Request(url, method=method, data=body)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"code": -1, "msg": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return 0, {"code": -1, "msg": str(e)[:200]}


def get_access_token(rt):
    """用长效令牌换取接口令牌。返回 (access_token, rotated_refresh_token_or_None)。"""
    url = PLUGIN_API + REFRESH_PATH
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Refresh-Token", rt)
    req.add_header("X-Auth-Refresh-Source", "plugin")
    req.add_header("X-Domain", "copilot.tencent.com")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            raise RuntimeError("长效令牌刷新 HTTP %s" % e.code)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("长效令牌刷新失败：%s" % e)
    if body.get("code") != 0 or not isinstance(body.get("data"), dict):
        raise RuntimeError("长效令牌刷新失败 code=%s %s（令牌可能已失效，请重新导出）"
                           % (body.get("code"), body.get("msg") or ""))
    data = body["data"]
    at = data.get("accessToken") or ""
    new_rt = data.get("refreshToken") or ""
    if not at:
        raise RuntimeError("刷新应答缺少接口令牌")
    return at, (new_rt if new_rt and new_rt != rt else None)


# ===================== 签到 =====================
def do_checkin(token):
    """签到（幂等）。今日已签返回 skipped，均视为成功。"""
    st, body = _http(BILLING_DOMAIN, CHECKIN_STATUS_PATH, token, method="POST")
    if st != 200 or not isinstance(body, dict) or body.get("code") != 0:
        return {"action": "failed", "success": False,
                "message": "查询签到状态失败 HTTP %s %s" % (st, body.get("msg")),
                "balance": None, "raw": desensitize(body)}
    data = body.get("data", {})
    balance = data.get("total_credits")
    if data.get("today_checked_in"):
        return {"action": "skipped", "success": True,
                "message": "今日已签到，无需重复打卡", "balance": balance,
                "raw": desensitize(body)}
    st2, chk = _http(BILLING_DOMAIN, CHECKIN_PATH, token, method="POST", payload={})
    code = chk.get("code")
    msg = chk.get("msg")
    if st2 == 200 and code == 0:
        _, after = _http(BILLING_DOMAIN, CHECKIN_STATUS_PATH, token, method="POST")
        new_bal = after.get("data", {}).get("total_credits") if isinstance(after, dict) else None
        return {"action": "checked_in", "success": True,
                "message": msg or "签到成功", "balance": new_bal,
                "raw": desensitize(chk)}
    if code == 10001 and "已签到" in (msg or ""):
        return {"action": "skipped", "success": True, "message": msg or "今日已签到",
                "balance": balance, "raw": desensitize(chk)}
    return {"action": "failed", "success": False,
            "message": msg or ("HTTP %s" % st2), "balance": balance,
            "raw": desensitize(chk)}


# ===================== 派猫猫旅行 =====================
def _travel_get(token, path):
    st, body = _http(TRAVEL_DOMAIN, path, token, method="GET")
    if st != 200 or not isinstance(body, dict) or body.get("code") != 0:
        return None
    return body.get("data")


def do_travel(token):
    """派猫猫旅行：① 先领取已到家的旅行积分；② 再判断今天能否派新趟（已派则跳过）。"""
    result = {"available": False, "claimed": None, "departed": None,
              "state": None, "message": "", "raw": {}}
    status = _travel_get(token, TRAVEL_STATUS_PATH)
    if status is None:
        result["message"] = "查询旅行状态失败（接口/网络异常）"
        return result
    result["available"] = True
    result["state"] = status.get("state")
    result["raw"]["status"] = desensitize(status)

    # ① 先领取已经到家的旅行积分
    if status.get("state") == "arrived":
        st, claim = _http(TRAVEL_DOMAIN, TRAVEL_CLAIM_PATH, token, method="POST", payload={})
        claim_data = claim.get("data", {}) if isinstance(claim, dict) else {}
        if st == 200 and isinstance(claim, dict) and claim.get("code") == 0:
            result["claimed"] = {"ok": True, "reward": claim_data.get("reward_credit"),
                                 "message": "已领取到家旅行奖励 +%s" % claim_data.get("reward_credit")}
        else:
            result["claimed"] = {"ok": False, "message": claim.get("msg") or ("HTTP %s" % st)}
        result["raw"]["claim"] = desensitize(claim)
        fresh = _travel_get(token, TRAVEL_STATUS_PATH)
        if fresh is not None:
            status = fresh
            result["state"] = status.get("state")
            result["raw"]["status"] = desensitize(status)

    # ② 判断今天还能不能派新的一趟
    if status.get("state") in (None, "", "idle"):
        if status.get("daily_limit_reached"):
            result["departed"] = {"ok": False, "skipped": True,
                                  "message": "今日派遣已达上限，跳过"}
        else:
            config = _travel_get(token, TRAVEL_CONFIG_PATH) or {}
            locations = (config.get("locations") or []) if isinstance(config, dict) else []
            ids = [l.get("id") for l in locations if l.get("id") is not None]
            loc_id = random.choice(ids) if ids else None
            st, dep = _http(TRAVEL_DOMAIN, TRAVEL_DEPART_PATH, token, method="POST",
                            payload={"location_id": loc_id} if loc_id is not None else {})
            dep_data = dep.get("data", {}) if isinstance(dep, dict) else {}
            if st == 200 and isinstance(dep, dict) and dep.get("code") == 0:
                loc = dep_data.get("location") or {}
                back = beijing_hm(dep_data.get("arrive_at"))
                result["departed"] = {"ok": True, "location": loc.get("name"), "back_at": back,
                                      "message": "已派往【%s】，预计 %s(北京) 返回"
                                                 % (loc.get("name"), back or "?")}
            else:
                result["departed"] = {"ok": False, "message": dep.get("msg") or ("HTTP %s" % st)}
            result["raw"]["depart"] = desensitize(dep)
    else:
        # traveling / 其他状态：当天已经派过
        result["departed"] = {"ok": False, "skipped": True,
                              "message": "Buddy 正在旅行中（当天已派过），跳过新趟"}

    parts = []
    if result["claimed"]:
        parts.append("领取：%s" % result["claimed"]["message"])
    if result["departed"]:
        parts.append("派遣：%s" % result["departed"]["message"])
    result["message"] = "；".join(parts) or "无需操作（无待领 / 当天已派过）"
    return result


# ===================== 微信推送 =====================
def push_wechat(title, desp):
    sckey = os.environ.get("SERVERCHAN_SCKEY")
    if sckey:
        try:
            url = "https://sctapi.ftqq.com/%s.send" % sckey
            data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                r = json.loads(resp.read().decode("utf-8", "replace"))
            log("微信推送(ServerChan)：%s" % (r.get("message") or r.get("code")))
            return
        except Exception as e:  # noqa: BLE001
            log("⚠️ ServerChan 推送失败：%s" % e)
    ptoken = os.environ.get("PUSHPLUS_TOKEN")
    if ptoken:
        try:
            url = "https://www.pushplus.plus/send"
            body = json.dumps({"token": ptoken, "title": title,
                               "content": desp, "template": "markdown"}).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                r = json.loads(resp.read().decode("utf-8", "replace"))
            log("微信推送(PushPlus)：%s" % r.get("msg"))
            return
        except Exception as e:  # noqa: BLE001
            log("⚠️ PushPlus 推送失败：%s" % e)
    log("未配置微信推送密钥（SERVERCHAN_SCKEY / PUSHPLUS_TOKEN），跳过推送。")


def build_desp(ck, tv):
    lines = []
    lines.append("## 一、签到结论")
    lines.append("- 状态：**%s**" % ("成功" if ck.get("success") else "失败"))
    lines.append("- 详情：%s" % ck.get("message"))
    if ck.get("balance") is not None:
        lines.append("- 当前积分余额：**%s**" % ck["balance"])
    lines.append("")
    lines.append("## 二、猫猫旅行")
    if tv.get("available"):
        lines.append("- 旅行状态：%s" % tv.get("state"))
        if tv.get("claimed"):
            lines.append("- 领取到家奖励：%s" % tv["claimed"]["message"])
        if tv.get("departed"):
            lines.append("- 派遣新趟：%s" % tv["departed"]["message"])
        if not tv.get("claimed") and not tv.get("departed"):
            lines.append("- 本次无需操作（无待领 / 当天已派过）")
    else:
        lines.append("- %s" % (tv.get("message") or "查询失败"))
    lines.append("")

    # 令牌到期提醒（双保险：即使一次性定时提醒被忽略，每日推送也会反复提示）
    days_left = token_days_left()
    if days_left is not None and days_left <= TOKEN_WARN_DAYS:
        lines.append("⚠️ **WORKBUDDY_TOKEN 将于 %d 天后到期，请尽快续签！**" % days_left)
        lines.append("续签方式：直接对 WorkBuddy 说「帮我续签 WORKBUDDY_TOKEN」即可自动续写。")

    lines.append("")
    lines.append("> 自动任务 · %s" % time.strftime("%Y-%m-%d %H:%M"))
    return "\n".join(lines)


# ===================== 主流程 =====================
def main():
    # 令牌来源（二选一，优先直接接口令牌）：
    #   WORKBUDDY_TOKEN          —— 本机导出的接口令牌（access token，约 28 天有效，定期用刷新脚本重导）
    #   WORKBUDDY_REFRESH_TOKEN  —— 长效令牌，云端实时刷新接口令牌（自续期，set-and-forget）
    token = None
    rt = os.environ.get("WORKBUDDY_REFRESH_TOKEN")
    if rt:
        try:
            token, rotated = get_access_token(rt)
            log("✅ 已由长效令牌刷新得到接口令牌（已脱敏，不打印明文）"
                + ("；并检测到令牌轮换已回写" if rotated else ""))
        except Exception as e:  # noqa: BLE001
            log("⚠️ 长效令牌刷新失败：%s；回退尝试 WORKBUDDY_TOKEN" % e)

    if not token:
        at = os.environ.get("WORKBUDDY_TOKEN")
        if not at:
            log("❌ 未找到 WORKBUDDY_TOKEN / WORKBUDDY_REFRESH_TOKEN，"
                "请先在本机运行 scripts/refresh_secret.sh 写入仓库 secret。")
            return 2
        token = at
        log("✅ 已取得接口令牌（来自 WORKBUDDY_TOKEN，已脱敏不打印明文）")

    # 2) 签到（决定整体成败）
    try:
        ck = do_checkin(token)
        log("签到：%s（余额 %s）" % (ck["message"], ck.get("balance")))
    except Exception as e:  # noqa: BLE001
        ck = {"action": "failed", "success": False,
              "message": "签到异常：%s" % e, "balance": None, "raw": {}}
        log("❌ %s" % ck["message"])

    # 3) 派猫（失败不影响签到结论）
    try:
        tv = do_travel(token)
        log("猫猫：%s" % tv["message"])
    except Exception as e:  # noqa: BLE001
        tv = {"available": False, "message": "猫猫流程异常：%s" % e, "raw": {}}
        log("⚠️ 猫猫流程异常（不影响签到结论）：%s" % e)

    # 4) 组装并推送（两段分明 + 令牌到期提醒）
    days_left = token_days_left()
    if days_left is not None and days_left <= TOKEN_WARN_DAYS:
        title = "⚠️令牌%d天后到期 · %s" % (days_left, "签到成功" if ck.get("success") else "签到失败")
    else:
        title = "Buddy加油站 · %s" % ("签到成功" if ck.get("success") else "签到失败")
    desp = build_desp(ck, tv)
    push_wechat(title, desp)

    # 5) 打印脱敏原始返回（验收用，绝不出现令牌）
    log("---- 脱敏后的原始接口返回（验收）----")
    log(json.dumps({"checkin_raw": ck.get("raw"), "travel_raw": tv.get("raw")},
                   ensure_ascii=False, indent=2))

    return 0 if ck.get("success") else 3


if __name__ == "__main__":
    raise SystemExit(main())
