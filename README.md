# Mod Collector

自动搜集多平台游戏 Mod，生成 HTML 报告并邮件通知。

## 支持平台

- **Nexus Mods** — 通过 API v1 获取（需 API Key）
- **Thunderstore** — 通过公共 API 获取
- **GitHub** — 通过 GitHub Search API 获取
- **ModDB** — 通过 HTML 解析获取

## 支持游戏

| 游戏 | 平台 |
|------|------|
| 戴森球计划 (Dyson Sphere Program) | Thunderstore, GitHub, ModDB |
| 异星工场 (Factorio) | GitHub, ModDB |
| REPO | Thunderstore, GitHub |
| 星露谷物语 (Stardew Valley) | Nexus Mods, GitHub, ModDB |

## 功能

- **每日更新** — 按最新更新排序，发现最新发布的 Mod
- **每周精选** — 按评分/受欢迎排序，推荐高质量 Mod
- **数据持久化** — JSON 文件存储，增量更新，按名称+作者去重
- **HTML 报告** — 美观的响应式报告，按游戏分章节展示
- **邮件通知** — 自动发送报告到指定邮箱
- **免费翻译** — 集成 MyMemory 翻译 API，英文描述自动翻译

## 快速开始

### 1. 安装依赖

仅需 Python 3 标准库，无需安装额外包：

```bash
python3 --version  # 需要 Python 3.6+
```

### 2. 配置

编辑 `config.json`：

```json
{
  "settings": {
    "email_to": "your_email@example.com",
    "send_email_script": "python3 /path/to/send.py",
    "nexus_api_key": "your_nexus_api_key",
    "github_token": "your_github_token"
  }
}
```

> **密钥优先级**：环境变量 > `secrets/` 目录文件 > `config.json` > gh CLI
>
> 推荐将敏感密钥放在 `secrets/` 目录（已加入 `.gitignore`）：
> - `secrets/nexus-api-key`
> - `secrets/github-token`

### 3. 运行

```bash
# 每日更新
python3 collector.py daily

# 每周精选
python3 collector.py weekly
```

### 4. 定时任务（可选）

```bash
# 每天 19:00 运行每日更新
0 19 * * * cd /path/to/mod-collector && python3 collector.py daily >> data/cron_daily.log 2>&1

# 每周一 19:00 运行每周精选
0 19 * * 1 cd /path/to/mod-collector && python3 collector.py weekly >> data/cron_weekly.log 2>&1
```

## 项目结构

```
mod-collector/
├── collector.py          # 主程序
├── config.json           # 配置文件
├── .gitignore            # Git 忽略规则
├── secrets/              # 密钥目录（不上传）
│   ├── nexus-api-key
│   └── github-token
└── data/                 # 运行数据（不上传）
    ├── mods_db.json      # Mod 数据库
    ├── report_*.html     # HTML 报告
    └── *.log             # 运行日志
```

## 自定义

### 添加新游戏

在 `config.json` 的 `games` 数组中添加：

```json
{
  "id": "my_game",
  "name_zh": "我的游戏",
  "name_en": "My Game",
  "platforms": ["github", "moddb"]
}
```

### 修改过滤条件

在 `config.json` 的 `settings` 中调整：

- `mods_per_game` — 每款游戏展示的 Mod 数量（默认 20）
- `min_rating` — 最低评分阈值（默认 3.0）
- `min_endorsements` — 最低推荐数阈值（默认 10）

## License

MIT
