import os
import time
import threading
import requests as _requests
import streamlit as st
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from db import get_contributors
from main import CSV_FIELDS
from background_jobs import (
    create_job, get_job,
    get_active_job_for_repo, cleanup_job,
)
from runner import run_scrape_job, parse_repo

load_dotenv()


# ── 必须定义在模块顶层，@st.cache_data 才能跨 rerun 正确命中缓存 ──
@st.cache_data(ttl=30, show_spinner=False)
def _fetch_rate_limit(tok: str):
    try:
        r = _requests.get(
            "https://api.github.com/rate_limit",
            headers={"Authorization": f"token {tok}"},
            timeout=5,
        )
        if r.status_code == 200:
            core = r.json().get("resources", {}).get("core", {})
            return core.get("remaining", 0), core.get("limit", 5000), core.get("reset", 0)
    except Exception:
        pass
    return None, None, None


def _default_token() -> str:
    try:
        return st.secrets.get("GITHUB_TOKEN", "") or os.getenv("GITHUB_TOKEN", "")
    except Exception:
        return os.getenv("GITHUB_TOKEN", "")


# ============ Sidebar ============
with st.sidebar:
    st.title("🐙 GH Contributor Analyzer")
    st.markdown("---")

    token = st.text_input(
        "GitHub Token",
        value=_default_token(),
        type="password",
        placeholder="ghp_xxxxxxxxxxxx",
    )
    st.caption("需要 `public_repo` 权限。[创建 Token ↗](https://github.com/settings/tokens/new)")

    # ── API 余量显示 ──
    if token:
        st.markdown("---")
        remaining, limit, reset_ts = _fetch_rate_limit(token)
        if remaining is not None:
            pct = remaining / limit if limit else 0
            color = "normal" if pct > 0.2 else ("off" if pct > 0.05 else "inverse")
            st.metric("API 余量", f"{remaining:,} / {limit:,}", delta=None, delta_color=color)
            st.progress(pct)
            reset_str = datetime.fromtimestamp(reset_ts).strftime("%H:%M:%S")
            st.caption(f"每小时重置 · 本次重置时间：{reset_str}")
        else:
            st.caption("⚠️ 无法获取 API 余量（Token 可能无效）")


# ============ 进度显示 ============
_PHASE_TEXT = {
    "starting":     "⏳ 初始化...",
    "repo_info":    "📋 获取仓库基本信息...",
    "contributors": "👥 分页抓取贡献者列表...",
    "stats":        "📊 获取代码增删统计（首次可能需要等待 GitHub 计算）...",
    "merging":      "🔀 合并与整理数据...",
    "enriching":    "🔍 抓取用户 Profile...",
    "saving":       "💾 保存到本地数据库...",
}


def _show_running(job: dict):
    """展示运行中的任务进度，每 1.5 秒刷新一次。"""
    phase = job.get("phase", "starting")
    done = job.get("done", 0)
    total = job.get("total", 1)
    rl_status = job.get("rl_status", "normal")
    rl_remaining = job.get("rl_remaining", 5000)
    rl_wait_s = job.get("rl_wait_s", 0)
    contrib_count = job.get("contrib_count", 0)

    st.info(
        "**后台任务进行中** — 切换页面或关闭浏览器均不影响进度，完成后结果自动保存到数据库。",
        icon="ℹ️",
    )

    if phase == "enriching" and total > 0:
        pct = done / total
        if rl_status == "paused":
            st.progress(pct, text=f"⏸️ Rate Limit 触发，约 {rl_wait_s}s 后自动恢复 · {done}/{total}")
            st.warning(
                f"🔴 **Rate Limit 已达上限**，所有线程已暂停。"
                f"约 **{rl_wait_s} 秒**后自动恢复，无需手动操作。"
            )
        elif rl_status == "slow":
            st.progress(pct, text=f"🐌 降速模式（剩余额度 {rl_remaining}）· {done}/{total}")
            st.info(f"🟡 **接近限速**（剩余 {rl_remaining} 次），已自动放慢请求速度。")
        else:
            st.progress(pct, text=f"🔍 抓取 Profile · {done}/{total}")
    else:
        label = _PHASE_TEXT.get(phase, phase)
        st.write(label)
        if contrib_count:
            st.caption(f"共 {contrib_count} 位贡献者")
        st.progress(0.0)

    time.sleep(1.5)
    st.rerun()


# ============ 结果展示（从 DB 读取）============
def _show_results(repo: str, details: dict):
    enriched = get_contributors(repo)
    if not enriched:
        st.warning("数据库中未找到该仓库的贡献者数据。")
        return

    st.markdown("---")
    st.subheader(f"📊 {repo} 贡献者排行")

    if details:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("⭐ Stars", f"{details.get('stargazers_count', 0):,}")
        c2.metric("🍴 Forks", f"{details.get('forks_count', 0):,}")
        c3.metric("👁 Watchers", f"{details.get('subscribers_count', 0):,}")
        c4.metric("🌐 Language", details.get("language") or "N/A")

    st.info(
        f"共获取到 **{len(enriched)}** 位贡献者。"
        "表格仅展示核心字段，**email、个人主页、Twitter、bio 等完整信息请下载 CSV**。"
        "展开上方「全部可获取字段说明」查看所有字段详情。",
        icon="💡",
    )

    df = pd.DataFrame(enriched)

    display_cols = [
        "rank", "login", "name", "location", "company",
        "total_commits", "total_additions", "total_deletions", "net_lines", "total_changes",
        "avg_changes_per_commit", "followers", "public_repos",
        "contributions_on_default_branch", "account_created",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    df_display = df[display_cols].copy()

    for col in ["total_commits", "total_additions", "total_deletions", "net_lines", "total_changes", "followers", "public_repos"]:
        if col in df_display.columns:
            df_display[col] = pd.to_numeric(df_display[col], errors="coerce").fillna(0).astype(int)

    if "avg_changes_per_commit" in df_display.columns:
        df_display["avg_changes_per_commit"] = pd.to_numeric(df_display["avg_changes_per_commit"], errors="coerce").round(1)

    st.caption("点击列标题可对表格进行排序 · 部分超大型仓库的修改行数为 0，属 GitHub API 已知限制，非工具问题")
    st.dataframe(
        df_display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "rank": st.column_config.NumberColumn("排名", width="small"),
            "login": st.column_config.TextColumn("用户名"),
            "name": st.column_config.TextColumn("姓名"),
            "location": st.column_config.TextColumn("地区"),
            "company": st.column_config.TextColumn("公司"),
            "total_commits": st.column_config.NumberColumn("总 Commits", format="%d"),
            "total_additions": st.column_config.NumberColumn("新增行", format="%d"),
            "total_deletions": st.column_config.NumberColumn("删除行", format="%d"),
            "net_lines": st.column_config.NumberColumn("净增行", format="%d"),
            "total_changes": st.column_config.NumberColumn("总变更行", format="%d"),
            "avg_changes_per_commit": st.column_config.NumberColumn("均变更/commit", format="%.1f"),
            "followers": st.column_config.NumberColumn("Followers", format="%d"),
            "public_repos": st.column_config.NumberColumn("公开 Repos", format="%d"),
            "contributions_on_default_branch": st.column_config.NumberColumn("默认分支贡献"),
            "account_created": st.column_config.TextColumn("账号注册时间"),
        },
    )

    all_cols = [c for c in CSV_FIELDS if c in df.columns]
    csv_bytes = df[all_cols].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    filename = f"contributors_{repo.replace('/', '_')}.csv"

    st.download_button(
        label=f"⬇️ 下载完整 CSV（{len(CSV_FIELDS)} 个字段，含 email / 主页 / bio 等）",
        data=csv_bytes,
        file_name=filename,
        mime="text/csv",
        type="primary",
    )
    st.success("✅ 数据已保存，前往「📂 历史数据」页面查看分析图表。")


# ============ Main ============
st.title("🐙 GitHub 仓库贡献者分析")
st.caption("基于 GitHub REST API，统计仓库贡献者的 commit 数、代码行数变更及个人主页信息。支持导出完整 CSV。")

st.markdown("---")

with st.expander("⚠️ 为什么人数比 GitHub 主页少？"):
    st.markdown("""
GitHub 网页上显示的贡献者数量 **≠ API 返回数量**，原因如下：

**GitHub 网页统计的是：**
- 所有提交过 PR 且被 merge 的用户

**本工具 API 统计的是：**
- 在 git commit 历史中有直接记录的用户

**差异根源：Squash Merge**

大量项目（如 vllm、PyTorch）使用 Squash Merge，
合并后的 commit 作者变为维护者，原 PR 作者的提交记录消失，
因此 API 返回人数会**显著少于**网页显示人数。

这是 GitHub API 的已知限制，并非工具问题。
    """)

with st.expander("📊 为什么部分仓库的修改行数为 0？"):
    st.markdown("""
GitHub 的 `/stats/contributors` 端点**不保证对所有仓库可用**：

- **超大型仓库**（commit 数量极多，如 vllm、PyTorch、Linux）：GitHub 后台统计耗时过长，该端点会返回空数据，导致 `total_additions` / `total_deletions` / `total_changes` 全部显示为 0
- **这是 GitHub API 的官方已知限制**，并非工具 bug，也无法绕过

此情况下 `total_commits`（来自 `/contributors` 端点）仍然准确，可作为贡献量的主要参考指标。
    """)

with st.expander("📋 全部可获取字段说明"):
    st.markdown("""
**表格中展示（核心贡献数据）**

| 字段 | 说明 |
|------|------|
| `rank` | 贡献排名（按总变更行数） |
| `login` | GitHub 用户名 |
| `name` | 真实姓名 |
| `location` | 所在地区 |
| `company` | 所在公司 |
| `total_commits` | 历史总 commit 数 |
| `total_additions` | 累计新增代码行数 |
| `total_deletions` | 累计删除代码行数 |
| `net_lines` | 净增行数（新增 − 删除） |
| `total_changes` | 总变更行数（新增 + 删除） |
| `avg_changes_per_commit` | 每次 commit 平均变更行数 |
| `followers` | GitHub Followers 数 |
| `public_repos` | 公开仓库数量 |
| `contributions_on_default_branch` | 默认分支 commit 贡献数 |

**仅在下载 CSV 中包含（隐私/补充信息）**

| 字段 | 说明 |
|------|------|
| `email` | 公开邮箱地址 |
| `blog` | 个人主页 / 网站 |
| `twitter_username` | Twitter 用户名 |
| `hireable` | 是否开放求职 |
| `bio` | 个人简介 |
| `public_gists` | 公开 Gist 数量 |
| `following` | 关注人数 |
| `avatar_url` | 头像图片链接 |
| `profile_url` | GitHub 主页链接 |
| `user_id` | GitHub 用户 ID |
| `account_created` | 账号注册时间 |
| `last_updated` | 账号最后更新时间 |
| `addition_deletion_ratio` | 增删比（新增 / 删除） |

> 💡 点击下方「下载完整 CSV」可获取所有 {total} 个字段的完整数据。
    """.format(total=len(CSV_FIELDS)))

# ============ 任务状态路由 ============
active_job_id = st.session_state.get("job_id")
active_job = get_job(active_job_id) if active_job_id else None

if active_job and active_job["status"] == "running":
    # —— 进行中：轮询进度 ——
    st.subheader(f"🔄 正在分析：{active_job['repo']}")
    _show_running(active_job)
    # _show_running 末尾调用 st.rerun()，不会继续执行

elif active_job and active_job["status"] == "complete":
    # —— 完成：展示结果 ——
    repo = active_job["repo"]
    details = active_job.get("details")
    st.success(f"✅ **{repo}** 分析完成！")
    _show_results(repo, details)
    st.divider()
    if st.button("🔄 分析另一个仓库", type="secondary"):
        cleanup_job(active_job_id)
        del st.session_state["job_id"]
        st.rerun()

elif active_job and active_job["status"] == "error":
    # —— 失败：展示错误 ——
    st.error(f"❌ 分析失败：{active_job['error']}")
    if st.button("重新开始"):
        cleanup_job(active_job_id)
        del st.session_state["job_id"]
        st.rerun()

else:
    # —— 无活跃任务：展示输入表单 ——
    if active_job_id:
        # session_state 残留（如服务器重启）
        del st.session_state["job_id"]

    col_input, col_btn, col_check, col_resume = st.columns([5, 2, 2, 2])
    with col_input:
        repo_input = st.text_input(
            "仓库地址",
            placeholder="owner/repo 或 https://github.com/owner/repo",
        )
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        run_btn = st.button("🚀 开始分析", type="primary", use_container_width=True)
    with col_check:
        st.markdown("<br>", unsafe_allow_html=True)
        include_anon = st.checkbox("包含匿名贡献者", value=False)
    with col_resume:
        st.markdown("<br>", unsafe_allow_html=True)
        resume_mode = st.checkbox("⚡ 续传", value=False, help="跳过已成功获取 Profile 的用户，仅补抓缺失数据")

    if run_btn:
        if not token:
            st.error("请在左侧填写 GitHub Token")
            st.stop()
        repo = parse_repo(repo_input or "")
        if not repo:
            st.error("格式不正确，支持：`owner/repo` 或 `https://github.com/owner/repo`")
            st.stop()

        # 如果同一仓库已有正在运行的后台任务，直接重连
        existing_jid = get_active_job_for_repo(repo)
        if existing_jid:
            st.session_state["job_id"] = existing_jid
            st.rerun()

        # 创建新任务并启动后台线程
        job_id = create_job(repo)
        st.session_state["job_id"] = job_id

        thread = threading.Thread(
            target=run_scrape_job,
            args=(job_id, repo, token, include_anon, resume_mode),
            daemon=False,  # 非守护线程：浏览器关闭后继续运行直到完成
        )
        thread.start()

        st.rerun()
