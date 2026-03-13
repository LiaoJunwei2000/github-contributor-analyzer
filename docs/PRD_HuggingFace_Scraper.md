# PRD：Hugging Face 贡献者爬取系统

**版本**：v1.0
**日期**：2026-03-11
**状态**：待评审

---

## 1. 背景与目标

### 1.1 背景

现有系统已实现对 GitHub 仓库贡献者的完整采集与分析能力。随着 AI 开源社区的快速发展，Hugging Face（以下简称 HF）上聚集了大量高质量的模型、数据集和 Space 项目，其背后的贡献者群体同样具有重要的研究与商业价值。

### 1.2 目标

在现有 GitHub 爬取系统的架构基础上，新增一套 Hugging Face 贡献者采集模块，实现对 HF 上 Model / Dataset / Space 三类项目贡献者信息的系统性采集、存储与分析。

### 1.3 成功指标

- 可采集任意公开 HF 项目的全量 commit 贡献者
- 贡献者 Profile 完整度 ≥ 80%（字段有值占比）
- 单项目（≤ 500 commits）采集时间 ≤ 10 分钟
- 与现有 GitHub 数据互不干扰，可分别导出

---

## 2. HF Hub API 真实能力与限制

> 以下信息均来源于 HF 官方文档（截至 2025 年 9 月）

### 2.1 速率限制（Rate Limits）

所有限制基于 **5 分钟固定窗口**，按账号/IP 独立计算（组织账号每成员独立计算）。

| 账号类型 | API 请求上限（每 5 分钟） | 文件下载（Resolver）上限 |
|---------|----------------------|----------------------|
| 匿名用户（按 IP） | **500** | 3,000 |
| 免费账号（有 Token） | **1,000** | 5,000 |
| PRO 账号 | **2,500** | 12,000 |
| Team 组织 | **3,000** | 20,000 |
| Enterprise 组织 | **6,000** | 50,000 |

**关键说明：**
- 超出限制时返回 HTTP **429 Too Many Requests**
- 响应头包含剩余量和重置时间：`RateLimit: "api";r={remaining};t={seconds_until_reset}`
- 策略头：`RateLimit-Policy: "fixed window";"api";q={total};w=300`（w=300 即 5 分钟）
- **强烈建议始终传入 HF Token**（免费账号 Token 即可将配额从 500 翻倍至 1,000）

**换算为实际吞吐量（免费 Token）：**

| 操作 | 每次消耗 API 次数 | 5 分钟可处理量 |
|-----|----------------|-------------|
| 获取项目元数据 | 1 次 | - |
| 获取 Commits（每页 100 条） | 1 次/页 | 约 100,000 commits（1,000 次×100条） |
| 获取用户 Profile | 1 次/人 | 最多 999 人 |

**实际制约**：用户 Profile 获取（1 次/人）是最大瓶颈。一个 500 人贡献的项目，需要约 500 次 API，占 5 分钟配额的 50%。

### 2.2 可用 API 端点

#### 2.2.1 项目元数据

```
GET https://huggingface.co/api/models/{namespace}/{repo_name}
GET https://huggingface.co/api/datasets/{namespace}/{repo_name}
GET https://huggingface.co/api/spaces/{namespace}/{repo_name}
```

**返回字段（已验证可用）：**

| 字段 | 类型 | 说明 |
|-----|-----|-----|
| `id` | string | 项目完整 ID，如 `"meta-llama/Llama-3.1-8B"` |
| `author` | string | 项目所有者 username |
| `private` | bool | 是否私有 |
| `gated` | bool/string | 是否需要申请访问 |
| `disabled` | bool | 是否被禁用 |
| `likes` | int | 点赞数 |
| `downloads` | int | 下载次数（仅 Model/Dataset 有） |
| `tags` | string[] | 标签列表，含 task、framework、language 等 |
| `pipeline_tag` | string | 主任务类型，如 `"text-generation"` |
| `library_name` | string | 主框架，如 `"transformers"`, `"diffusers"` |
| `created_at` | ISO datetime | 创建时间 |
| `last_modified` | ISO datetime | 最后修改时间 |
| `sha` | string | 当前最新 commit SHA |
| `siblings` | object[] | 文件列表（含文件名、大小） |
| `cardData` | object | model card 元数据（license、datasets、metrics 等） |
| `safetensors` | object | 模型参数量信息（如有） |

**注意**：`downloads` 字段在某些端点需要加参数 `?full=true` 才返回。

#### 2.2.2 Commits 历史（贡献者来源）

```
GET https://huggingface.co/api/models/{id}/commits/{branch}
GET https://huggingface.co/api/datasets/{id}/commits/{branch}
GET https://huggingface.co/api/spaces/{id}/commits/{branch}
```

**分页参数：**
- `p={page_number}`（从 0 开始）
- 每页固定返回 **100 条**
- 无 Link header 分页，需手动递增 page 直到返回空数组

**每条 Commit 返回字段（已验证）：**

| 字段 | 类型 | 说明 |
|-----|-----|-----|
| `oid` | string | Commit SHA（40位） |
| `title` | string | commit 标题（第一行） |
| `message` | string | 完整 commit message |
| `date` | ISO datetime | commit 时间 |
| `authors` | object[] | 作者列表 |
| `authors[].username` | string | HF 用户名（**已绑定到 HF 账号**） |
| `authors[].avatarUrl` | string | 头像 URL |

**重要限制：**
- `authors` 中的 `username` 是 HF 账号用户名，但**未绑定 HF 账号的 git commit 不会出现在此列表**（即纯 git 提交者可能被过滤掉）
- 不提供 `additions` / `deletions` 行数统计（HF API 无此能力）
- 分支名通常为 `main`，部分项目用 `master`

#### 2.2.3 用户 Profile

```
GET https://huggingface.co/api/users/{username}/overview
```

**返回字段（已验证可用）：**

| 字段 | 类型 | 说明 |
|-----|-----|-----|
| `id` | string | 用户唯一 ID |
| `name` | string | 用户名（= username） |
| `fullname` | string | 显示名/全名 |
| `avatarUrl` | string | 头像 URL |
| `isPro` | bool | 是否 PRO 用户 |
| `isEnterprise` | bool | 是否企业用户 |
| `isMod` | bool | 是否版主 |
| `bio` | string | 个人简介（可能为 null） |
| `website` | string | 个人网站（可能为 null） |
| `location` | string | 地理位置（可能为 null） |
| `numFollowers` | int | 粉丝数 |
| `numFollowing` | int | 关注数 |
| `numModels` | int | 名下 Model 数量 |
| `numDatasets` | int | 名下 Dataset 数量 |
| `numSpaces` | int | 名下 Space 数量 |
| `numLikes` | int | 点赞过的项目数 |
| `createdAt` | ISO datetime | 账号创建时间 |
| `orgsNames` | string[] | 所属组织列表 |

**补充端点：**
```
GET https://huggingface.co/api/users/{username}/followers   # 粉丝列表
GET https://huggingface.co/api/users/{username}/following   # 关注列表
```

#### 2.2.4 认证方式

所有端点均支持以下方式传 Token：
```http
Authorization: Bearer {HF_TOKEN}
```

无 Token 时使用匿名限额（500次/5分钟），有 Token 时使用账号限额。

---

## 3. 功能需求

### 3.1 核心功能

#### F1：单项目采集（MVP）

用户输入 HF 项目 URL 或 `namespace/repo-name`，系统自动：
1. 识别项目类型（Model / Dataset / Space）
2. 获取项目元数据
3. 分页抓取全量 commits，聚合出贡献者列表及各自 commit 数量
4. 并发获取每位贡献者的 HF Profile
5. 合并写入数据库

**支持的输入格式：**
- `meta-llama/Llama-3.1-8B`
- `https://huggingface.co/meta-llama/Llama-3.1-8B`
- `https://huggingface.co/datasets/openai/gsm8k`
- `https://huggingface.co/spaces/stabilityai/stable-diffusion`

#### F2：历史数据查看与导出

- 列出所有已采集的 HF 项目
- 查看单项目贡献者列表，支持按 commit 数排序
- 导出 CSV（全量字段）
- 删除项目数据

#### F3：HF Token 管理

- 侧边栏输入 HF Token（可选）
- 实时显示当前速率限制使用情况（解析 `RateLimit` 响应头）
- Token 验证（调用 `whoami` 端点）

### 3.2 后续迭代功能（非 MVP）

#### F4：批量采集

与现有 GitHub 批量采集对称，支持多项目并行采集。

#### F5：PPT 集成

将 HF 贡献者数据接入现有 PPT 生成器，支持混合 GitHub + HF 数据源。

#### F6：跨平台搜索

在现有跨仓库搜索页新增 HF 数据，支持 GitHub + HF 贡献者联合检索。

---

## 4. 数据模型

### 4.1 新增数据库表

#### `hf_repos` 表

```sql
CREATE TABLE hf_repos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT UNIQUE NOT NULL,   -- "namespace/repo-name"
    hf_type       TEXT NOT NULL,          -- 'model' | 'dataset' | 'space'
    description   TEXT,
    author        TEXT,                   -- 项目所有者 username
    likes         INTEGER DEFAULT 0,
    downloads     INTEGER DEFAULT 0,      -- space 无此字段，默认 0
    pipeline_tag  TEXT,                   -- 主任务类型
    library_name  TEXT,                   -- 主框架
    tags          TEXT,                   -- JSON 数组字符串
    license       TEXT,                   -- 从 cardData 提取
    gated         TEXT,                   -- false | 'auto' | 'manual'
    created_at    TEXT,
    last_modified TEXT,
    sha           TEXT,                   -- 最新 commit SHA
    scraped_at    TEXT NOT NULL
);
```

#### `hf_contributors` 表

```sql
CREATE TABLE hf_contributors (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_full_name   TEXT NOT NULL,
    hf_type          TEXT NOT NULL,
    rank             INTEGER,
    username         TEXT NOT NULL,        -- HF 用户名
    fullname         TEXT,                 -- 显示名
    bio              TEXT,
    location         TEXT,
    website          TEXT,
    avatar_url       TEXT,
    is_pro           INTEGER DEFAULT 0,    -- bool
    num_followers    INTEGER DEFAULT 0,
    num_following    INTEGER DEFAULT 0,
    num_models       INTEGER DEFAULT 0,
    num_datasets     INTEGER DEFAULT 0,
    num_spaces       INTEGER DEFAULT 0,
    orgs             TEXT,                 -- JSON 数组字符串
    total_commits    INTEGER DEFAULT 0,    -- 在该项目的 commit 数
    first_commit_at  TEXT,                 -- 最早一次 commit 时间
    last_commit_at   TEXT,                 -- 最近一次 commit 时间
    profile_url      TEXT,                 -- https://huggingface.co/{username}
    account_created  TEXT,
    scraped_at       TEXT NOT NULL,
    UNIQUE(repo_full_name, username)
);
```

### 4.2 字段对比：GitHub vs HF

| 维度 | GitHub（现有） | HF（新增） |
|-----|-------------|----------|
| 项目标识 | `owner/repo` | `namespace/repo`（含 type） |
| 贡献度指标 | commits + additions + deletions | commits 数量（无行数统计） |
| 用户职业信息 | company, hireable, twitter | orgs, is_pro |
| 社交数据 | followers, following, public_repos | numFollowers, numModels, numDatasets, numSpaces |
| 技术标签 | language | pipeline_tag, library_name, tags |
| 项目热度 | stars, forks, watchers | likes, downloads |

---

## 5. 采集流程设计

### 5.1 主流程（4 步）

```
Step 1: 解析输入 & 识别类型
        ├── 支持 URL 和 namespace/repo 两种格式
        ├── 自动推断 type（model/dataset/space）
        └── GET /api/{type}/{id}  →  项目元数据

Step 2: 分页抓取 Commits
        ├── GET /api/{type}/{id}/commits/main?p=0
        ├── 每页 100 条，循环递增 p 直到空响应
        ├── 聚合：贡献者 username → commit 列表（含时间）
        └── 计算：total_commits, first_commit_at, last_commit_at

Step 3: 并发获取用户 Profile（4 线程）
        ├── GET /api/users/{username}/overview
        ├── 跳过已在 DB 中有完整 Profile 的用户（Resume 模式）
        └── 解析并存储 fullname, bio, location, 等字段

Step 4: 写入数据库
        ├── UPSERT hf_repos
        └── UPSERT hf_contributors（按 rank 排序）
```

### 5.2 速率限制处理策略

参照现有 `RateLimiter` 类，解析 HF 的响应头进行自适应限速：

```
正常状态（remaining > 200）：不额外等待
慢速状态（50 < remaining ≤ 200）：每次请求加 0.5s 延迟
暂停状态（remaining ≤ 50）：阻塞所有线程，等待 t 秒后自动恢复
```

**与 GitHub 的差异：**
- HF 窗口为 5 分钟（GitHub 为 1 小时），重置更频繁
- HF 的 `RateLimit` 头格式不同，需单独解析
- 建议将并发线程数降至 **2**（避免在短窗口内集中消耗配额）

### 5.3 异常处理

| 情况 | 处理方式 |
|-----|---------|
| HTTP 429 | 读取 `t` 值，等待对应秒数后重试 |
| HTTP 404 | 项目不存在或已删除，终止并提示用户 |
| HTTP 401 | Token 无效，提示检查 Token |
| HTTP 403 | 项目为 gated，需申请访问权限 |
| Commit 列表为空 | 可能项目刚建立，跳过 Step 2-3，仅存元数据 |
| 用户 Profile 404 | 该用户账号已注销，跳过，username 仍保留 |
| 网络超时 | 最多重试 3 次，指数退避（2s, 4s, 8s） |

---

## 6. UI 设计

### 6.1 页面结构

在 `app.py` 新增两个页面：

```
🤗 HF 采集          →  pages/hf_scraper.py    （单项目采集）
📂 HF 历史数据      →  pages/hf_history.py    （查看与导出）
```

### 6.2 HF 采集页（`hf_scraper.py`）

**侧边栏：**
- HF Token 输入框（密码类型，可选）
- Token 验证状态（已验证 / 未验证 / 无效）
- 实时速率限制仪表盘：`剩余 {remaining} / {total}，{seconds}s 后重置`

**主区域：**
```
[输入框] huggingface.co/meta-llama/Llama-3.1-8B  [采集]

类型自动识别：✓ Model

[选项]
□ Resume 模式（跳过已有完整 Profile 的用户）

[进度区域]
阶段 1/4：获取项目元数据... ✓
阶段 2/4：抓取 Commits（第 3 页 / 共约 12 页）...  ████░░ 25%
阶段 3/4：获取贡献者 Profile（47 / 120）...
阶段 4/4：写入数据库... ✓

[结果表格]  rank | username | total_commits | location | numModels | ...
[下载 CSV]
```

### 6.3 HF 历史数据页（`hf_history.py`）

**Tab 1：项目列表**
- 表格：full_name / type / likes / downloads / 贡献者数 / 采集时间
- 操作：查看详情 / 删除

**Tab 2：贡献者数据**
- 选择项目 → 显示贡献者表格
- 筛选：按 location、org 搜索

**Tab 3：导出**
- 全量 CSV / 精简 CSV（核心 10 字段）

---

## 7. 新增文件清单

| 文件 | 类型 | 说明 |
|-----|-----|-----|
| `hf_main.py` | 新建 | HF API 核心爬取逻辑，包含 HfRateLimiter 类 |
| `hf_runner.py` | 新建 | 任务编排（parse_hf_repo、run_hf_scrape_job） |
| `pages/hf_scraper.py` | 新建 | HF 单项目采集 UI |
| `pages/hf_history.py` | 新建 | HF 历史数据查看与导出 UI |
| `db.py` | 修改 | 新增 hf_repos、hf_contributors 表的 CRUD |
| `app.py` | 修改 | 新增两个页面入口 |

**复用（无需修改）：**
- `background_jobs.py`：后台任务管理，直接复用
- `.streamlit/secrets.toml`：新增 `HF_TOKEN` 字段

---

## 8. 约束与风险

### 8.1 已知限制

| 限制 | 说明 |
|-----|-----|
| 无行数统计 | HF Commits API 不返回 additions/deletions，贡献度仅能用 commit 数量衡量 |
| 未绑定账号的贡献者丢失 | 仅通过 git 提交但未注册 HF 账号的贡献者不出现在 commits API 响应中 |
| 大型项目 commits 量大 | 如 transformers 库有数万 commits，需分页抓取，耗时较长 |
| gated 项目无法访问 | 需申请权限的项目（如 Llama 部分版本）无法直接获取 commits |
| Profile 字段可能为 null | bio/location/website 等字段大量用户未填写 |

### 8.2 风险

| 风险 | 概率 | 缓解措施 |
|-----|-----|---------|
| HF API 频繁变更 | 中 | 关键字段做 nullable 处理，异常捕获后优雅降级 |
| 速率限制比文档更严格 | 低-中 | 实现自适应限速，默认保守策略 |
| 大项目（>10K commits）超时 | 中 | 提供进度保存与 Resume 能力 |

---

## 9. 实现优先级

| 优先级 | 功能 | 预计工作量 |
|-------|-----|----------|
| P0（MVP） | hf_main.py 核心爬取 + db.py 扩展 | 较大 |
| P0（MVP） | hf_runner.py 任务编排 | 小 |
| P0（MVP） | hf_scraper.py 采集 UI | 中 |
| P1 | hf_history.py 历史数据 UI | 中 |
| P2 | 批量采集（复用 batch_scraper 模式） | 小 |
| P3 | PPT 集成 / 跨平台搜索 | 大 |

---

*文档结束*
