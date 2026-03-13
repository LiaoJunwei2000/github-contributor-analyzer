# GitHub / HuggingFace 开源人才分析平台

基于 GitHub REST API 与 Hugging Face API 的多平台贡献者分析工具，支持数据采集、智能地区分类、AI 人才档案生成，以及企业汇报级洞察报告 PPT 输出。

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-red)](https://streamlit.io)

---

## 目录

- [功能概览](#功能概览)
- [快速开始](#快速开始本地开发)
- [项目结构](#项目结构)
- [页面说明](#页面说明)
- [洞察报告 PPT](#洞察报告-ppt)
- [技术架构](#技术架构)
- [数据库 Schema](#数据库-schema)
- [已知限制](#已知限制)
- [云端部署](#云端部署streamlit-cloud--supabase)
- [Git 管理策略](#git-管理策略)

---

## 功能概览

### GitHub 侧

| 功能 | 说明 |
|------|------|
| 贡献者采集 | 分页获取仓库所有贡献者，基于 git commit 历史 |
| 代码统计 | 每位贡献者的 additions / deletions / net lines |
| 用户画像 | 公司、地区、邮箱、Followers、求职状态等 25 个字段 |
| 批量采集 | 一次输入多个仓库，各自独立后台线程并行运行 |
| 续传模式 | 跳过已抓取用户，仅补全缺失 Profile，适合中途断线重跑 |
| 后台任务 | 爬取在后台线程运行，切换页面不中断，进度实时显示 |
| 限速管理 | 自动检测 Rate Limit，降速/暂停/自动恢复 |

### Hugging Face 侧

| 功能 | 说明 |
|------|------|
| 项目贡献者采集 | 抓取 HF 模型/数据集/Space 的 commit 贡献者，含社交链接 |
| 组织成员采集 | 批量抓取 HF 组织（Org）的全部成员信息 |
| 社交链接 | 自动提取 Twitter / GitHub / Bluesky / LinkedIn / Google Scholar |
| 速率感知 | 解析 `RateLimit` 响应头，自动限速，支持 HF Token 提升配额 |
| 历史查看 | 按项目/组织查看采集数据，支持 CSV 导出 |

### 数据管理

| 功能 | 说明 |
|------|------|
| 标签管理 | 给仓库贴自定义标签（颜色标注），按标签分类筛选 |
| 跨仓库搜索 | 从多个仓库中按公司或地区筛选贡献者 |
| 可视化看板 | 公司分布、地区热图、Commits 趋势、散点图等 6 种图表 |
| 数据导出 | 完整 CSV（25 字段），UTF-8 BOM 编码 |
| 双端数据库 | 本地 SQLite / 云端 PostgreSQL 自动切换 |

### 洞察报告（核心功能）

| 功能 | 说明 |
|------|------|
| 按标签/仓库筛选人才 | 支持多仓库交集，按公司/地区二次过滤 |
| 智能地区分类 | 静态关键字 + AI（OpenRouter）双层分类，结果持久化缓存 |
| 地区分组表格 | `st.data_editor` 可视化编辑，支持按地区一键全选/全不选 |
| AI 人才档案 | 通过 OpenRouter 批量生成技术方向、贡献摘要、匹配度评分（A/B/C/D） |
| 多模型支持 | 支持 Gemini / Claude / LLaMA / DeepSeek / GPT-4o 等，实时供应商状态面板 |
| 洞察报告 PPT | 企业汇报级 16:9 PPTX，5 种主题，按匹配等级排序人才 |

---

## 分支说明

| 分支 | 环境 | 数据库 |
|------|------|--------|
| `dev` | 本地开发 | SQLite（自动创建 `contributors.db`） |
| `main` | Streamlit Cloud 生产环境 | Supabase PostgreSQL |

数据库自动切换：有 `DATABASE_URL` 用 PostgreSQL，否则用 SQLite，无需手动配置。

---

## 快速开始（本地开发）

### 1. 克隆并切换到 dev 分支

```bash
git clone https://github.com/LiaoJunwei2000/github-contributor-analyzer.git
cd github-contributor-analyzer
git checkout dev
```

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 配置 Token

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

编辑 `.streamlit/secrets.toml`：

```toml
GITHUB_TOKEN = "ghp_your_token_here"   # public_repo 权限即可
HF_TOKEN     = "hf_xxxxxxxxxxxx"        # 可选，提升 HF API 配额
```

> GitHub Token：[点击创建 →](https://github.com/settings/tokens/new)
> HF Token：[点击创建 →](https://huggingface.co/settings/tokens)

OpenRouter API Key（用于洞察报告 AI 功能）在应用界面内填写，无需写入配置文件。

### 4. 启动应用

```bash
streamlit run app.py
```

浏览器访问 `http://localhost:8501`。

---

## 项目结构

```
├── app.py                    # Streamlit 入口，注册页面导航
├── main.py                   # GitHub 爬取核心（限速、API 请求、用户信息）
├── runner.py                 # GitHub 任务编排（调用 background_jobs）
├── hf_main.py                # HF 爬取核心（HfRateLimiter、commit/user 解析）
├── hf_runner.py              # HF 任务编排
├── background_jobs.py        # 后台任务状态（进程级内存字典，线程安全）
├── db.py                     # 数据库模块（SQLite / PostgreSQL 双后端）
├── insight_llm.py            # OpenRouter LLM 集成（地区分类 + 人才档案 + 总览生成）
├── insight_ppt.py            # PPT 构建（自含绘图原语，不依赖 Streamlit）
├── requirements.txt
└── pages/
    ├── scraper.py            # GitHub 单仓库采集
    ├── batch_scraper.py      # GitHub 批量采集（多仓库并行）
    ├── 1_📂_历史数据.py       # GitHub 历史数据看板
    ├── hf_scraper.py         # HF 项目/组织采集
    ├── hf_history.py         # HF 历史数据查看 + CSV 导出
    ├── tags.py               # 仓库标签管理
    ├── cross_search.py       # 跨仓库贡献者搜索
    ├── insight_report.py     # 洞察报告（7步工作流 + AI + PPT）
    ├── ppt_generator.py      # GitHub 简版 PPT 生成（单仓库/批量）
    └── manual.py             # 使用手册
```

**本地数据文件（不入 Git）：**
- `contributors.db` — SQLite 数据库
- `.streamlit/secrets.toml` — Token 配置

---

## 页面说明

### 🔍 GitHub 数据采集

1. 侧边栏填入 GitHub Token（自动显示当前 API 余量）
2. 输入仓库地址（支持 `owner/repo` 或完整 URL）
3. 可选：勾选「包含匿名贡献者」或「⚡ 续传」
4. 点击「🚀 开始分析」，任务在后台线程运行，可自由切换页面

> 大型仓库（500+ 贡献者）完整抓取约需 3–10 分钟。续传模式可从断点恢复。

### 📥 批量采集

每行输入一个仓库，点击开始后各仓库独立后台线程并行运行，表格实时显示进度。

### 📂 历史数据

| 标签页 | 功能 |
|--------|------|
| 👤 开发者画像 | 完整个人信息、头像、与 Top 10 均值对比图 |
| 🌐 多维分析 | 公司分布、Commits 直方图、地区分布、Followers 散点图等 |
| 📋 数据表格 | 搜索、排序、Top N 滑块 |
| ⬇️ 导出 | 完整 CSV（25 字段）和精简 CSV（14 字段） |

### 🤗 HF 贡献者采集

**项目采集（「单项目」标签页）：**

1. 填入 HF Token（可选，提升限速上限）
2. 输入 HF 仓库地址（如 `meta-llama/Llama-3.1-8B`，支持 model/dataset/space）
3. 点击开始，后台抓取 commit 历史并逐一丰富用户 Profile（含社交链接）

**组织采集（「组织」标签页）：**

1. 输入 HF 组织名（如 `microsoft`）
2. 抓取全部成员，包含职位类型（admin/contributor 等）

> HF commit 仅包含绑定了 HF 账号的作者；unbound git 作者会被过滤。
> 部分 gated 数据集即使公开也可能返回 401，属 HF API 已知行为。

### 🤗 HF 历史数据

按项目或组织查看已采集数据，支持完整字段 CSV 导出（含 Twitter/GitHub/Bluesky/LinkedIn 链接）。

### 🏷️ 标签管理

创建自定义标签（名称 + 颜色），给仓库贴标签。标签可在洞察报告中用于批量选取仓库。

### 🔎 跨仓库搜索

从多个仓库中按公司或地区筛选贡献者，结果表格可排序导出。

### 📊 PPT 生成（简版）

从单个 GitHub 仓库生成贡献者名片 PPT，或批量合并多仓库。适合快速汇报。

### 📈 洞察报告

企业级人才洞察 PPT 生成工作流，详见下节。

---

## 洞察报告 PPT

### 7 步工作流

| 步骤 | 操作 | 说明 |
|------|------|------|
| ① | 选择仓库 | 按标签批量选取，或逐个勾选 |
| ② | 筛选贡献者 | 按公司 / 地区二次过滤，实时预览人数 |
| ③ | 地区分组 | 静态关键字预分类 → AI 批量处理未知地点 → 可视化表格手动调整 |
| ④ | AI 分析配置 | 选择 OpenRouter 模型，查看供应商实时延迟/可用率 |
| ⑤ | 生成人才档案 | 批量调用 LLM，为每位人才生成技术方向、贡献摘要、匹配度（A/B/C/D） |
| ⑥ | 生成总览内容 | 生成整体质量评估、地区密度分析、各项目技术价值说明 |
| ⑦ | 生成 PPT | 选择主题，导出 PPTX |

### 地区分类机制

1. **静态匹配**：内置 12 个地区、200+ 城市/国家关键字，无 API 调用，毫秒级完成
2. **AI 分类**：未识别的 location 字段批量送入 OpenRouter（50 个/批），支持模糊地名和多地区归属
3. **DB 缓存**：分类结果写入 `location_cache` 表，下次直接复用，避免重复调用

地区范围：香港、新加坡、台湾、澳门、中国大陆、北美、日本、韩国、欧洲、东南亚、中东、其他

### AI 人才档案字段

| 字段 | 说明 |
|------|------|
| `tech_direction` | 技术方向（≤120 字符），如「分布式存储 / Rust 系统编程」 |
| `contribution_summary` | 贡献摘要（≤150 字符），含具体数字 |
| `key_skills` | 3 个技能标签 |
| `match_score` | 0–100 整数，技术与华为开源战略契合度 |
| `match_level` | A（≥80）/ B（60–79）/ C（40–59）/ D（<40） |
| `match_reason` | ≤120 字符，匹配理由或差距说明 |

### PPT 结构

| 页面 | 说明 |
|------|------|
| 封面 | 报告标题、生成日期 |
| 总览 · 质量与密度 | 整体评估、地区分布、匹配等级分布图 |
| 华为匹配度分析（可多页）| 所有项目技术价值卡片，2 列动态布局，每页 6 个 |
| 地区分节页（每地区一页）| 该地区人才数量大字展示 |
| 人才详情页（每人一页）| 头像、Bio、联系方式、AI 档案、匹配分数，**按 A→B→C→D 排序** |
| 项目贡献者索引（可多页）| 所有项目 × 贡献者列表，行高自适应 |

### PPT 主题

| 主题 | 主色 |
|------|------|
| 华为经典 | 华为红 `#C7000B` |
| 深海蓝 | `#1A56AB` |
| 森林绿 | `#1E7A4E` |
| 暮光紫 | `#6B3FA0` |
| 极简灰 | `#2D3A4A` |

### 支持的 OpenRouter 模型

- `google/gemini-2.0-flash-001`（默认）
- `google/gemini-flash-1.5-8b`
- `anthropic/claude-haiku-4-5`
- `meta-llama/llama-3.3-70b-instruct`
- `deepseek/deepseek-chat-v3-0324`
- `openai/gpt-4o-mini`

模型列表从 OpenRouter API 实时拉取，展示上下文长度、最大输出 Token、每百万 Token 费用及供应商可用率。

---

## 技术架构

### 技术栈

| 类别 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | |
| HTTP 客户端 | `requests` + `urllib` | 同步请求，含重试与限速处理 |
| 并发 | `ThreadPoolExecutor` + `threading.Thread` | Profile 并发抓取 + 后台任务 |
| GUI 框架 | Streamlit | 多页面 Web 应用 |
| 数据库（本地） | SQLite | 无需额外安装 |
| 数据库（云端） | PostgreSQL via `psycopg2` | Supabase 托管，连接池管理 |
| 数据处理 | `pandas` | DataFrame 操作与 CSV 导出 |
| 可视化 | `plotly` | 交互式图表 |
| PPT 生成 | `python-pptx` | 自含绘图原语，16:9，5 种主题 |
| LLM 接入 | OpenRouter API（OpenAI-compatible） | 地区分类 + 人才档案 + 总览生成 |

### Rate Limit 管理

**GitHub（`RateLimiter` 类）：**
- 从响应头 `X-RateLimit-Remaining` / `X-RateLimit-Reset` 实时更新
- 三态：`normal`（正常）→ `slow`（余量 < 200，加 1s 延迟）→ `paused`（余量耗尽，阻塞等待重置）

**HuggingFace（`HfRateLimiter` 类）：**
- 解析 `RateLimit: "api";r={remaining};t={reset_seconds}` 响应头
- HTTP 429 触发暂停；匿名 500/5min，Token 1000/5min，PRO 2500/5min

---

## 数据库 Schema

### GitHub 表

```sql
CREATE TABLE repos (
    id         INTEGER PRIMARY KEY,
    full_name  TEXT UNIQUE NOT NULL,
    description TEXT, stars INTEGER, forks INTEGER,
    language TEXT, scraped_at TEXT
);

CREATE TABLE contributors (
    id                   INTEGER PRIMARY KEY,
    repo_full_name       TEXT NOT NULL,
    rank                 INTEGER,
    login                TEXT NOT NULL,
    -- 25 个贡献与用户字段 (commits, additions, deletions,
    --   name, company, location, email, followers, bio …)
    scraped_at           TEXT,
    UNIQUE(repo_full_name, login)
);

CREATE TABLE tags (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, color TEXT
);
CREATE TABLE repo_tags (
    repo_full_name TEXT, tag_id INTEGER,
    PRIMARY KEY (repo_full_name, tag_id)
);
```

### HuggingFace 表

```sql
CREATE TABLE hf_repos (
    id INTEGER PRIMARY KEY,
    repo_id TEXT UNIQUE NOT NULL,   -- e.g. "meta-llama/Llama-3.1-8B"
    repo_type TEXT,                 -- model / dataset / space
    description TEXT, downloads INTEGER, likes INTEGER,
    scraped_at TEXT
);

CREATE TABLE hf_contributors (
    id             INTEGER PRIMARY KEY,
    hf_repo_id     TEXT NOT NULL,
    username       TEXT NOT NULL,
    -- 30 个字段: fullname, location, bio, employer,
    --   linkedin_url, twitter_url, github_url, bluesky_url …
    UNIQUE(hf_repo_id, username)
);
```

### 位置缓存表

```sql
CREATE TABLE location_cache (
    raw_location  TEXT PRIMARY KEY,
    regions       TEXT NOT NULL,    -- JSON 数组，如 ["香港","北美"]
    classified_at TEXT
);
```

---

## 已知限制

### GitHub API 贡献者数 < 网页显示数

GitHub 网页统计所有 PR 作者，API 只统计 git commit 历史中的直接作者。使用 Squash Merge 的项目（如 vllm、PyTorch）会导致原 PR 作者不被计入。

### 超大型仓库 additions/deletions 为 0

`/stats/contributors` 端点对 commit 极多的仓库可能全零，属官方已知限制。`total_commits` 仍准确。

### HF 只统计绑定了账号的作者

HF commit 历史只记录绑定了 HuggingFace 账号的 git 作者，unbound 作者不可见。

### 后台任务在云端的限制

Streamlit Cloud 空闲超时（约 5 分钟）会 sleep 整个应用进程，后台线程随之终止。已写入数据库的数据不受影响，进行中的任务会中断。建议保持页面活跃，或分批抓取 + 续传。

---

## 云端部署（Streamlit Cloud + Supabase）

完全免费：**Streamlit Community Cloud**（应用托管）+ **Supabase**（PostgreSQL）。

### 第一步：创建 Supabase 数据库

1. 注册 [supabase.com](https://supabase.com)，创建新项目
2. 进入项目 → Connect → **Session Mode Pooler**，复制 URI：
   ```
   postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres
   ```
   > ⚠️ 不要用 Direct Connection（仅支持 IPv6，Streamlit Cloud 不兼容）

3. 无需手动建表，启动时 `init_db()` 自动创建所有表

### 第二步：部署到 Streamlit Community Cloud

1. 将 `main` 分支推送到 GitHub
2. 登录 [share.streamlit.io](https://share.streamlit.io) → New app → 选仓库，入口文件 `app.py`
3. Advanced settings → Secrets：
   ```toml
   DATABASE_URL = "postgresql://..."
   GITHUB_TOKEN = "ghp_..."
   HF_TOKEN     = "hf_..."          # 可选
   ```
4. Deploy

### 日常开发流程

```bash
git checkout dev
# 修改、测试 …
git add . && git commit -m "feat: xxx"
git push

# 测试通过后合并到 main 触发云端更新
git checkout main && git merge dev && git push
git checkout dev
```

---

## Git 管理策略

| 分支 | 说明 | 合并方式 |
|------|------|---------|
| `main` | 生产环境，受保护 | — |
| `dev` | 本地开发主分支 | Merge to main |
| `feature/*` | 新功能 | Squash Merge |
| `fix/*` | Bug 修复 | Squash Merge |

**提交规范（Conventional Commits）：** `feat` / `fix` / `docs` / `refactor` / `chore`

---

*本项目由 Claude Code 协助规划与开发。*
