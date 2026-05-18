# Mod Collector

自动搜集多平台游戏 Mod，生成 HTML 报告并邮件通知。

## 支持平台

- **Nexus Mods** — 通过 API v1 获取（需 API Key）
- **Thunderstore** — 通过公共 API 获取
- **GitHub** — 通过 GitHub Search API 获取

## 支持游戏

| 游戏 | 平台 |
|------|------|
| 戴森球计划 (Dyson Sphere Program) | Thunderstore, GitHub |
| 异星工场 (Factorio) | GitHub |
| REPO | Thunderstore, GitHub |
| 星露谷物语 (Stardew Valley) | Nexus Mods, GitHub |

## 功能

- **每日更新** — 按最新更新排序，发现最新发布的 Mod
- **每周精选** — 按评分/受欢迎排序，推荐高质量 Mod
- **数据持久化** — JSON 文件存储，增量更新，按名称+作者去重
- **HTML 报告** — 美观的响应式报告，按游戏分章节展示，含 Mod 更新时间
- **邮件通知** — 自动发送报告到指定邮箱
- **免费翻译** — 集成 MyMemory 翻译 API，英文描述自动翻译
- **智能轮显** — 评分线性归一化 + 权重衰减机制，保证每个 Mod 都有展示机会

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
  "games": [
    {
      "id": "stardew_valley",
      "name_zh": "星露谷物语",
      "name_en": "Stardew Valley",
      "platforms": ["nexus", "github"],
      "min_rating": 5.0,
      "min_endorsements": 50,
      "mods_per_game": 30
    }
  ],
  "settings": {
    "min_rating": 3.0,
    "min_endorsements": 10,
    "mods_per_game": 20,
    "email_to": "your_email@example.com",
    "send_email_script": "python3 /path/to/send.py",
    "nexus_api_key": "",
    "github_token": ""
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

## 配置说明

### 全局配置（settings）

所有游戏共用的默认值：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `min_rating` | 3.0 | 最低评分阈值 |
| `min_endorsements` | 10 | 最低推荐数阈值 |
| `mods_per_game` | 20 | 每款游戏展示的 Mod 数量 |
| `show_weight_restore` | 0.05 | 未展示时权重恢复速度 |
| `email_to` | — | 收件邮箱 |
| `send_email_script` | — | 邮件发送脚本路径 |

### 每游戏独立配置（games 数组）

每款游戏可单独覆盖全局配置，支持字段：

| 参数 | 说明 |
|------|------|
| `id` | 游戏唯一标识 |
| `name_zh` | 中文名 |
| `name_en` | 英文名（用于平台搜索） |
| `platforms` | 数据源平台列表 |
| `min_rating` | 覆盖全局最低评分（可选） |
| `min_endorsements` | 覆盖全局最低推荐数（可选） |
| `mods_per_game` | 覆盖全局展示数量（可选） |

**配置优先级**：游戏独立配置 > 全局 settings > 内置默认值

### 添加新游戏

```json
{
  "id": "my_game",
  "name_zh": "我的游戏",
  "name_en": "My Game",
  "platforms": ["github", "thunderstore"],
  "min_rating": 4.0,
  "mods_per_game": 15
}
```

## 评分归一化与轮显机制

### 评分线性归一化

原始评分通过线性映射压缩到 **(0.5, 1.5)** 区间：

```
归一化分 = 0.5 + (评分 - 最低分) / (最高分 - 最低分) × 1.0
```

避免评分差距过大导致低分 Mod 永远无法展示。

### 权重衰减与恢复

| 情况 | 权重变化 | 公式 |
|------|----------|------|
| 被选中展示 | 衰减 | `f(x) = 0.82 × x^1.7`（最低 0.001） |
| 未被选中 | 恢复 | `x + 0.05`（最高 1.0） |
| 检测到更新 | 重置 | `1.0` |

### 排序规则

```
最终排序分 = 归一化评分 × show_weight
```

高评分 Mod 优先展示，但展示后权重快速衰减，让出位置给其他 Mod；未展示的 Mod 权重逐步恢复，保证 30 天内所有 Mod 都有展示机会。

## License

MIT
