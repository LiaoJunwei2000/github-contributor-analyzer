import os
import time
import threading

import requests as _requests
import streamlit as st
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

from background_jobs import create_job, get_job, get_active_job_for_repo
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


# ============ Main ============
st.title("📥 批量数据采集")
st.caption("一次输入多个仓库，各自独立后台任务并行运行。切换页面或关闭浏览器均不影响进度。")

st.markdown("---")

# ── 初始化 session state ──
if "batch_jobs" not in st.session_state:
    st.session_state["batch_jobs"] = []

# ── 输入区 ──
repos_input = st.text_area(
    "输入多个仓库（每行一个，支持 `owner/repo` 或 GitHub URL 两种格式）",
    height=150,
    placeholder="facebook/react\nvuejs/vue\nhttps://github.com/microsoft/vscode",
)

# 解析有效仓库
valid_repos = []
if repos_input:
    for line in repos_input.strip().splitlines():
        r = parse_repo(line.strip())
        if r and r not in valid_repos:
            valid_repos.append(r)

# 展示解析结果
if repos_input and not valid_repos:
    st.warning("未识别到有效仓库，请检查格式（`owner/repo` 或 `https://github.com/owner/repo`）")
elif valid_repos:
    st.caption(f"识别到 {len(valid_repos)} 个有效仓库：{', '.join(valid_repos)}")

col_opt, col_btn = st.columns([3, 1])
with col_opt:
    include_anon = st.checkbox("包含匿名贡献者", value=False)
with col_btn:
    st.markdown("<br>", unsafe_allow_html=True)
    start_btn = st.button(
        "🚀 开始批量采集",
        type="primary",
        disabled=(len(valid_repos) == 0 or not token),
        use_container_width=True,
    )

if start_btn:
    if not token:
        st.error("请在左侧填写 GitHub Token")
        st.stop()

    new_started = 0
    for repo in valid_repos:
        # 若该仓库已有正在运行的任务，直接接管
        existing_jid = get_active_job_for_repo(repo)
        if existing_jid:
            tracked_ids = [j["job_id"] for j in st.session_state["batch_jobs"]]
            if existing_jid not in tracked_ids:
                st.session_state["batch_jobs"].append({"repo": repo, "job_id": existing_jid})
            continue

        # 跳过已在批次队列中的仓库
        if any(j["repo"] == repo for j in st.session_state["batch_jobs"]):
            continue

        job_id = create_job(repo)
        st.session_state["batch_jobs"].append({"repo": repo, "job_id": job_id})

        thread = threading.Thread(
            target=run_scrape_job,
            args=(job_id, repo, token, include_anon, False),
            daemon=False,
        )
        thread.start()
        new_started += 1

    if new_started:
        st.rerun()

# ============ 采集队列 ============
batch_jobs = st.session_state.get("batch_jobs", [])

if batch_jobs:
    st.markdown("---")
    st.subheader("采集队列")

    _PHASE_TEXT = {
        "starting":     "初始化",
        "repo_info":    "获取仓库信息",
        "contributors": "抓取贡献者列表",
        "stats":        "获取代码统计",
        "merging":      "合并数据",
        "enriching":    "抓取用户 Profile",
        "saving":       "保存数据库",
        "complete":     "完成",
        "error":        "失败",
    }

    rows = []
    has_running = False
    n_done = 0
    n_failed = 0

    for item in batch_jobs:
        job = get_job(item["job_id"])
        if not job:
            n_failed += 1
            rows.append({"仓库": item["repo"], "状态": "⚠️ 任务丢失", "进度": "—"})
            continue

        status = job["status"]
        phase = job.get("phase", "starting")
        done = job.get("done", 0)
        total = job.get("total", 0)

        if status == "running":
            has_running = True
            status_icon = "🔄 运行中"
            if phase == "enriching" and total > 0:
                progress_str = f"{done}/{total} ({_PHASE_TEXT.get(phase, phase)})"
            else:
                progress_str = _PHASE_TEXT.get(phase, phase)
        elif status == "complete":
            n_done += 1
            status_icon = "✅ 完成"
            progress_str = f"{total}/{total}" if total > 0 else "完成"
        else:
            n_failed += 1
            status_icon = "❌ 失败"
            err = job.get("error", "未知错误")
            progress_str = err[:50] + ("..." if len(err) > 50 else "")

        rows.append({"仓库": item["repo"], "状态": status_icon, "进度": progress_str})

    df_queue = pd.DataFrame(rows)
    st.dataframe(df_queue, use_container_width=True, hide_index=True)

    # 整体进度条
    n_total = len(batch_jobs)
    n_finished = n_done + n_failed
    if n_total > 0:
        st.progress(n_finished / n_total,
                    text=f"已完成 {n_done} 成功 / {n_failed} 失败 / 共 {n_total} 个仓库")

    # 完成汇总 or 轮询
    if not has_running and n_finished == n_total and n_total > 0:
        if n_failed == 0:
            st.success(f"✅ 全部 {n_total} 个仓库采集完成！前往「📂 历史数据」页面查看分析结果。")
        else:
            st.warning(f"采集结束：{n_done} 个成功，{n_failed} 个失败。可查看上方表格中的错误详情。")
        if st.button("🔄 清空队列，重新开始"):
            st.session_state["batch_jobs"] = []
            st.rerun()
    elif has_running:
        time.sleep(2)
        st.rerun()
