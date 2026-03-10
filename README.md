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
- [云端部署](#云端部署streamlit-cloud--supabase)
- [Git 管理策略](#git-管理策略)
- [PPT 生成](#ppt-生成)

---

## 功能概览

| 功能 | 说明 |
|------|------|
| 贡献者采集 | 分页获取仓库所有贡献者，基于 git commit 历史 |
| 代码统计 | 每位贡献者的 additions / deletions / net lines |
| 用户画像 | 公司、地区、邮箱、Followers、求职状态等 25 个字段 |
| 后台任务 | 爬取在后台线程运行，切换页面不中断，进度实时显示 |
| 限速管理 | 自动检测 Rate Limit，降速/暂停/自动恢复，进度条三态显示 |
| API 余量 | 侧边栏实时显示剩余请求配额与重置时间 |
| 续传模式 | 跳过已抓取用户，仅补全缺失 Profile，适合中途断线重跑 |
| 数据存储 | 本地 SQLite / 云端 PostgreSQL 自动切换 |
| 可视化看板 | 公司分布、地区热图、Commits 趋势、散点图等 6 种图表 |
| 数据导出 | 完整 CSV（25 字段），UTF-8 BOM 编码 |
| **PPT 生成** | 华为浅色版风格，自选贡献者，含封面、概览、名片汇总、个人详情页 |
| **批量采集** | 一次输入多个仓库，各自独立后台线程并行运行，实时进度表 |
| **批量 PPT** | 多仓库合并为一份 PPTX，总封面 + 每仓库独立段落 |
| 使用手册 | 内置教程页面，含 Token 配置步骤和 FAQ |

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

> PPT 生成功能需要 `python-pptx` 和 `matplotlib`，已包含在 `requirements.txt` 中。

### 3. 配置 GitHub Token

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

编辑 `.streamlit/secrets.toml`，只需填入 GitHub Token（本地不需要 `DATABASE_URL`）：

```toml
GITHUB_TOKEN = "ghp_your_token_here"
```

> Token 需要 `public_repo` 权限。[点击创建 →](https://github.com/settings/tokens/new)

### 4. 启动应用

```bash
streamlit run app.py
```

浏览器访问 `http://localhost:8501`，数据自动存入本地 `contributors.db`。

---

## 项目结构

```
github-contributor-analyzer/
├── app.py                      # Streamlit 入口，注册页面导航
├── main.py                     # 核心爬取逻辑（API 请求、限速管理、数据处理）
├── runner.py                   # 共享爬取执行逻辑（无 UI 依赖，供多页面调用）
├── db.py                       # 数据库模块（SQLite / PostgreSQL 自动切换）
├── background_jobs.py          # 后台任务状态管理（进程级内存）
├── requirements.txt            # Python 依赖
├── .gitignore
├── README.md
├── .streamlit/
│   ├── secrets.toml.example    # 配置模板
│   └── secrets.toml            # 本地配置（gitignore）
└── pages/
    ├── scraper.py              # 数据采集页面（单仓库）
    ├── batch_scraper.py        # 批量采集页面（多仓库并行）
    ├── 1_📂_历史数据.py         # 历史数据看板页面
    ├── ppt_generator.py        # PPT 生成页面（单仓库 & 批量合并）
    └── manual.py               # 使用手册页面
```

**本地数据文件（不入 Git）：**
- `contributors.db` — SQLite 数据库
- `.streamlit/secrets.toml` — Token 配置

---

## 技术架构

### 技术栈

| 类别 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | |
| HTTP 客户端 | `requests` | 同步请求，含重试与限速处理 |
| 并发 | `ThreadPoolExecutor(max_workers=4)` | 并发抓取用户 Profile |
| 后台任务 | `threading.Thread(daemon=False)` | 切换页面/关闭浏览器不中断 |
| GUI 框架 | `Streamlit` | 多页面 Web 应用 |
| 数据库（本地） | `SQLite` | 无需额外安装 |
| 数据库（云端） | `PostgreSQL` via `psycopg2` | Supabase 托管，连接池管理 |
| 数据处理 | `pandas` | DataFrame 操作与 CSV 导出 |
| 可视化 | `plotly` | 交互式图表 |
| PPT 生成 | `python-pptx` + `matplotlib` | 华为浅色版风格 PPTX，兼容 Google Slides |
| 配置管理 | `python-dotenv` + `st.secrets` | 本地 `.env` / 云端 secrets |
| 进度条 | `tqdm` | CLI 进度显示 |

### 系统架构

```
Browser (Streamlit Web UI)
        │
        ├── pages/scraper.py              # 采集页面（UI 线程）
        │       │
        │       ├── [点击开始分析]
        │       │       │
        │       │       ▼
        │       │   background_jobs.py    # 任务状态字典（进程级内存）
        │       │       │
        │       │       ▼
        │       │   threading.Thread ──► _run_job()   # 独立后台线程
        │       │                               │
        │       │                               ▼
        │       │                           main.py (核心模块)
        │       │                               ├── RateLimiter        # 线程安全限速管理
        │       │                               ├── fetch_repo_details()
        │       │                               ├── fetch_all_contributors()
        │       │                               ├── poll_contributor_stats()
        │       │                               ├── merge_contrib_and_stats()
        │       │                               └── enrich_with_user_details()
        │       │                                       │
        │       │                                       ▼
        │       │                                   db.py (数据库)
        │       │                                       ├── save_repo()
        │       │                                       └── save_contributors()
        │       │
        │       └── [轮询] st.rerun() 每 1.5s 读取 background_jobs 状态更新 UI
        │
        └── pages/1_📂_历史数据.py         # 看板页面
                │
                ▼
            db.py → list_repos() / get_contributors() / delete_repo()
```

### Rate Limit 管理（RateLimiter 类）

- 每个爬取任务共享一个 `RateLimiter` 实例，跨所有并发线程
- 从响应头 `X-RateLimit-Remaining` / `X-RateLimit-Reset` 实时更新状态
- **三种状态：**
  - `normal`（余量 ≥ 200）：正常速度
  - `slow`（余量 < 200）：请求间自动加 1s 延迟
  - `paused`（余量 = 0 或收到 403）：使用 `threading.Event` 阻塞所有线程，等待重置后自动恢复
- Rate limit 等待不消耗 retry 次数（while 循环分离两类错误）

### 数据库 Schema

```sql
CREATE TABLE repos (
    id          INTEGER PRIMARY KEY,
    full_name   TEXT UNIQUE NOT NULL,
    description TEXT,
    stars       INTEGER,
    forks       INTEGER,
    watchers    INTEGER,
    language    TEXT,
    scraped_at  TEXT
);

CREATE TABLE contributors (
    id                              INTEGER PRIMARY KEY,
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

1. 侧边栏填入 GitHub Token（自动显示当前 API 余量）
2. 输入仓库地址（支持 `owner/repo` 或 `https://github.com/owner/repo` 两种格式）
3. 可选：勾选「包含匿名贡献者」或「⚡ 续传」
4. 点击「🚀 开始分析」，任务在后台线程运行，可自由切换页面
5. 返回页面后自动恢复进度显示，完成后展示结果表格并下载 CSV

> **大型仓库**（500+ 贡献者）完整抓取约需 3-10 分钟。
> **续传模式**：若中途触发 Rate Limit，勾选「⚡ 续传」重新分析，自动跳过已完成用户。

### 📂 历史数据

| 标签页 | 功能 |
|--------|------|
| 👤 开发者画像 | 点击贡献者查看完整个人信息、头像、与 Top 10 均值对比图 |
| 🌐 多维分析 | 公司分布、Commits 直方图、地区分布、Followers vs Commits 散点图、求职状态饼图、代码增删对比 |
| 📋 数据表格 | 搜索、排序、Top N 滑块，含所有字段 |
| ⬇️ 导出 | 完整 CSV（25 字段）和精简 CSV（14 字段） |

### 📥 批量采集

1. 在左侧填写 GitHub Token
2. 在文本框中每行输入一个仓库（支持 `owner/repo` 或完整 GitHub URL）
3. 点击「🚀 开始批量采集」，每个仓库独立启动后台线程并行运行
4. 页面每 2 秒自动刷新，表格实时展示各仓库状态（运行中 / 完成 / 失败）
5. 全部完成后显示汇总，前往「📂 历史数据」查看各仓库结果

> 同一仓库若已有正在运行的任务，会自动接管进度显示，不会重复启动。

### 📊 PPT 生成

**单仓库（「单仓库」标签页）：**

1. 选择已爬取的仓库
2. 可按公司或地区筛选贡献者
3. 多选需要详细展示的贡献者（不限数量）
4. 点击「🚀 生成 PPT」，自动拉取头像并生成 PPTX 文件
5. 下载后可直接用 Microsoft PowerPoint 或 Google Slides 打开

**批量合并（「批量生成」标签页）：**

1. 多选已采集的仓库
2. 用滑块设置每个仓库取前 N 名贡献者（默认 10）
3. 点击「🚀 生成合并 PPT」，所有仓库合并到一份 PPTX
4. 文件结构：总封面 → 仓库A 段落 → 仓库B 段落 → …

生成内容：

| 页面 | 说明 |
|------|------|
| 封面 | 仓库名、总贡献者数、总 Commits、生成日期 |
| 概览 | 来源公司 Top12 / 来源地区 Top12 柱状图 |
| 名片汇总 | 3 列 × 2 行固定布局，含头像、排名、公司、地区、联系方式 |
| 贡献者详情 | 每人一页：头像、Bio、联系信息（超链接）、6 项指标卡、与 Top10 均值对比图 |

> 所有联系方式（GitHub 主页、邮箱、个人网站、Twitter）在 PPT 内均为可点击超链接。

### 📖 使用手册

内置教程，包含：Token 获取步骤、操作流程图解、抓取耗时参考、字段说明、FAQ。

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

GitHub 网页统计所有提交过 PR 且被 merge 的用户，而 API 只统计 git commit 历史中有直接记录的用户。大量项目（如 vllm、PyTorch）使用 **Squash Merge**，合并后 commit 作者变为维护者，原 PR 作者不被 API 计入。

### 超大型仓库 additions/deletions 为 0

GitHub 的 `/stats/contributors` 端点对 commit 数量极多的仓库可能返回全零，属于官方文档注明的已知限制。此时 `total_commits` 仍然准确。

### 后台任务在云端的限制

Streamlit Cloud 空闲超时（约 5 分钟无用户访问）会 sleep 整个应用进程，**所有后台线程都会被终止**。已完成并写入数据库的数据不受影响，但进行中的任务会中断。建议：
- 保持浏览器页面开着，或
- 对于超大型仓库分批分析 + 续传模式

---

## 云端部署（Streamlit Cloud + Supabase）

完全免费方案：**Streamlit Community Cloud**（应用托管）+ **Supabase**（PostgreSQL 数据库）。

### 第一步：创建 Supabase 数据库

1. 注册 [supabase.com](https://supabase.com)，创建新项目
2. 进入项目后点击顶部 **Connect 按钮**
3. 选择 **Session Mode Pooler**（支持 IPv4，兼容 Streamlit Cloud），复制 URI：
   ```
   postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres
   ```
   > ⚠️ **不要用 Direct Connection**（仅支持 IPv6，Streamlit Cloud 不兼容）

4. 无需手动建表，应用启动时 `init_db()` 会自动创建

### 第二步：部署到 Streamlit Community Cloud

1. 将 `main` 分支推送到 GitHub（确保 `secrets.toml` 在 `.gitignore` 中）
2. 登录 [share.streamlit.io](https://share.streamlit.io)，点击 **New app**
3. 选择仓库，Branch 选 **main**，入口文件填 `app.py`
4. 点击 **Advanced settings → Secrets**，填入：
   ```toml
   DATABASE_URL = "postgresql://postgres.你的项目ID:密码@aws-0-区域.pooler.supabase.com:5432/postgres"
   GITHUB_TOKEN = "ghp_你的token"
   ```
5. 点击 **Deploy** 即可

### 日常开发流程

```bash
# 在 dev 分支开发和测试
git checkout dev
# ... 修改代码 ...
git add . && git commit -m "feat: xxx"
git push

# 测试通过后合并到 main 触发云端更新
git checkout main
git merge dev
git push
git checkout dev
```

> `.streamlit/secrets.toml` 已在 `.gitignore` 中，不会上传到 GitHub。

---

## Git 管理策略

| 分支类型 | 命名规范 | 合并方式 |
|---------|---------|---------|
| `main` | 生产环境，受保护 | — |
| `dev` | 本地开发主分支 | Merge to main |
| `feature/*` | `feature/short-desc` | Squash Merge |
| `fix/*` | `fix/issue-desc` | Squash Merge |

### 提交规范（Conventional Commits）

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `refactor` | 代码重构 |
| `chore` | 构建/依赖等杂项 |

---

## PPT 生成

### 视觉风格

参考华为官方浅色版模板设计：

| 元素 | 规格 |
|------|------|
| 幻灯片尺寸 | 16:9（33.87 × 19.05 cm） |
| 主色 | 华为红 `#C7000B` |
| 深色对比 | 暗红 `#6B0004`（封面面板、地区图） |
| 背景 | 纯白 `#FFFFFF` |
| 字体 | Arial（兼容 Google Slides） |
| 页眉 | 白底 + 左侧红竖条 + 底部灰分割线 |
| 页脚 | 浅灰底条 + 顶部红细线 |

### 名片汇总页布局

固定 **3 列 × 2 行**，每页最多 6 人，超出自动分页。每张名片包含：

- 头像（无头像时显示首字母红色占位块）
- 排名（🥇🥈🥉 / `#N`）+ 姓名
- `@login`（红色）
- 💻 总提交数
- 🔗 GitHub 主页 / 🏢 公司 / 📍 地区 / 📧 邮箱 / 🌐 主页 / 🐦 推特（均为超链接）

### 图表中文支持

PPT 生成器在模块加载时自动搜索系统 CJK 字体并注册到 matplotlib：

- **macOS**：PingFang SC → STHeiti Light → STHeiti Medium → Songti
- **Linux**：WQY MicroHei → Noto Sans CJK
- **Windows**：微软雅黑 → 宋体
- 找不到时回退 Arial（图表标签显示英文）

---

## 反馈与贡献

如有问题或建议，欢迎在 [GitHub Issues](https://github.com/LiaoJunwei2000/github-contributor-analyzer/issues) 中反馈。

---

*本项目由 Claude Code 协助规划与开发。*
