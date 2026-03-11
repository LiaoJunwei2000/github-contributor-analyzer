import os
import time
import threading
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from db import get_hf_contributors, get_hf_org_members
from hf_main import parse_hf_repo, parse_hf_org, fetch_hf_rate_limit_status
from hf_runner import run_hf_scrape_job, run_hf_org_scrape_job
from background_jobs import (
    create_job, get_job,
    get_active_job_for_repo, cleanup_job, list_running_jobs,
)

load_dotenv()

HF_PROJ_CSV_FIELDS = [
    "rank", "username", "fullname", "location", "website", "bio",
    "affiliation_type", "employer", "linkedin_url", "twitter_url", "github_url",
    "bluesky_url", "scholar_url",
    "is_pro", "num_followers", "num_following",
    "num_models", "num_datasets", "num_spaces",
    "num_discussions", "num_papers", "num_upvotes", "num_likes",
    "orgs", "total_commits", "first_commit_at", "last_commit_at",
    "profile_url", "avatar_url", "account_created",
]

HF_ORG_CSV_FIELDS = [
    "username", "fullname", "member_type", "location", "website", "bio",
    "affiliation_type", "employer", "linkedin_url", "twitter_url", "github_url",
    "bluesky_url", "scholar_url",
    "is_pro", "num_followers", "num_following",
    "num_models", "num_datasets", "num_spaces",
    "num_discussions", "num_papers", "num_upvotes", "num_likes",
    "orgs", "profile_url", "avatar_url", "account_created",
]


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_hf_rl(tok: str):
    return fetch_hf_rate_limit_status(tok)


def _default_token() -> str:
    try:
        return st.secrets.get("HF_TOKEN", "") or os.getenv("HF_TOKEN", "")
    except Exception:
        return os.getenv("HF_TOKEN", "")


# ============ Sidebar ============
with st.sidebar:
    st.title("🤗 HF Contributor Analyzer")
    st.markdown("---")

    token = st.text_input(
        "HF Token（可选）",
        value=_default_token(),
        type="password",
        placeholder="hf_xxxxxxxxxxxx",
    )
    st.caption(
        "无 Token 时使用匿名限额（500次/5min），有 Token 可提升至 1,000次/5min。"
        "[创建 Token ↗](https://huggingface.co/settings/tokens)"
    )

    st.markdown("---")
    rl = _fetch_hf_rl(token or "__anon__")
    remaining = rl.get("remaining")
    limit = rl.get("limit")
    reset_in = rl.get("reset_in")

    if remaining is not None and limit:
        pct = remaining / limit if limit else 0
        color = "normal" if pct > 0.3 else ("off" if pct > 0.1 else "inverse")
        st.metric("API 余量（5min窗口）", f"{remaining:,} / {limit:,}", delta_color=color)
        st.progress(min(pct, 1.0))
        if reset_in is not None:
            st.caption(f"约 {reset_in}s 后重置")
    else:
        st.caption("⚠️ 无法获取速率限制信息")


# ============ 进度显示（两种任务共用）============
_PHASE_TEXT = {
    "starting":  "⏳ 初始化...",
    "repo_info": "📋 获取项目元数据...",
    "org_info":  "📋 获取组织信息...",
    "commits":   "📜 分页抓取 Commits（聚合贡献者）...",
    "members":   "👥 获取组织成员列表（分页中）...",
    "enriching": "🔍 抓取用户 Profile...",
    "waiting":   "⏸️ API 限速等待中...",
    "saving":    "💾 保存到数据库...",
}


def _show_running(job: dict):
    phase = job.get("phase", "starting")
    done = job.get("done", 0)
    total = job.get("total", 1) or 1
    rl_status = job.get("rl_status", "normal")
    rl_remaining = job.get("rl_remaining", 1000)
    rl_wait_until = job.get("rl_wait_until", 0) or 0
    contrib_count = job.get("contrib_count", 0)

    st.info(
        "**后台任务进行中** — 切换页面或关闭浏览器均不影响进度，完成后结果自动保存到数据库。",
        icon="ℹ️",
    )

    if phase == "waiting":
        # 限速等待：倒计时进度条
        remaining_s = max(0, rl_wait_until - time.time()) if rl_wait_until > 0 else 0
        reset_total = job.get("rl_wait_s", remaining_s) or remaining_s or 300
        pct_elapsed = max(0.0, min(1.0, 1.0 - (remaining_s / reset_total))) if reset_total > 0 else 1.0
        st.warning(
            f"🔴 **API 限速已触发**，系统将在 **{remaining_s:.0f} 秒**后自动恢复抓取。\n\n"
            f"已完成：{done} / {total} 个 Profile",
        )
        st.progress(
            pct_elapsed,
            text=f"⏳ 等待限速重置... {remaining_s:.0f}s 剩余（已用 {pct_elapsed*100:.0f}%）",
        )
        sleep_s = 1.0  # 每秒刷新
    elif phase == "enriching" and total > 0:
        pct = done / total
        if rl_status == "slow":
            st.progress(pct, text=f"🐌 降速模式（API 余量 {rl_remaining}）· {done}/{total}")
            st.info(f"🟡 **接近限速**（剩余 {rl_remaining} 次），已自动放慢速度。")
        else:
            st.progress(pct, text=f"🔍 抓取 Profile · {done}/{total}")
        sleep_s = 1.5
    else:
        label = _PHASE_TEXT.get(phase, phase)
        st.write(label)
        if contrib_count:
            st.caption(f"共 {contrib_count} 位{'成员' if phase == 'members' else '贡献者'}")
        st.progress(0.0)
        sleep_s = 1.5

    time.sleep(sleep_s)
    st.rerun()


# ============ 结果展示：项目贡献者 ============
def _show_proj_results(full_name: str, hf_type: str, details: dict):
    rows = get_hf_contributors(full_name)
    if not rows:
        st.warning("数据库中未找到该项目的贡献者数据。")
        return

    st.markdown("---")
    type_icon = {"model": "🧠", "dataset": "📦", "space": "🚀"}.get(hf_type, "🤗")
    st.subheader(f"{type_icon} {full_name} 贡献者排行")

    if details:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("❤️ Likes", f"{details.get('likes', 0):,}")
        c2.metric("⬇️ Downloads", f"{details.get('downloads', 0):,}")
        c3.metric("🏷️ Task", details.get("pipeline_tag") or "N/A")
        c4.metric("📚 Library", details.get("library_name") or "N/A")

    st.info(f"共获取到 **{len(rows)}** 位贡献者。", icon="💡")

    df = pd.DataFrame(rows)
    display_cols = [
        "rank", "username", "fullname", "location",
        "affiliation_type", "employer",
        "total_commits", "first_commit_at", "last_commit_at",
        "num_followers", "num_models", "num_datasets", "num_spaces",
        "num_discussions", "num_papers", "num_upvotes", "num_likes",
        "is_pro", "linkedin_url", "twitter_url", "github_url", "bluesky_url", "scholar_url",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    df_display = df[display_cols].copy()
    for col in ["total_commits", "num_followers", "num_models", "num_datasets", "num_spaces",
                "num_discussions", "num_papers", "num_upvotes", "num_likes"]:
        if col in df_display.columns:
            df_display[col] = pd.to_numeric(df_display[col], errors="coerce").fillna(0).astype(int)
    if "is_pro" in df_display.columns:
        df_display["is_pro"] = df_display["is_pro"].apply(lambda x: "✅" if x else "")

    st.dataframe(
        df_display, use_container_width=True, hide_index=True,
        column_config={
            "rank":             st.column_config.NumberColumn("排名", width="small"),
            "username":         st.column_config.TextColumn("HF 用户名"),
            "fullname":         st.column_config.TextColumn("姓名"),
            "location":         st.column_config.TextColumn("地区"),
            "affiliation_type": st.column_config.TextColumn("身份"),
            "employer":         st.column_config.TextColumn("机构"),
            "total_commits":    st.column_config.NumberColumn("Commits", format="%d"),
            "first_commit_at":  st.column_config.TextColumn("首次提交"),
            "last_commit_at":   st.column_config.TextColumn("最近提交"),
            "num_followers":    st.column_config.NumberColumn("Followers", format="%d"),
            "num_models":       st.column_config.NumberColumn("Models", format="%d"),
            "num_datasets":     st.column_config.NumberColumn("Datasets", format="%d"),
            "num_spaces":       st.column_config.NumberColumn("Spaces", format="%d"),
            "num_discussions":  st.column_config.NumberColumn("Discussions", format="%d"),
            "num_papers":       st.column_config.NumberColumn("Papers", format="%d"),
            "num_upvotes":      st.column_config.NumberColumn("Upvotes", format="%d"),
            "num_likes":        st.column_config.NumberColumn("Likes", format="%d"),
            "is_pro":           st.column_config.TextColumn("PRO"),
            "linkedin_url":     st.column_config.LinkColumn("LinkedIn", display_text="🔗"),
            "twitter_url":      st.column_config.LinkColumn("X/Twitter", display_text="🐦"),
            "github_url":       st.column_config.LinkColumn("GitHub", display_text="💻"),
            "bluesky_url":      st.column_config.LinkColumn("Bluesky", display_text="🦋"),
            "scholar_url":      st.column_config.LinkColumn("Scholar", display_text="📚"),
        },
    )

    export_cols = [c for c in HF_PROJ_CSV_FIELDS if c in df.columns]
    csv_bytes = df[export_cols].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label=f"⬇️ 下载完整 CSV（{len(export_cols)} 个字段）",
        data=csv_bytes,
        file_name=f"hf_contributors_{full_name.replace('/', '_')}.csv",
        mime="text/csv",
        type="primary",
    )
    st.success("✅ 数据已保存，前往「🗂️ HF 历史」页面查看所有项目。")


# ============ 结果展示：组织成员 ============
def _show_org_results(org_name: str, overview: dict):
    rows = get_hf_org_members(org_name)
    if not rows:
        st.warning("数据库中未找到该组织的成员数据。")
        return

    st.markdown("---")
    st.subheader(f"🏛️ {overview.get('fullname') or org_name} 成员列表")

    if overview:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("👥 总成员", f"{overview.get('num_members', 0):,}")
        c2.metric("🧠 Models", f"{overview.get('num_models', 0):,}")
        c3.metric("📦 Datasets", f"{overview.get('num_datasets', 0):,}")
        c4.metric("🚀 Spaces", f"{overview.get('num_spaces', 0):,}")
        c5.metric("👁 Followers", f"{overview.get('num_followers', 0):,}")

    total_fetched = len(rows)
    total_org = overview.get("num_members", 0)
    if total_org and total_fetched < total_org:
        cap_note = f"（组织共 {total_org:,} 人，本次抓取 {total_fetched} 人）"
    else:
        cap_note = ""
    st.info(f"共获取到 **{total_fetched:,}** 位成员{cap_note}", icon="💡")

    df = pd.DataFrame(rows)
    display_cols = [
        "username", "fullname", "member_type", "location",
        "affiliation_type", "employer",
        "num_followers", "num_models", "num_datasets", "num_spaces",
        "num_discussions", "num_papers", "num_upvotes", "num_likes",
        "is_pro", "linkedin_url", "twitter_url", "github_url", "bluesky_url", "scholar_url",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    df_display = df[display_cols].copy()
    for col in ["num_followers", "num_models", "num_datasets", "num_spaces",
                "num_discussions", "num_papers", "num_upvotes", "num_likes"]:
        if col in df_display.columns:
            df_display[col] = pd.to_numeric(df_display[col], errors="coerce").fillna(0).astype(int)
    if "is_pro" in df_display.columns:
        df_display["is_pro"] = df_display["is_pro"].apply(lambda x: "✅" if x else "")

    st.dataframe(
        df_display, use_container_width=True, hide_index=True,
        column_config={
            "username":         st.column_config.TextColumn("HF 用户名"),
            "fullname":         st.column_config.TextColumn("姓名"),
            "member_type":      st.column_config.TextColumn("类型"),
            "location":         st.column_config.TextColumn("地区"),
            "affiliation_type": st.column_config.TextColumn("身份"),
            "employer":         st.column_config.TextColumn("机构"),
            "num_followers":    st.column_config.NumberColumn("Followers", format="%d"),
            "num_models":       st.column_config.NumberColumn("Models", format="%d"),
            "num_datasets":     st.column_config.NumberColumn("Datasets", format="%d"),
            "num_spaces":       st.column_config.NumberColumn("Spaces", format="%d"),
            "num_discussions":  st.column_config.NumberColumn("Discussions", format="%d"),
            "num_papers":       st.column_config.NumberColumn("Papers", format="%d"),
            "num_upvotes":      st.column_config.NumberColumn("Upvotes", format="%d"),
            "num_likes":        st.column_config.NumberColumn("Likes", format="%d"),
            "is_pro":           st.column_config.TextColumn("PRO"),
            "linkedin_url":     st.column_config.LinkColumn("LinkedIn", display_text="🔗"),
            "twitter_url":      st.column_config.LinkColumn("X/Twitter", display_text="🐦"),
            "github_url":       st.column_config.LinkColumn("GitHub", display_text="💻"),
            "bluesky_url":      st.column_config.LinkColumn("Bluesky", display_text="🦋"),
            "scholar_url":      st.column_config.LinkColumn("Scholar", display_text="📚"),
        },
    )

    export_cols = [c for c in HF_ORG_CSV_FIELDS if c in df.columns]
    csv_bytes = df[export_cols].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label=f"⬇️ 下载完整 CSV（{len(export_cols)} 个字段）",
        data=csv_bytes,
        file_name=f"hf_org_{org_name}.csv",
        mime="text/csv",
        type="primary",
    )
    st.success("✅ 数据已保存，前往「🗂️ HF 历史」页面查看所有记录。")


# ============ Main ============
st.title("🤗 HF 采集")
st.caption("采集 Hugging Face 项目贡献者 或 组织成员信息。")
st.markdown("---")

tab_proj, tab_org = st.tabs(["🧠 项目贡献者", "🏛️ 组织成员"])


# ──────────────── Tab 1：项目贡献者 ────────────────
with tab_proj:
    with st.expander("📋 支持的输入格式"):
        st.markdown("""
| 格式 | 示例 |
|------|------|
| `namespace/repo-name` | `meta-llama/Llama-3.1-8B` |
| HF Model URL | `https://huggingface.co/meta-llama/Llama-3.1-8B` |
| HF Dataset URL | `https://huggingface.co/datasets/openai/gsm8k` |
| HF Space URL | `https://huggingface.co/spaces/gradio/hello_world` |

> 只统计**已绑定 HF 账号**的 git 提交者；无 additions/deletions 行数（HF API 不提供）。
        """)

    proj_job_id = st.session_state.get("hf_proj_job_id")
    # 页面刷新后 session_state 丢失，从进程级 _jobs 恢复
    if not proj_job_id:
        running = list_running_jobs(job_type="proj")
        if running:
            proj_job_id = running[0]
            st.session_state["hf_proj_job_id"] = proj_job_id
    proj_job = get_job(proj_job_id) if proj_job_id else None

    if proj_job and proj_job["status"] == "running":
        st.subheader(f"🔄 正在采集：{proj_job['repo']}")
        _show_running(proj_job)

    elif proj_job and proj_job["status"] == "complete":
        details = proj_job.get("details") or {}
        full_name = proj_job["repo"]
        hf_type = details.get("hf_type", "model")
        st.success(f"✅ **{full_name}** 采集完成！")
        _show_proj_results(full_name, hf_type, details)
        st.divider()
        if st.button("🔄 采集另一个项目", key="proj_reset"):
            cleanup_job(proj_job_id)
            del st.session_state["hf_proj_job_id"]
            st.rerun()

    elif proj_job and proj_job["status"] == "error":
        st.error(f"❌ 采集失败：{proj_job['error']}")
        if st.button("重新开始", key="proj_retry"):
            cleanup_job(proj_job_id)
            del st.session_state["hf_proj_job_id"]
            st.rerun()

    else:
        if proj_job_id:
            del st.session_state["hf_proj_job_id"]

        col_input, col_btn, col_resume = st.columns([6, 2, 2])
        with col_input:
            proj_input = st.text_input(
                "项目地址",
                placeholder="meta-llama/Llama-3.1-8B 或完整 HF URL",
                key="proj_input",
            )
        with col_btn:
            st.markdown("<br>", unsafe_allow_html=True)
            proj_run = st.button("🚀 开始采集", type="primary", use_container_width=True, key="proj_run")
        with col_resume:
            st.markdown("<br>", unsafe_allow_html=True)
            proj_resume = st.checkbox("⚡ 续传", key="proj_resume",
                                      help="跳过已成功获取 Profile 的用户，仅补抓缺失数据")

        if proj_run:
            parsed = parse_hf_repo(proj_input or "")
            if not parsed:
                st.error("格式不正确，支持：`namespace/repo-name` 或完整 HF URL（含 datasets/spaces 路径）")
                st.stop()
            full_name, hf_type = parsed

            existing_jid = get_active_job_for_repo(full_name)
            if existing_jid:
                st.session_state["hf_proj_job_id"] = existing_jid
                st.rerun()

            job_id = create_job(full_name, job_type="proj")
            st.session_state["hf_proj_job_id"] = job_id
            threading.Thread(
                target=run_hf_scrape_job,
                args=(job_id, full_name, hf_type, token, proj_resume),
                daemon=False,
            ).start()
            st.rerun()


# ──────────────── Tab 2：组织成员 ────────────────
with tab_org:
    with st.expander("📋 说明"):
        st.markdown("""
| 格式 | 示例 |
|------|------|
| 组织名称 | `NanyangTechnologicalUniversity` |
| 组织主页 URL | `https://huggingface.co/NanyangTechnologicalUniversity` |

**注意：**
- 使用 `huggingface_hub` 库获取全量成员，无人数上限
- 无需 Token 即可获取公开组织成员；有 Token 可提升速率限制
- 每位成员的 Profile（地区、简介等）需额外抓取，成员越多耗时越长
        """)

    org_job_id = st.session_state.get("hf_org_job_id")
    # 页面刷新后 session_state 丢失，从进程级 _jobs 恢复
    if not org_job_id:
        running = list_running_jobs(job_type="org")
        if running:
            org_job_id = running[0]
            st.session_state["hf_org_job_id"] = org_job_id
    org_job = get_job(org_job_id) if org_job_id else None

    if org_job and org_job["status"] == "running":
        st.subheader(f"🔄 正在采集：{org_job['repo']}")
        _show_running(org_job)

    elif org_job and org_job["status"] == "complete":
        overview = org_job.get("details") or {}
        org_name = org_job["repo"]
        st.success(f"✅ **{overview.get('fullname') or org_name}** 成员采集完成！")
        _show_org_results(org_name, overview)
        st.divider()
        if st.button("🔄 采集另一个组织", key="org_reset"):
            cleanup_job(org_job_id)
            del st.session_state["hf_org_job_id"]
            st.rerun()

    elif org_job and org_job["status"] == "error":
        st.error(f"❌ 采集失败：{org_job['error']}")
        if st.button("重新开始", key="org_retry"):
            cleanup_job(org_job_id)
            del st.session_state["hf_org_job_id"]
            st.rerun()

    else:
        if org_job_id:
            del st.session_state["hf_org_job_id"]

        col_input, col_btn, col_resume = st.columns([6, 2, 2])
        with col_input:
            org_input = st.text_input(
                "组织地址",
                placeholder="NanyangTechnologicalUniversity 或 https://huggingface.co/NanyangTechnologicalUniversity",
                key="org_input",
            )
        with col_btn:
            st.markdown("<br>", unsafe_allow_html=True)
            org_run = st.button("🚀 开始采集", type="primary", use_container_width=True, key="org_run")
        with col_resume:
            st.markdown("<br>", unsafe_allow_html=True)
            org_resume = st.checkbox("⚡ 续传", key="org_resume",
                                     help="跳过已成功获取 Profile 的成员，仅补抓缺失数据")

        if org_run:
            org_name = parse_hf_org(org_input or "")
            if not org_name:
                st.error("格式不正确，请输入组织名称（如 `NanyangTechnologicalUniversity`）或组织主页 URL")
                st.stop()

            existing_jid = get_active_job_for_repo(org_name)
            if existing_jid:
                st.session_state["hf_org_job_id"] = existing_jid
                st.rerun()

            job_id = create_job(org_name, job_type="org")
            st.session_state["hf_org_job_id"] = job_id
            threading.Thread(
                target=run_hf_org_scrape_job,
                args=(job_id, org_name, token, org_resume),
                daemon=False,
            ).start()
            st.rerun()
