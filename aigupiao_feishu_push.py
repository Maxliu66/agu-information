#!/usr/bin/env python3
"""
爱股票要闻 -> 飞书推送服务
通过GitHub Actions定时运行，电脑关机也能自动推送

功能：
- 定时轮询爱股票快讯API
- 按模式过滤要闻（all/important/hot/app_push/broad）
- 增量去重，已推送的不再重复
- 推送飞书群机器人webhook
- 状态持久化（本地JSON文件 / GitHub git commit）

用法：
  本地单次: python aigupiao_feishu_push.py
  CI单次:  python aigupiao_feishu_push.py --once
  本地持续: python aigupiao_feishu_push.py --continuous [间隔秒数]
  测试:    python aigupiao_feishu_push.py --test
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============== 配置 ==============

# 飞书 Webhook URL
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")

# 爱股票快讯API（直接硬编码，无需额外配置）
EXPRESS_LIST_URL = "https://apis.aigupiao.com/Express/express_list/"

# 状态文件路径（本地运行用）
STATE_FILE = Path(__file__).parent / "push_state.json"

# GitHub Actions Cache 路径（CI环境用）
GITHUB_CACHE_PATH = os.environ.get("GITHUB_CACHE_PATH", "")

# 要闻过滤模式：
#   "app_push"   - 仅推送 app_push=yes 的要闻（3-5条/天）
#   "important"  - 推送 important=yes 或 app_push=yes 或 important_db=yes（5-10条/天）
#   "hot"        - 推送 24h热门 或 important 或 app_push（10-20条/天）
#   "broad"      - hot + important_db + 阅读量>5000（30-50条/天）
#   "all"        - 推送所有要闻（100+条/天）
FILTER_MODE = os.environ.get("FILTER_MODE", "all")

# 每次获取条数
FETCH_NUMBER = int(os.environ.get("FETCH_NUMBER", "20"))

# 非交易时段静默（0-6点凌晨不推送，减少噪音）
NIGHT_SILENCE = os.environ.get("NIGHT_SILENCE", "true").lower() == "true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://stock.aigupiao.com/",
}


# ============== 数据获取 ==============


def fetch_express_list(before="0", after="", number=20):
    """获取爱股票快讯列表"""
    params = {
        "before": before,
        "source": "pc",
        "web_data": "yes",
        "number": str(number),
        "u_id": "",
        "division": "",
        "express_show_type": "1",
    }
    if after:
        params["after"] = after
        del params["before"]

    query = urllib.parse.urlencode(params)
    url = f"{EXPRESS_LIST_URL}?{query}"

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        return data
    except Exception as e:
        print(f"[ERROR] 获取快讯失败: {e}", file=sys.stderr)
        return None


def parse_news_items(api_data):
    """从API返回数据中解析新闻条目"""
    items = []
    if not api_data or api_data.get("rslt") != "succ":
        return items

    data = api_data.get("data", {})
    if not isinstance(data, dict):
        return items

    for date_key, date_data in data.items():
        if not isinstance(date_data, dict):
            continue
        news_list = date_data.get("data", [])
        if not isinstance(news_list, list):
            continue
        for item in news_list:
            items.append(item)

    return items


def filter_important_news(items, mode="all"):
    """根据模式过滤重要要闻"""
    if mode == "all":
        return items

    filtered = []
    for item in items:
        is_important = item.get("important") == "yes"
        is_app_push = item.get("app_push") == "yes"
        is_important_db = item.get("important_db") == "yes"
        is_24h_hot = item.get("is_24_hour_hot_news") == "yes"

        if mode == "important":
            if is_important or is_app_push or is_important_db:
                filtered.append(item)
        elif mode == "app_push":
            if is_app_push:
                filtered.append(item)
        elif mode == "hot":
            if is_24h_hot or is_important or is_app_push:
                filtered.append(item)
        elif mode == "broad":
            if is_24h_hot or is_important or is_app_push or is_important_db:
                filtered.append(item)
            else:
                try:
                    view_num = int(item.get("view_num", "0"))
                except (ValueError, TypeError):
                    view_num = 0
                if view_num >= 5000:
                    filtered.append(item)

    return filtered


def strip_html(text):
    """去除HTML标签"""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def format_content_lines(text):
    """将快讯内容格式化为分行显示"""
    # 在【标题】后换行
    text = re.sub(r'(】)\s*', r'\1\n', text)
    # 句号后空一行
    text = re.sub(r'。\s*', r'。\n\n', text)
    # 分号后换行
    text = re.sub(r'；\s*', r'；\n', text)
    # 编号列表前换行
    text = re.sub(r'(\d+、)\s*', r'\n\1', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text


# ============== 状态管理 ==============


def load_state():
    """加载推送状态"""
    # GitHub Actions 环境：从缓存文件读取
    if GITHUB_CACHE_PATH and Path(GITHUB_CACHE_PATH).exists():
        try:
            with open(GITHUB_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # 本地环境：从JSON文件读取
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {"pushed_ids": [], "last_sort_time": "0"}


def save_state(state):
    """保存推送状态"""
    state["updated_at"] = datetime.now(
        timezone(timedelta(hours=8))
    ).isoformat()

    # 保持 pushed_ids 列表不超过500条
    if len(state.get("pushed_ids", [])) > 500:
        state["pushed_ids"] = state["pushed_ids"][-500:]

    save_path = GITHUB_CACHE_PATH if GITHUB_CACHE_PATH else str(STATE_FILE)
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] 保存状态失败: {e}", file=sys.stderr)


# ============== 飞书推送 ==============


def build_feishu_card(news_item):
    """构建飞书消息卡片"""
    content = strip_html(news_item.get("content", "") or news_item.get("web_content", ""))
    if not content:
        return None

    news_id = news_item.get("id", "")
    rec_time_desc = news_item.get("rec_time_desc", "")
    theme = news_item.get("theme", "")
    is_important = news_item.get("important") == "yes"
    is_app_push = news_item.get("app_push") == "yes"
    is_24h_hot = news_item.get("is_24_hour_hot_news") == "yes"

    # 格式化内容
    content = format_content_lines(content)

    # 构建标题标记
    tags = []
    if is_app_push:
        tags.append("🔔推送")
    if is_important:
        tags.append("★重要")
    if is_24h_hot:
        tags.append("🔥热门")
    if theme:
        tags.append(f"#{theme}")

    title_suffix = f" {' '.join(tags)}" if tags else ""
    header_title = f"{rec_time_desc}{title_suffix}"

    # 卡片颜色：重要用红色，热门用橙色，普通用蓝色
    if is_important or is_app_push:
        template = "red"
    elif is_24h_hot:
        template = "orange"
    else:
        template = "blue"

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": header_title,
                },
                "template": template,
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content,
                },
            ],
        },
    }

    return card


def send_to_feishu(card_data):
    """发送消息到飞书webhook"""
    if not FEISHU_WEBHOOK_URL:
        print("[ERROR] 未设置 FEISHU_WEBHOOK_URL 环境变量", file=sys.stderr)
        return False

    try:
        payload = json.dumps(card_data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            FEISHU_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            return True
        else:
            print(f"[ERROR] 飞书推送失败: {result}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[ERROR] 飞书推送异常: {e}", file=sys.stderr)
        return False


# ============== 主逻辑 ==============


def is_night_time():
    """检查当前是否为凌晨静默时段（0-6点）"""
    now_hour = datetime.now(timezone(timedelta(hours=8))).hour
    return 0 <= now_hour < 6


def run():
    """执行一次轮询+推送"""
    print(f"[{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}] 开始轮询...")

    if not FEISHU_WEBHOOK_URL:
        print("[ERROR] 请设置环境变量 FEISHU_WEBHOOK_URL")
        print("  本地: set FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx")
        print("  CI:   在 GitHub Secrets 中添加 FEISHU_WEBHOOK_URL")
        sys.exit(1)

    # 凌晨静默检查
    if NIGHT_SILENCE and is_night_time():
        print("  凌晨0-6点静默期，跳过推送")
        return

    # 加载状态
    state = load_state()
    pushed_ids = set(state.get("pushed_ids", []))
    last_sort_time = state.get("last_sort_time", "0")

    # 获取最新要闻
    api_data = fetch_express_list(before="0", number=FETCH_NUMBER)
    if not api_data:
        print("[WARN] 未获取到数据，跳过本轮")
        return

    # 解析新闻条目
    all_items = parse_news_items(api_data)
    print(f"  获取到 {len(all_items)} 条快讯")

    # 过滤重要要闻
    important_items = filter_important_news(all_items, FILTER_MODE)
    print(f"  过滤后 {len(important_items)} 条要闻（模式: {FILTER_MODE}）")

    # 增量去重：ID去重 + 时间去重（双保险防重复）
    now_ts = int(time.time())
    # 只推送最近30分钟内的新闻（超过30分钟的视为旧闻，不推送）
    TIME_WINDOW = 30 * 60
    new_items = []
    for item in important_items:
        news_id = item.get("id")
        sort_time = int(item.get("sort_time", item.get("rec_time", "0")))
        if news_id in pushed_ids:
            continue
        if (now_ts - sort_time) > TIME_WINDOW:
            continue
        new_items.append(item)

    if not new_items:
        print("  无新要闻，跳过推送")
        if api_data.get("last_time"):
            state["last_sort_time"] = api_data["last_time"]
            save_state(state)
        return

    # 按时间正序推送（旧的先推）
    new_items.sort(key=lambda x: int(x.get("sort_time", x.get("rec_time", "0"))))

    print(f"  发现 {len(new_items)} 条新要闻，开始推送...")

    success_count = 0
    for item in new_items:
        card = build_feishu_card(item)
        if not card:
            continue

        if send_to_feishu(card):
            news_id = item.get("id")
            pushed_ids.add(news_id)
            state["pushed_ids"] = list(pushed_ids)
            success_count += 1
            content_preview = strip_html(item.get("content", ""))[:50]
            print(f"  OK [{item.get('rec_time_desc')}] {content_preview}...")
            time.sleep(0.5)
        else:
            content_preview = strip_html(item.get("content", ""))[:50]
            print(f"  FAIL [{item.get('rec_time_desc')}] {content_preview}...")

    # 更新状态
    if api_data.get("last_time"):
        state["last_sort_time"] = api_data["last_time"]
    save_state(state)

    print(f"  推送完成: {success_count}/{len(new_items)} 成功")


def run_continuous(interval=300):
    """持续运行模式（本地使用）"""
    print(f"=== 爱股票 -> 飞书推送服务 (每{interval}秒轮询) ===")
    print(f"过滤模式: {FILTER_MODE}")
    print(f"凌晨静默: {'开' if NIGHT_SILENCE else '关'}")
    print(f"按 Ctrl+C 停止\n")

    while True:
        try:
            run()
        except KeyboardInterrupt:
            print("\n已停止")
            break
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--once":
            run()
        elif cmd == "--continuous":
            interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300
            run_continuous(interval)
        elif cmd == "--test":
            print("=== 测试模式 ===")
            api_data = fetch_express_list(before="0", number=5)
            if api_data:
                items = parse_news_items(api_data)
                important = filter_important_news(items, FILTER_MODE)
                print(f"\n获取 {len(items)} 条快讯，过滤后 {len(important)} 条要闻:\n")
                for item in important:
                    imp = "★重要" if item.get("important") == "yes" else ""
                    push = "🔔推送" if item.get("app_push") == "yes" else ""
                    hot = "🔥热门" if item.get("is_24_hour_hot_news") == "yes" else ""
                    print(f"  {imp}{push}{hot} [{item.get('rec_time_desc')}] {strip_html(item.get('content', ''))[:80]}")
                print(f"\nlast_time: {api_data.get('last_time', 'N/A')}")
        else:
            print("用法:")
            print("  python aigupiao_feishu_push.py --once        # 单次运行（CI用）")
            print("  python aigupiao_feishu_push.py --continuous  # 持续运行（本地用）")
            print("  python aigupiao_feishu_push.py --test        # 测试模式（不推送）")
    else:
        run()
