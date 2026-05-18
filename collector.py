#!/usr/bin/env python3
"""
Mod Collector - 自动搜集各平台游戏 Mod
数据持久化 + HTML 报告生成 + 邮件发送

Usage:
    python3 collector.py daily     # 每日更新（按最新更新排序）
    python3 collector.py weekly    # 每周精选（按评分+受欢迎排序）
"""

import json
import os
import sys
import time
import hashlib
import urllib.request
import urllib.parse
import urllib.error
import subprocess
import re
from datetime import datetime, timedelta
from html import escape

# === 配置 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_DIR = os.path.join(BASE_DIR, "secrets")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_secret(name):
    """从 secrets 目录加载密钥。"""
    path = os.path.join(SECRETS_DIR, name)
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return ""

def get_nexus_key(cfg):
    """获取 Nexus API key：环境变量 > secrets 文件 > config.json"""
    return os.environ.get("NEXUS_API_KEY") or load_secret("nexus-api-key") or cfg["settings"].get("nexus_api_key", "")

def get_github_token(cfg):
    """获取 GitHub token：环境变量 > secrets 文件 > gh CLI > config.json"""
    # 环境变量
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    # secrets 文件
    token = load_secret("github-token")
    if token:
        return token
    # gh CLI
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except:
        pass
    # config.json
    return cfg["settings"].get("github_token", "")

def get_data_dir(cfg):
    d = cfg["settings"]["data_dir"]
    # 支持相对路径（相对于 config.json 所在目录）
    if not os.path.isabs(d):
        d = os.path.join(BASE_DIR, d)
    os.makedirs(d, exist_ok=True)
    return d

def get_db_path(cfg):
    return os.path.join(get_data_dir(cfg), "mods_db.json")

# === 数据持久化（JSON 文件） ===
def load_db(cfg):
    path = get_db_path(cfg)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"mods": {}, "last_run": None, "run_history": []}

def save_db(cfg, db):
    path = get_db_path(cfg)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def mod_id(mod):
    """生成 mod 唯一 ID（用于去重）"""
    raw = f"{mod.get('name','')}|{mod.get('author','')}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:12]

def merge_mods(db, new_mods, show_weight_decay=0.7):
    """合并新数据到数据库，按名称+作者去重。
    
    权重机制：
    - 新 mod 初始权重 = 1.0
    - 每次被选中展示后，权重衰减（默认 *0.7，最小 0.2）
    - 检测到更新时间变化时，权重重置为 1.0
    """
    existing = db["mods"]
    added = 0
    updated = 0
    updated_ids = set()  # 记录本次更新的 mod IDs
    
    for m in new_mods:
        mid = mod_id(m)
        m["last_seen"] = datetime.now().isoformat()
        
        if mid not in existing:
            m["first_seen"] = m["last_seen"]
            m["show_weight"] = 1.0
            existing[mid] = m
            added += 1
        else:
            old = existing[mid]
            # 检测更新时间是否变化
            old_time = old.get("last_updated", "")
            new_time = m.get("last_updated", "")
            if new_time and new_time != old_time:
                # Mod 有更新，重置权重
                old["show_weight"] = 1.0
                updated_ids.add(mid)
            
            for k in ("rating", "downloads", "endorsements", "url", "image", "description", "last_updated", "language", "stars"):
                if k in m and m[k]:
                    old[k] = m[k]
            old["last_seen"] = m["last_seen"]
            # 新 mod 初始化权重
            if "show_weight" not in old:
                old["show_weight"] = 1.0
            updated += 1
    
    # 对所有未被选中展示的 mod 进行权重衰减（防止老 mod 权重过低）
    for mid, mod in existing.items():
        if mid not in updated_ids and mid not in [mod_id(m) for m in new_mods]:
            pass  # 不在本次数据中的 mod 不受影响
    
    db["mods"] = existing
    return added, updated

# === HTTP 请求（纯 stdlib） ===
def http_get(url, headers=None, timeout=30, retries=2):
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; ModCollector/1.0)"}
    if headers:
        hdrs.update(headers)
    
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                # Rate limited - wait and retry
                wait = min(2 ** attempt, 10)
                time.sleep(wait)
                continue
            print(f"  [WARN] HTTP {e.code}")
            return None
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  [WARN] {e}")
            return None
    return None

def http_get_json(url, headers=None, timeout=30):
    text = http_get(url, headers, timeout)
    if text:
        try:
            return json.loads(text)
        except:
            return None
    return None

# === 翻译（MyMemory 免费 API） ===
_translate_cache = {}
_translate_queue = []

def translate_batch(texts, max_len=200):
    """批量翻译多个文本，减少 API 调用次数。"""
    results = []
    for text in texts:
        if not text:
            results.append("")
        elif any('\u4e00' <= c <= '\u9fff' for c in text[:50]):
            results.append(text[:max_len])
        else:
            results.append(text)
    # 批量调用翻译 API（合并前 5 个文本）
    en_texts = [(i, t) for i, t in enumerate(results) if t and not any('\u4e00' <= c <= '\u9fff' for c in t[:50])]
    if not en_texts:
        return results
    
    # 每次最多翻译 3 个文本
    batch_size = 3
    for i in range(0, len(en_texts), batch_size):
        batch = en_texts[i:i+batch_size]
        combined = " ||| ".join(t for _, t in batch)
        try:
            url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({
                "q": combined[:1500],
                "langpair": "en|zh-CN"
            })
            data = http_get_json(url, timeout=15)
            if data and "responseData" in data:
                translated = data["responseData"]["translatedText"]
                parts = translated.split(" ||| ")
                for (orig_idx, _), part in zip(batch, parts):
                    results[orig_idx] = part[:max_len]
        except:
            pass
        time.sleep(1.5)  # 避免频率限制
    return results

def translate_to_zh(text, max_len=200):
    """单个文本翻译（带缓存）"""
    if not text:
        return ""
    if text in _translate_cache:
        return _translate_cache[text]
    if any('\u4e00' <= c <= '\u9fff' for c in text[:50]):
        return text[:max_len]
    try:
        url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({
            "q": text[:500],
            "langpair": "en|zh-CN"
        })
        data = http_get_json(url, timeout=10)
        if data and "responseData" in data:
            result = data["responseData"]["translatedText"][:max_len]
            _translate_cache[text] = result
            return result
    except:
        pass
    _translate_cache[text] = text[:max_len]
    return text[:max_len]

# === 平台 Fetchers ===

def fetch_nexus(game_en, sort="endorsements", cfg=None):
    """Nexus Mods API v1"""
    api_key = get_nexus_key(cfg) if cfg else ""
    results = []
    if not api_key:
        print("  [SKIP] Nexus Mods: 需要 API key")
        return results
    
    game_ids = {"Stardew Valley": "1303"}
    gid = game_ids.get(game_en)
    if not gid:
        print(f"  [SKIP] Nexus Mods: 未找到游戏 '{game_en}' 专区")
        return results
    
    domain = "stardewvalley" if gid == "1303" else ""
    headers = {"apikey": api_key, "User-Agent": "ModCollector/1.0"}
    
    # 步骤1：获取 mod ID 列表
    if sort == "-last_updated":
        url = f"https://api.nexusmods.com/v1/games/{gid}/mods/updated.json?period=1w"
    else:
        # 获取最近活跃的（用 1 个月数据作为近似）
        url = f"https://api.nexusmods.com/v1/games/{gid}/mods/updated.json?period=1m"
    
    mod_list = http_get_json(url, headers)
    if not mod_list or not isinstance(mod_list, list):
        print(f"  [WARN] 无法获取 mod 列表")
        return results
    
    # 取前 N 个
    mod_ids = [m["mod_id"] for m in mod_list[:25]]
    
    # 步骤2：获取每个 mod 的详细信息
    import re
    for mid in mod_ids:
        url = f"https://api.nexusmods.com/v1/games/{gid}/mods/{mid}.json"
        data = http_get_json(url, headers)
        if data and isinstance(data, dict):
            rating = data.get("endorsement_count", 0)
            desc = data.get("summary", "") or ""
            desc = re.sub(r'\[/?\w+[^\]]*\]', '', desc).strip()[:200]
            results.append({
                "name": data.get("name", ""),
                "author": data.get("author", ""),
                "description": desc,
                "rating": float(rating),
                "endorsements": rating,
                "downloads": data.get("mod_downloads", 0),
                "url": f"https://www.nexusmods.com/{domain}/mods/{mid}",
                "image": data.get("picture_url", ""),
                "last_updated": datetime.fromtimestamp(data.get("updated_timestamp", 0)).isoformat() if data.get("updated_timestamp") else "",
                "platform": "nexus",
            })
        time.sleep(0.5)  # 避免限流
    
    return results

def fetch_thunderstore(game_en, sort="-rating", cfg=None):
    """Thunderstore API v1"""
    # Thunderstore community slugs (URL 中的格式)
    community_map = {
        "REPO": "r-e-p-o",
        "Dyson Sphere Program": "dyson-sphere-program",
        "Factorio": None,
    }
    community = community_map.get(game_en)
    if not community:
        print(f"  [SKIP] Thunderstore: 未找到游戏 '{game_en}' 社区")
        return []
    
    order = "rating" if sort == "-rating" else "last_updated"
    url = f"https://thunderstore.io/api/v1/package/?community={community}&ordering=-{order}&page_size=25"
    data = http_get_json(url, timeout=60)
    
    results = []
    if data and isinstance(data, list):
        # 限制处理数量，避免超时
        for item in data[:30]:
            versions = item.get("versions", [])
            latest = versions[0] if versions else {}
            results.append({
                "name": item.get("name", ""),
                "author": item.get("owner", ""),
                "description": (item.get("description", "") or latest.get("description", ""))[:200],
                "rating": item.get("rating_score", 0),
                "downloads": item.get("download_count", 0),
                "endorsements": item.get("rating_score", 0),
                "url": item.get("package_url", ""),
                "image": latest.get("icon", ""),
                "last_updated": latest.get("date_created", "") or item.get("date_updated", ""),
                "platform": "thunderstore"
            })
    return results

def fetch_github(game_en, sort="updated", cfg=None):
    """GitHub Search API"""
    query = f"{game_en} mod"
    sort_param = "updated" if sort == "-last_updated" else "stars"
    
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&sort={sort_param}&order=desc&per_page=25"
    headers = {}
    token = get_github_token(cfg) if cfg else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = http_get_json(url, headers=headers, timeout=30)
    
    results = []
    if data and "items" in data:
        for item in data["items"]:
            desc = item.get("description", "") or ""
            results.append({
                "name": item.get("full_name", "").split("/")[-1],
                "author": item.get("owner", {}).get("login", ""),
                "description": desc[:200],
                "rating": item.get("stargazers_count", 0),
                "downloads": item.get("forks_count", 0),
                "endorsements": item.get("stargazers_count", 0),
                "url": item.get("html_url", ""),
                "image": "",
                "last_updated": item.get("pushed_at", ""),
                "platform": "github",
                "language": item.get("language", ""),
                "stars": item.get("stargazers_count", 0)
            })
    return results

def fetch_moddb(game_en, sort="date", cfg=None):
    """ModDB 搜索（HTML 解析）"""
    # ModDB 搜索 URL - 使用正确的路径
    sort_val = "name" if sort == "-last_updated" else "visits"
    url = f"https://www.moddb.com/search?q={urllib.parse.quote(game_en)}&filter=mods&sort={sort_val}"
    results = []
    text = http_get(url, timeout=30)
    if not text:
        # 尝试备用 URL
        url = f"https://www.moddb.com/mods?filter=t&kw={urllib.parse.quote(game_en)}"
        text = http_get(url, timeout=30)
    if not text:
        print(f"  [INFO] ModDB 未找到内容")
        return results
    
    # 解析 mod 卡片 - 多种模式匹配
    patterns = [
        r'<a[^>]+href="(/mods/[^"]+)"[^>]+class="heading"[^>]*>([^<]+)</a>',
        r'<a[^>]+href="(/mods/[^"]+)"[^>]*>([^<]+)</a>',
    ]
    links = []
    for pattern in patterns:
        links = re.findall(pattern, text)
        if links:
            break
    
    seen = set()
    for path, title in links[:25]:
        if path in seen or '/mods/' not in path:
            continue
        seen.add(path)
        results.append({
            "name": title.strip(),
            "author": "",
            "description": "",
            "rating": 0,
            "downloads": 0,
            "endorsements": 0,
            "url": f"https://www.moddb.com{path}",
            "image": "",
            "last_updated": "",
            "platform": "moddb"
        })
    return results

# 平台分派
FETCHERS = {
    "nexus": fetch_nexus,
    "thunderstore": fetch_thunderstore,
    "github": fetch_github,
    "moddb": fetch_moddb,
}

def collect_all(cfg, sort_mode):
    """搜集所有游戏的 mod"""
    db = load_db(cfg)
    min_rating = cfg["settings"].get("min_rating", 3.0)
    min_endorsements = cfg["settings"].get("min_endorsements", 10)
    
    all_results = {}
    total_added = 0
    total_updated = 0
    
    for game in cfg["games"]:
        game_id = game["id"]
        game_name_zh = game["name_zh"]
        game_name_en = game["name_en"]
        platforms = game.get("platforms", [])
        
        print(f"\n{'='*40}")
        print(f"  {game_name_zh} ({game_name_en})")
        print(f"{'='*40}")
        game_mods = []
        
        for platform in platforms:
            fetcher = FETCHERS.get(platform)
            if not fetcher:
                print(f"  [SKIP] 未知平台: {platform}")
                continue
            
            print(f"  [{platform}] ...", end="", flush=True)
            try:
                mods = fetcher(game_name_en, sort=sort_mode, cfg=cfg)
                print(f" {len(mods)} 个")
                game_mods.extend(mods)
                # 避免请求过快
                time.sleep(1)
            except Exception as e:
                print(f" 错误: {e}")
        
        # 平台内去重
        seen = set()
        unique_mods = []
        for m in game_mods:
            mid = mod_id(m)
            if mid not in seen:
                seen.add(mid)
                unique_mods.append(m)
        game_mods = unique_mods
        
        # 过滤：1) 无更新时间的 mod 不计入统计  2) 低评分过滤
        has_time_mods = [m for m in game_mods if m.get("last_updated")]
        no_time_count = len(game_mods) - len(has_time_mods)
        
        filtered = [m for m in has_time_mods 
                    if m.get("rating", 0) >= min_rating 
                    or m.get("endorsements", 0) >= min_endorsements]
        
        # 权重轮显机制：
        # - 新 mod 初始权重 1.0
        # - 被选中展示后，权重衰减（默认 *0.85）
        # - 未展示时，每次运行权重恢复（默认 +0.05）
        # - 权重范围 [0.3, 1.0]
        # - 检测到更新时间变化，权重重置为 1.0
        show_weight_decay = cfg["settings"].get("show_weight_decay", 0.85)
        show_weight_min = cfg["settings"].get("show_weight_min", 0.3)
        show_weight_restore = cfg["settings"].get("show_weight_restore", 0.05)
        show_weight_max = 1.0
        
        # 为本次候选 mod 赋权重（从 DB 读取或初始化为 1.0）
        for m in filtered:
            mid = mod_id(m)
            if mid in db["mods"]:
                m["show_weight"] = db["mods"][mid].get("show_weight", 1.0)
            else:
                m["show_weight"] = 1.0
        
        # 按 score * weight 排序取前 N
        def weighted_score(m):
            return (m.get("rating", 0) + m.get("endorsements", 0)) * m.get("show_weight", 1.0)
        filtered.sort(key=weighted_score, reverse=True)
        top_mods = filtered[:cfg["settings"].get("mods_per_game", 20)]
        
        # 更新 DB 中权重：展示的衰减，未展示的恢复
        filtered_ids = set(mod_id(m) for m in filtered)
        top_ids = set(mod_id(m) for m in top_mods)
        for mid in filtered_ids:
            if mid not in db["mods"]:
                continue
            if mid in top_ids:
                # 被展示：衰减
                db["mods"][mid]["show_weight"] = max(
                    db["mods"][mid].get("show_weight", 1.0) * show_weight_decay,
                    show_weight_min
                )
            else:
                # 未展示：恢复
                db["mods"][mid]["show_weight"] = min(
                    db["mods"][mid].get("show_weight", 1.0) + show_weight_restore,
                    show_weight_max
                )
        
        # 合并到数据库
        added, updated = merge_mods(db, top_mods)
        total_added += added
        total_updated += updated
        
        all_results[game_id] = {
            "name_zh": game_name_zh,
            "name_en": game_name_en,
            "mods": top_mods,
            "total_found": len(game_mods),
            "total_filtered": len(filtered)
        }
        
        print(f"  -> 过滤后 {len(filtered)} 个（{no_time_count} 无更新时间已排除），取 TOP {len(top_mods)}，新增 {added}，更新 {updated}")
    
    # 更新运行记录
    now = datetime.now().isoformat()
    db["last_run"] = now
    db["run_history"].append({
        "time": now,
        "mode": "weekly" if sort_mode == "-rating" else "daily",
        "total_added": total_added,
        "total_updated": total_updated
    })
    db["run_history"] = db["run_history"][-100:]
    
    save_db(cfg, db)
    return all_results, total_added, total_updated

# === HTML 报告生成 ===
def generate_html(results, mode, cfg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode_label = "📅 每日最新 Mod 更新" if mode == "daily" else "⭐ 每周精选 Mod 推荐"
    sort_label = "最新更新" if mode == "daily" else "评分最高 / 最受欢迎"
    
    platform_names = {
        "nexus": "Nexus Mods",
        "thunderstore": "Thunderstore",
        "github": "GitHub",
        "moddb": "ModDB"
    }
    platform_colors = {
        "nexus": "#f05b2a",
        "thunderstore": "#1a73e8",
        "github": "#333333",
        "moddb": "#2c3e50"
    }
    
    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mod 搜集报告 - {now}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#f5f5f5;color:#333;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:20px}}
.header{{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:30px;border-radius:12px;margin-bottom:20px}}
.header h1{{font-size:24px;margin-bottom:8px}}
.header p{{opacity:0.9;font-size:14px}}
.game-section{{background:#fff;border-radius:12px;margin-bottom:20px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08)}}
.game-header{{background:#f8f9fa;padding:16px 20px;border-bottom:1px solid #eee}}
.game-header h2{{font-size:20px}}
.game-header .stats{{font-size:12px;color:#888;margin-top:4px}}
.mod-item{{padding:16px 20px;border-bottom:1px solid #f0f0f0;display:flex;align-items:flex-start;gap:14px}}
.mod-item:last-child{{border-bottom:none}}
.mod-item:hover{{background:#fafafa}}
.mod-rank{{font-size:22px;font-weight:bold;color:#667eea;min-width:30px}}
.mod-info{{flex:1}}
.mod-name{{font-size:16px;font-weight:600;margin-bottom:4px}}
.mod-name a{{color:#333;text-decoration:none}}
.mod-name a:hover{{color:#667eea}}
.mod-desc{{font-size:13px;color:#666;margin:6px 0}}
.mod-meta{{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#999}}
.mod-meta span{{display:flex;align-items:center;gap:4px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;color:#fff}}
.summary{{background:#fff;border-radius:12px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}}
.summary h3{{margin-bottom:10px}}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>{mode_label}</h1>
<p>生成时间：{now} ｜ 排序方式：{sort_label} ｜ 共 {len(results)} 款游戏</p>
</div>""")
    
    total_mods = 0
    for game_id, gdata in results.items():
        mods = gdata["mods"]
        total_mods += len(mods)
        
        parts.append(f"""
<div class="game-section">
<div class="game-header">
<h2>{gdata['name_zh']} <small style="color:#999;font-size:13px">{gdata['name_en']}</small></h2>
<div class="stats">发现 {gdata['total_found']} 个，过滤后 {gdata['total_filtered']} 个，展示 TOP {len(mods)}</div>
</div>""")
        
        for i, mod in enumerate(mods, 1):
            platform = mod.get("platform", "")
            color = platform_colors.get(platform, "#999")
            pname = platform_names.get(platform, platform)
            rating = mod.get("rating", 0)
            downloads = mod.get("downloads", 0)
            author = mod.get("author", "")
            desc = mod.get("description", "")[:150]
            url = mod.get("url", "#")
            last_updated = mod.get("last_updated", "")
            if last_updated:
                try:
                    dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    time_str = last_updated[:16]
            else:
                time_str = ""
            
            parts.append(f"""
<div class="mod-item">
<div class="mod-rank">{i}</div>
<div class="mod-info">
<div class="mod-name"><a href="{escape(url)}">{escape(mod['name'])}</a></div>
<div class="mod-meta">
<span class="badge" style="background:{color}">{pname}</span>
{'<span>👤 ' + escape(author) + '</span>' if author else ''}
{'<span>🕐 ' + escape(time_str) + '</span>' if time_str else ''}
<span>⭐ {rating}</span>
<span>📥 {downloads:,}</span>
</div>
{'<div class="mod-desc">' + escape(desc) + '</div>' if desc else ''}
</div>
</div>""")
        
        parts.append("</div>")
    
    parts.append(f"""
<div class="summary">
<h3>本次统计摘要</h3>
<p>共搜集 <strong>{total_mods}</strong> 个 Mod，覆盖 {len(results)} 款游戏</p>
<p style="margin-top:8px;color:#999;font-size:12px">由 Mod Collector 自动生成 ｜ 数据来源：{', '.join(s['name'] for s in cfg['sources'])}</p>
</div>
</div>
</body>
</html>""")
    
    return "".join(parts)

# === 邮件发送 ===
def send_email(to, subject, html_content, cfg):
    """发送邮件，HTML 内容作为正文。"""
    # 从配置读取邮件发送脚本路径
    cmd_str = cfg["settings"].get("send_email_script", "")
    if not cmd_str:
        print("\n未配置 send_email_script，跳过邮件发送")
        return
    # 解析命令："python3 /path/to/send.py"
    import shlex
    cmd = shlex.split(cmd_str) + [to, subject]
    print(f"\n发送邮件到 {to}...")
    try:
        result = subprocess.run(
            cmd,
            input=html_content,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print("  ✅ 邮件发送成功")
        else:
            print(f"  ❌ 发送失败: {result.stderr}")
    except Exception as e:
        print(f"  ❌ 发送异常: {e}")

# === 主函数 ===
def main():
    if len(sys.argv) < 2:
        print("用法: python3 collector.py [daily|weekly]")
        sys.exit(1)
    
    mode = sys.argv[1]
    if mode not in ("daily", "weekly"):
        print("错误: 模式必须是 daily 或 weekly")
        sys.exit(1)
    
    cfg = load_config()
    sort_mode = "-last_updated" if mode == "daily" else "-rating"
    
    print(f"{'='*50}")
    print(f"  Mod Collector - {'每日更新' if mode == 'daily' else '每周精选'}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  排序: {'最新更新' if mode == 'daily' else '评分最高'}")
    print(f"{'='*50}")
    
    results, added, updated = collect_all(cfg, sort_mode)
    
    mode_label = "每日最新 Mod" if mode == "daily" else "每周精选 Mod"
    subject = f"[Mod Collector] {mode_label} - {datetime.now().strftime('%Y-%m-%d')}"
    html_content = generate_html(results, mode, cfg)
    
    html_path = os.path.join(get_data_dir(cfg), f"report_{mode}_{datetime.now().strftime('%Y%m%d_%H%M')}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\n报告已保存: {html_path}")
    
    to_email = cfg["settings"].get("email_to", "")
    if to_email:
        send_email(to_email, subject, html_content, cfg)
    else:
        print("\n未配置收件邮箱，跳过邮件发送")
    
    print(f"\n{'='*50}")
    print(f"  完成！新增 {added} 个，更新 {updated} 个")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
