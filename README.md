# GitHub Contributor Analyzer

基于 GitHub REST API 的仓库贡献者分析工具，支持数据采集、本地存储、可视化看板与 CSV 导出。

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-red)](https://streamlit.io)

---

## 目录

- [功能概览](#功能概览)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [技术架构](#技术架构)
- [页面说明](#页面说明)
- [字段说明](#字段说明)
- [已知限制](#已知限制)
- [Agent Teams 开发方案](#agent-teams-开发方案)
- [Git 管理策略](#git-管理策略)

---

## 功能概览

| 功能 | 说明 |
|------|------|
| 贡献者采集 | 分页获取仓库所有贡献者，基于 git commit 历史 |
| 代码统计 | 每位贡献者的 additions / deletions / net lines |
| 用户画像 | 公司、地区、邮箱、Followers、求职状态等 25 个字段 |
| 本地存储 | SQLite 数据库，支持多仓库历史对比 |
| 可视化看板 | 公司分布、地区热图、Commits 趋势、散点图等 6 种图表 |
| 数据导出 | 完整 CSV（25 字段）和精简 CSV，UTF-8 BOM 编码 |
| 使用手册 | 内置教程页面，含 Token 配置步骤和 FAQ |

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/LiaoJunwei2000/github-contributor-analyzer.git
cd github-contributor-analyzer
```

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 配置 GitHub Token

在项目根目录创建 `.env` 文件：

```env
GITHUB_TOKEN=ghp_your_token_here
```

> Token 需要 `public_repo` 权限。[点击创建 →](https://github.com/settings/tokens/new)
>
> 未配置 Token 时 API 限制为 60 次/小时，分析大型仓库请务必配置。

### 4. 启动应用

```bash
streamlit run app.py
```

浏览器访问 `http://localhost:8501`

---

## 项目结构

```
github-contributor-analyzer/
├── app.py                      # Streamlit 入口，注册页面导航
├── main.py                     # 核心爬取逻辑（API 请求、数据处理）
├── db.py                       # SQLite 数据库模块
├── requirements.txt            # Python 依赖
├── .env.example                # Token 配置示例
├── .gitignore
├── README.md
└── pages/
    ├── scraper.py              # 数据采集页面
    ├── 1_📂_历史数据.py         # 历史数据看板页面
    └── manual.py               # 使用手册页面
```

**数据文件（本地，不入 Git）：**
- `contributors.db` — SQLite 数据库，存储所有爬取结果
- `.env` — GitHub Token 配置

---

## 技术架构

### 技术栈

| 类别 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | |
| HTTP 客户端 | `requests` | 同步请求，含重试与限流处理 |
| 并发 | `ThreadPoolExecutor` | 并发获取用户 Profile，默认 8 线程 |
| GUI 框架 | `Streamlit` | 本地 Web 应用，多页面导航 |
| 数据库 | `SQLite` (内置) | 本地持久化，无需额外安装 |
| 数据处理 | `pandas` | DataFrame 操作与 CSV 导出 |
| 可视化 | `plotly` | 交互式图表 |
| 配置管理 | `python-dotenv` | `.env` 文件自动加载 |
| 进度条 | `tqdm` | 爬取进度显示 |

### 系统架构

```
Streamlit Web UI (localhost:8501)
        │
        ├── pages/scraper.py          # 数据采集入口
        │       │
        │       ▼
        │   main.py (核心模块)
        │       ├── fetch_repo_details()           → GET /repos/{owner}/{repo}
        │       ├── fetch_all_contributors()        → GET /repos/{owner}/{repo}/contributors（分页）
        │       ├── poll_contributor_stats()        → GET /repos/{owner}/{repo}/stats/contributors（轮询）
        │       ├── merge_contrib_and_stats()       → 本地合并计算
        │       └── enrich_with_user_details()      → GET /users/{login}（并发 8 线程）
        │               │
        │               ▼
        │           db.py (SQLite)
        │               ├── save_repo()
        │               └── save_contributors()
        │
        └── pages/1_📂_历史数据.py    # 看板与分析
                │
                ▼
            db.py (SQLite)
                ├── list_repos()
                ├── get_contributors()
                └── delete_repo()
```

### Rate Limit 处理

- 响应头 `X-RateLimit-Reset` 监控重置时间
- 遇到 403 rate limit 时自动等待至重置时间
- 遇到其他网络错误时指数退避重试（最多 3 次）
- 404 直接返回 `None`（bot 账号正常情况）

### 数据库 Schema

```sql
CREATE TABLE repos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name   TEXT UNIQUE NOT NULL,
    description TEXT,
    stars       INTEGER,
    forks       INTEGER,
    watchers    INTEGER,
    language    TEXT,
    scraped_at  TEXT
);

CREATE TABLE contributors (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_full_name                  TEXT NOT NULL,
    rank                            INTEGER,
    login                           TEXT NOT NULL,
    -- 25 个贡献与用户字段...
    scraped_at                      TEXT,
    UNIQUE(repo_full_name, login)
);
```

---

## 页面说明

### 🔍 数据采集

1. 侧边栏填入 GitHub Token
2. 输入仓库地址（格式：`owner/repo`，例如 `facebook/react`）
3. 点击「🚀 开始分析」
4. 等待 5 个步骤依次完成：获取仓库信息 → 抓取贡献者列表 → 获取增删统计 → 合并数据 → 抓取用户 Profile → 保存数据库
5. 预览结果表格，下载完整 CSV

> 大型仓库（500+ 贡献者）完整抓取约需 3-10 分钟。

### 📂 历史数据

| 标签页 | 功能 |
|--------|------|
| 👤 开发者画像 | 点击贡献者查看完整个人信息、头像、与 Top 10 均值对比图 |
| 🌐 多维分析 | 公司分布、Commits 直方图、地区分布、Followers vs Commits 散点图、求职状态饼图、代码增删对比 |
| 📋 数据表格 | 搜索、排序、Top N 滑块，含所有字段 |
| ⬇️ 导出 | 完整 CSV（25 字段）和精简 CSV（14 字段） |

### 📖 使用手册

内置教程，包含：
- Token 获取步骤说明
- 操作流程图解
- 抓取步骤耗时参考
- 字段说明表格
- 6 条 FAQ（贡献者数量差异、增删为 0、Rate Limit、数据备份等）

---

## 字段说明

### 贡献数据字段

| 字段 | 说明 |
|------|------|
| `rank` | 贡献排名（按总变更行数降序） |
| `login` | GitHub 用户名 |
| `total_commits` | 历史总 commit 数（来自 `/stats/contributors`） |
| `total_additions` | 累计新增代码行数 |
| `total_deletions` | 累计删除代码行数 |
| `net_lines` | 净增行数（新增 − 删除） |
| `total_changes` | 总变更行数（新增 + 删除） |
| `avg_changes_per_commit` | 每次 commit 平均变更行数 |
| `addition_deletion_ratio` | 新增行 / 删除行比值 |
| `contributions_on_default_branch` | 默认分支 commit 贡献数（来自 `/contributors` 端点） |

### 用户主页字段

| 字段 | 说明 |
|------|------|
| `name` | 真实姓名 |
| `company` | 所在公司/机构 |
| `location` | 所在地区 |
| `email` | 公开邮箱 |
| `blog` | 个人主页/网站 |
| `twitter_username` | Twitter 用户名 |
| `hireable` | 是否开放求职 |
| `bio` | 个人简介 |
| `followers` / `following` | Followers / Following 数量 |
| `public_repos` / `public_gists` | 公开仓库 / Gist 数量 |
| `account_created` | 账号注册时间 |
| `last_updated` | 账号最后更新时间 |
| `avatar_url` | 头像图片 URL |
| `profile_url` | GitHub 主页 URL |

---

## 已知限制

### API 贡献者数量 < GitHub 网页显示数量

GitHub 网页统计的是**所有提交过 PR 且被 merge 的用户**，而 API 只统计 git commit 历史中有直接记录的用户。

大量项目（如 vllm、PyTorch、Transformers）使用 **Squash Merge**，合并后 commit 作者变为维护者，原 PR 作者不被 API 计入。这是 GitHub API 的已知限制。

### 超大型仓库 additions/deletions 为 0

GitHub 的 `/stats/contributors` 端点对 commit 数量极多的仓库可能返回全零，属于官方文档注明的已知限制。此时 `total_commits`（来自 `/contributors` 端点）仍然准确。

---

## Agent Teams 开发方案

本项目使用 Claude Agent Teams 进行协作开发，以下是完整方案。

### Agent 角色定义

| Agent | 职责 | 负责文件 |
|-------|------|---------|
| **team-lead** | 任务分配、进度跟踪、最终集成 | — |
| **tech-architect** | 架构设计、数据模型、接口规范 | `main.py`、`db.py`、`README.md` |
| **app-ui-agent** | Streamlit 采集页面 UI | `app.py`、`pages/scraper.py` |
| **history-ui-agent** | 历史数据看板 UI | `pages/1_📂_历史数据.py` |
| **code-review-agent** | 代码审查、Bug 修复 | 只读审查 |

### 开发阶段流程

```
Phase 1：规划（串行）
  [tech-architect] 架构设计 + 数据模型 + 接口规范 → README.md

Phase 2：并行开发
  ┌──────────────────┬───────────────────────┐
  │  app-ui-agent    │  history-ui-agent     │
  │  scraper.py      │  1_📂_历史数据.py     │
  └────────┬─────────┴──────────┬────────────┘
           └──────────┬─────────┘

Phase 3：审查与修复（并行）
  [code-review-agent] Bug 修复 + UI 优化

Phase 4：文档
  [tech-architect] 完善 README.md
```

### Agent 协作规范

**文件所有权（避免冲突）：**

| Agent | 负责文件 |
|-------|---------|
| tech-architect | `main.py`、`db.py`、`README.md` |
| app-ui-agent | `app.py`、`pages/scraper.py` |
| history-ui-agent | `pages/1_📂_历史数据.py` |
| code-review-agent | 只读审查，不直接修改源码 |

**Agent 类型要求：** 使用 `general-purpose` 类型 + 显式指定 `model: "sonnet"`（`Plan` 类型缺少 `SendMessage` 工具，无法与 team-lead 通信）。

---

## Git 管理策略

### 分支策略

```
main（受保护，只接受 PR 合并）
  │
  ├── feature/add-csv-export ──────────► PR → main
  ├── fix/pagination-bug ──────────────► PR → main
  └── hotfix/auth-token-crash ─────────► PR → main → [Tag v1.x.y]
```

| 分支类型 | 命名规范 | 合并方式 |
|---------|---------|---------|
| `main` | — | — |
| `feature/*` | `feature/short-desc` | Squash Merge |
| `fix/*` | `fix/issue-desc` | Squash Merge |
| `hotfix/*` | `hotfix/critical-desc` | Squash Merge |

### 提交规范（Conventional Commits）

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `refactor` | 代码重构 |
| `chore` | 构建/依赖等杂项 |

---

## 云端部署（Streamlit Cloud + Supabase）

完全免费方案：**Streamlit Community Cloud**（应用托管）+ **Supabase**（PostgreSQL 数据库）。

### 第一步：创建 Supabase 数据库

1. 注册 [supabase.com](https://supabase.com)，创建新项目
2. 进入 **Project Settings → Database → Connection string → URI**，复制连接字符串（格式如下）：
   ```
   postgresql://postgres:[YOUR-PASSWORD]@db.[YOUR-PROJECT-REF].supabase.co:5432/postgres
   ```
3. 无需手动建表，应用启动时 `init_db()` 会自动创建

### 第二步：部署到 Streamlit Community Cloud

1. 将代码推送到 GitHub（确保 `secrets.toml` 在 `.gitignore` 中）
2. 登录 [share.streamlit.io](https://share.streamlit.io)，点击 **New app**
3. 选择你的 GitHub 仓库，入口文件填 `app.py`
4. 点击 **Advanced settings → Secrets**，填入以下内容：
   ```toml
   DATABASE_URL = "postgresql://postgres:密码@db.你的项目ID.supabase.co:5432/postgres"
   GITHUB_TOKEN = "ghp_你的token"
   ```
5. 点击 **Deploy** 即可

### 本地开发配置

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# 编辑 .streamlit/secrets.toml，填入 Supabase 连接字符串和 GitHub Token
streamlit run app.py
```

> `.streamlit/secrets.toml` 已在 `.gitignore` 中，不会上传到 GitHub。

---

## 反馈与贡献

如有问题或建议，欢迎在 [GitHub Issues](https://github.com/LiaoJunwei2000/github-contributor-analyzer/issues) 中反馈。

---

*本项目由 Claude Code 协助规划与开发。*
