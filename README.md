# GitHub Contributor Analyzer

一个用于抓取 GitHub 仓库贡献者信息、统计代码行数与 commit 数、并展示贡献者主页信息的 CLI 工具。

---

## 目录

- [项目目标](#项目目标)
- [快速开始](#快速开始)
- [技术架构](#技术架构)
- [Agent Teams 开发方案](#agent-teams-开发方案)
- [部署方案](#部署方案)
- [Git 管理策略](#git-管理策略)

---

## 项目目标

| 功能 | 说明 |
|------|------|
| 贡献者列表 | 获取指定 repo 的所有贡献者 |
| Commit 统计 | 每位贡献者的 commit 总数 |
| 代码行数 | 每位贡献者的 additions / deletions |
| 主页信息 | 头像、bio、公司、地区、followers、公开 repo 数等 |
| 多格式输出 | JSON / CSV / Markdown / Terminal 彩色表格 |

---

## 快速开始

```bash
# 安装
pip install gh-contributor-analyzer

# 配置 Token
export GITHUB_TOKEN=your_token_here

# 分析仓库，输出 Markdown 表格（前 20 名贡献者）
gh-analyzer analyze owner/repo --format md --top 20

# 输出 JSON 并保存
gh-analyzer analyze golang/go --format json --output contributors.json

# Docker 运行
docker run --rm \
  -e GITHUB_TOKEN=$GITHUB_TOKEN \
  -v $(pwd)/output:/output \
  ghcr.io/your-org/gh-analyzer:latest \
  analyze owner/repo --output /output/report.md
```

---

## 技术架构

### 技术栈

| 类别 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.11+ | 生态成熟，异步支持完善，数据处理库丰富 |
| HTTP 客户端 | `httpx` (async) | 原生异步、支持 HTTP/2、接口与 requests 兼容 |
| CLI 框架 | `typer` + `rich` | Typer 基于类型注解自动生成帮助文档；Rich 提供彩色输出和进度条 |
| 数据验证 | `pydantic` v2 | 高性能数据校验，自动序列化为 JSON/dict，字段级错误提示 |
| 缓存 | `diskcache` | 磁盘级 KV 缓存，TTL 支持，避免重复请求 GitHub API |
| 异步并发 | `asyncio` + `asyncio.Semaphore` | 控制并发量，防止触发 Rate Limit |
| 测试 | `pytest` + `pytest-asyncio` + `respx` | respx 用于 mock httpx 请求，覆盖异步测试场景 |
| 代码质量 | `ruff` + `mypy` | ruff 替代 flake8+black，速度快；mypy 静态类型检查 |
| 打包 | `pyproject.toml` + `hatch` | 现代 Python 打包标准，支持 CLI 入口点 |

### 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLI (typer)                            │
│   gh-analyzer analyze <repo> [--format json|csv|md|table]      │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Orchestrator                              │
│   协调各模块执行顺序，汇总结果，分发给 Reporter                   │
└───┬───────────────┬───────────────────────┬────────────────────┘
    │               │                       │
    ▼               ▼                       ▼
┌────────┐   ┌────────────┐        ┌─────────────────┐
│Fetcher │   │ StatsCalc  │        │ ProfileEnricher │
│获取贡献│   │ 统计 commit│        │ 补充用户主页信息 │
│者列表  │   │ 增删行数   │        │ (GraphQL 批量)  │
└───┬────┘   └─────┬──────┘        └────────┬────────┘
    │              │                         │
    └──────────────┴────────────┬────────────┘
                                ▼
                   ┌────────────────────────┐
                   │      Cache Layer        │
                   │  (diskcache, TTL=1h)   │
                   └────────────┬───────────┘
                                │ cache miss → 发起请求
                                ▼
                   ┌────────────────────────┐
                   │  GitHub API Gateway    │
                   │  RateLimiter + httpx   │
                   └───────────┬────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
     GitHub REST API                   GitHub GraphQL API
   /contributors, /stats/contributors   批量查询用户 Profile
```

### GitHub API 策略

**REST API 用于：**
- `GET /repos/{owner}/{repo}/contributors` — 贡献者列表（含 commit 数）
- `GET /repos/{owner}/{repo}/stats/contributors` — 周维度 additions/deletions（需轮询 202 → 200）
- `GET /users/{username}` — 单用户信息（GraphQL 不可用时降级）

**GraphQL API 用于：**
- 批量获取贡献者 Profile，单次请求携带多个用户，大幅减少请求次数

```graphql
query BulkUsers {
  user0: user(login: "alice") { avatarUrl bio company location followers { totalCount } }
  user1: user(login: "bob")   { avatarUrl bio company location followers { totalCount } }
  # 每批最多 100 个用户
}
```

**Rate Limit 处理：**
- 响应头 `X-RateLimit-Remaining` 监控剩余配额
- `Remaining < 10` 时主动 sleep 至 `X-RateLimit-Reset`
- 遇到 429/403 时指数退避重试：`2^attempt * base_delay`，最多 5 次
- `asyncio.Semaphore(10)` 限制并发飞行请求数

### 核心数据模型

```python
from pydantic import BaseModel
from typing import Optional

class ContributorStats(BaseModel):
    login: str
    commits: int
    additions: int
    deletions: int
    changed_files: int = 0

class ContributorProfile(BaseModel):
    login: str
    name: Optional[str] = None
    avatar_url: str
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    followers: int = 0
    public_repos: int = 0

class Contributor(BaseModel):
    login: str
    stats: ContributorStats
    profile: Optional[ContributorProfile] = None

    @property
    def net_lines(self) -> int:
        return self.stats.additions - self.stats.deletions
```

### 模块职责

| 模块 | 职责 | 关键接口 |
|------|------|---------|
| `fetcher.py` | GitHub REST API 数据抓取 | `async fetch_contributors(owner, repo)` |
| `enricher.py` | GraphQL 批量 Profile 查询 | `async enrich_profiles(logins)` |
| `stats.py` | 数据合并与排名计算 | `merge_contributor_data(stats, profiles)` |
| `reporter.py` | 多格式输出 | `report(contributors, fmt, output_path)` |
| `cache.py` | diskcache 封装，TTL 管理 | `get(key)` / `set(key, value, ttl)` |
| `rate_limiter.py` | 并发控制 + 退避重试 | `async request(method, url, **kwargs)` |
| `orchestrator.py` | 流程编排 | `async analyze(repo_url, token, fmt)` |

### 项目目录结构

```
gh-analyzer/
├── pyproject.toml
├── README.md
├── .env.example
├── Dockerfile
├── .pre-commit-config.yaml
├── src/
│   └── gh_analyzer/
│       ├── __init__.py
│       ├── cli.py           # typer CLI 定义
│       ├── orchestrator.py  # 流程编排
│       ├── fetcher.py       # REST API 抓取
│       ├── enricher.py      # GraphQL Profile 查询
│       ├── stats.py         # 数据合并与计算
│       ├── reporter.py      # 多格式输出
│       ├── cache.py         # diskcache 封装
│       ├── rate_limiter.py  # 速率控制
│       ├── models.py        # Pydantic 数据模型
│       └── config.py        # 配置项
├── tests/
│   ├── conftest.py
│   ├── test_fetcher.py
│   ├── test_enricher.py
│   ├── test_stats.py
│   ├── test_reporter.py
│   └── test_rate_limiter.py
└── .github/
    └── workflows/
        ├── ci.yml
        ├── release.yml
        └── docker.yml
```

---

## Agent Teams 开发方案

本项目使用 Claude Agent Teams 进行协作开发，以下是完整方案。

### Agent 角色定义

| Agent | 职责 | 可用工具 | 输入 | 输出 |
|-------|------|---------|------|------|
| **team-lead** | 任务分配、进度跟踪、最终集成 | 全部 | — | 任务列表、集成结果 |
| **architect-agent** | 架构设计、数据模型定义、接口规范 | Read, Write, Glob, Grep | 需求文档 | `models.py`、接口规范文档 |
| **core-dev-agent** | 实现 fetcher/stats/enricher 核心模块 | 全部 | 接口规范、数据模型 | `fetcher.py`、`stats.py`、`enricher.py` |
| **cli-dev-agent** | 实现 CLI 入口、reporter 多格式输出 | 全部 | 核心模块接口 | `cli.py`、`reporter.py` |
| **test-agent** | 单元测试、集成测试、测试夹具 | 全部 | 所有源码模块 | `tests/` 目录、覆盖率报告 |
| **reviewer-agent** | 代码审查、静态分析、质量把关 | Read, Grep, Bash | PR 代码 | 审查意见、ruff/mypy 检查结果 |

### 开发阶段流程

```
Phase 1：架构设计（串行）
────────────────────────────────────────
  [architect-agent]
       |
       | 产出：models.py + 接口定义文档
       v（必须完成后 Phase 2 才能开始）

Phase 2：并行开发（并行）
────────────────────────────────────────
  ┌─────────────────┬──────────────────┐
  │  core-dev-agent │  cli-dev-agent   │
  │  fetcher.py     │  cli.py          │
  │  stats.py       │  reporter.py     │
  │  enricher.py    │  orchestrator.py │
  └────────┬────────┴────────┬─────────┘
           └────────┬────────┘
                    v

Phase 3：测试与审查（并行）
────────────────────────────────────────
  [test-agent] ──并行── [reviewer-agent]
       |                      |
       | 单元/集成测试          | ruff + mypy 审查
       └──────────┬───────────┘
                  v（全部通过后进入 Phase 4）

Phase 4：文档与发布（串行）
────────────────────────────────────────
  [architect-agent] 完善 README + CHANGELOG
  [team-lead] 打 tag → 触发 CI/CD 自动发布
```

### 任务分解示例

```
Phase 1
  Task #1:  [architect-agent] 定义项目目录结构与模块划分，输出 architecture.md
  Task #2:  [architect-agent] 设计 Pydantic 数据模型（models.py）
  Task #3:  [architect-agent] 定义各模块公共接口（函数签名、类型注解）

Phase 2（并行，依赖 Task #2 #3）
  Task #4:  [core-dev-agent] 实现 rate_limiter.py + cache.py（基础设施）
  Task #5:  [core-dev-agent] 实现 fetcher.py（REST API 调用 + 分页）
  Task #6:  [core-dev-agent] 实现 stats.py + enricher.py（数据处理）
  Task #7:  [cli-dev-agent]  实现 cli.py + orchestrator.py
  Task #8:  [cli-dev-agent]  实现 reporter.py（JSON/CSV/MD/Terminal）

Phase 3（并行，依赖 Phase 2）
  Task #9:  [test-agent]     编写核心模块单元测试（mock httpx）
  Task #10: [test-agent]     编写 CLI 集成测试
  Task #11: [reviewer-agent] ruff + mypy 全量检查，输出审查报告

Phase 4（依赖 Phase 3）
  Task #12: [architect-agent] 完善 README.md 使用文档与 CHANGELOG
```

### Agent 协作规范

**文件所有权（避免冲突）：**

| Agent | 负责文件 |
|-------|---------|
| architect-agent | `models.py`、`architecture.md`、`README.md` |
| core-dev-agent | `fetcher.py`、`stats.py`、`enricher.py`、`cache.py`、`rate_limiter.py` |
| cli-dev-agent | `cli.py`、`orchestrator.py`、`reporter.py` |
| test-agent | `tests/` 全部文件 |
| reviewer-agent | 只读审查，不直接修改源码 |

**质量门禁：**
- `ruff check src/` — 无 error 级别问题
- `mypy src/ --strict` — 无类型错误
- 测试覆盖率 >= 80%

**core-dev-agent Prompt 模板示例：**
```
你是 core-dev-agent，负责实现 fetcher/stats/enricher 模块。
严格遵循 architecture.md 中的接口定义和数据模型。
使用 httpx 异步调用 GitHub API，统一处理限流和错误。
完成后通过 SendMessage 将文件路径和关键接口摘要发送给 team-lead。
不得修改 models.py 或 cli.py。
```

---

## 部署方案

### A. PyPI 包发布

**pyproject.toml 关键配置：**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "gh-contributor-analyzer"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "rich>=13.0",
    "typer>=0.12",
    "pydantic>=2.0",
    "diskcache>=5.6",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=5.0", "ruff>=0.4", "mypy>=1.10", "pre-commit>=3.7"]

[project.scripts]
gh-analyzer = "gh_analyzer.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/gh_analyzer"]
```

**发布命令：**
```bash
python -m build
twine check dist/*
twine upload dist/*
```

### B. Docker 容器化

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app/dist/*.whl ./
RUN pip install --no-cache-dir *.whl && rm -f *.whl
ENTRYPOINT ["gh-analyzer"]
```

### C. GitHub Actions CI/CD

**ci.yml** — PR 时触发：
```yaml
on:
  pull_request:
    branches: [main]
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -e ".[dev]"
      - run: ruff check src tests
      - run: ruff format --check src tests
      - run: mypy src
      - run: pytest
```

**release.yml** — `v*` tag 自动发布到 PyPI：
```yaml
on:
  push:
    tags: ["v*"]
jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write  # OIDC trusted publishing
    steps:
      - uses: actions/checkout@v4
      - run: pip install build && python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

**docker.yml** — 构建推送镜像到 GHCR：
```yaml
on:
  push:
    tags: ["v*"]
jobs:
  docker:
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with: { registry: ghcr.io, username: "${{ github.actor }}", password: "${{ secrets.GITHUB_TOKEN }}" }
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=semver,pattern={{version}}
            type=raw,value=latest
      - uses: docker/build-push-action@v5
        with: { push: true, tags: "${{ steps.meta.outputs.tags }}", cache-from: "type=gha", cache-to: "type=gha,mode=max" }
```

### 环境配置（`.env.example`）

```env
# GitHub Personal Access Token（需要 public_repo 权限）
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# 可选配置
GH_ANALYZER_CONCURRENCY=10        # 并发请求数，默认 10
GH_ANALYZER_CACHE_TTL=3600        # 缓存时间（秒），默认 1 小时
GH_ANALYZER_CACHE_DIR=~/.cache/gh-analyzer
```

---

## Git 管理策略

### 分支策略

```
main（受保护，只接受 PR 合并）
  │
  ├── feature/add-csv-export ──────────────► PR → main → [Tag v1.x]
  │
  ├── fix/pagination-bug ──────────────────► PR → main
  │
  └── hotfix/auth-token-crash ──────────────► PR → main → [Tag v1.x.y]
```

| 分支类型 | 命名规范 | 创建自 | 合并到 | 保护规则 |
|---------|---------|--------|--------|---------|
| `main` | — | — | — | 必须 PR + 1 review + CI 通过，禁止 force push |
| `feature/*` | `feature/short-desc` | `main` | `main` | CI 通过 + review，合并后删除 |
| `fix/*` | `fix/issue-desc` | `main` | `main` | CI 通过 + review，合并后删除 |
| `hotfix/*` | `hotfix/critical-desc` | `main` | `main` | CI 通过，review 可选，合并后立即打 tag |

所有合并使用 **Squash Merge**，保持线性历史。

### 提交规范（Conventional Commits）

| 类型 | 说明 | SemVer 影响 |
|------|------|------------|
| `feat` | 新功能 | MINOR |
| `fix` / `perf` | Bug 修复 / 性能优化 | PATCH |
| `feat!` / `BREAKING CHANGE` | 破坏性变更 | MAJOR |
| `docs` / `test` / `refactor` / `chore` / `ci` | 无功能变化 | 无 |

**示例：**
```bash
git commit -m "feat(enricher): add GraphQL batch user profile query

Use GraphQL to fetch up to 100 user profiles per request,
reducing API calls by ~95% for large repositories.

Closes #12"
```

### Pre-commit Hooks（`.pre-commit-config.yaml`）

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: detect-private-key

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.10
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic>=2.0]

  - repo: https://github.com/compilerla/conventional-pre-commit
    rev: v3.4.0
    hooks:
      - id: conventional-pre-commit
        stages: [commit-msg]
        args: [feat, fix, docs, test, refactor, chore, perf, ci]
```

### 发布清单（Release Checklist）

- [ ] 根据 Conventional Commits 历史确定新版本号，更新 `pyproject.toml`
- [ ] 更新 `CHANGELOG.md`，记录新功能、修复项和 Breaking Changes
- [ ] 本地执行 `pytest --cov` 确认覆盖率 >= 80%，CI 全绿
- [ ] `mypy src` 无错误
- [ ] `pip audit` 检查依赖无已知安全漏洞
- [ ] README 使用示例与当前代码一致
- [ ] 本地构建 Docker 镜像并验证 `docker run --rm gh-analyzer:latest --help`
- [ ] `git tag v1.2.0 && git push origin v1.2.0`，触发自动发布 workflow
- [ ] 确认 PyPI 页面已更新，包版本正确
- [ ] 在 GitHub Releases 页面编辑 release notes

---

*本文档由 Claude Code Agent Teams 协作规划生成（tech-architect + agile-coordinator + devops-planner）。*
