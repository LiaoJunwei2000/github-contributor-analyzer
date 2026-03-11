import os
import time
import threading
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from db import (
    list_hf_repos, get_hf_contributors, delete_hf_repo,
    list_hf_orgs, get_hf_org_members, delete_hf_org,
)
from hf_runner import run_hf_org_refresh_job, run_hf_proj_refresh_job
from background_jobs import create_job, get_job, cleanup_job, list_running_jobs

load_dotenv()

def _default_token() -> str:
    try:
        return st.secrets.get("HF_TOKEN", "") or os.getenv("HF_TOKEN", "")
    except Exception:
        return os.getenv("HF_TOKEN", "")

_PHASE_TEXT = {
    "enriching": "🔍 重新抓取用户 Profile...",
    "waiting":   "⏸️ API 限速等待中...",
    "saving":    "💾 保存到数据库...",
}

def _refresh_progress(job: dict):
    """显示刷新 Profile 任务的内联进度条（不 rerun，由调用方决定）。"""
    phase = job.get("phase", "enriching")
    done  = job.get("done", 0)
    total = job.get("total", 1) or 1
    rl_wait_until = job.get("rl_wait_until", 0) or 0

    if phase == "waiting":
        remaining_s = max(0.0, rl_wait_until - time.time()) if rl_wait_until > 0 else 0
        reset_total = job.get("rl_wait_s", remaining_s) or remaining_s or 300
        pct = max(0.0, min(1.0, 1.0 - remaining_s / reset_total)) if reset_total > 0 else 1.0
        st.warning(f"⏸️ 限速等待 {remaining_s:.0f}s · 已完成 {done}/{total}")
        st.progress(pct)
    else:
        pct = done / total
        label = _PHASE_TEXT.get(phase, phase)
        st.progress(pct, text=f"{label} {done}/{total}")

# 恢复刷新任务（页面刷新后从进程级 _jobs 重建 session_state）
if "hf_refresh_jobs" not in st.session_state:
    st.session_state["hf_refresh_jobs"] = {}
for jid in list_running_jobs(job_type="refresh"):
    j = get_job(jid)
    if j and j["repo"] not in st.session_state["hf_refresh_jobs"]:
        st.session_state["hf_refresh_jobs"][j["repo"]] = jid

st.title("🗂️ HF 历史数据")
st.caption("查看所有已采集的 Hugging Face 项目贡献者及组织成员数据。")
st.markdown("---")

repos = list_hf_repos()
orgs = list_hf_orgs()

if not repos and not orgs:
    st.info("暂无 HF 数据，请前往「🤗 HF 采集」先采集项目或组织。", icon="💡")
    st.stop()

main_tab_proj, main_tab_org = st.tabs(["🧠 项目贡献者", "🏛️ 组织成员"])

# ══════════════════ 项目贡献者 Tab ══════════════════
with main_tab_proj:
    if not repos:
        st.info("暂无项目数据，请前往「🤗 HF 采集」→「🧠 项目贡献者」先采集。", icon="💡")
    else:
        tab_list, tab_contribs, tab_export = st.tabs(["📋 项目列表", "👥 贡献者数据", "⬇️ 导出"])

        # ── 项目列表 ──
        with tab_list:
            st.markdown(f"共 **{len(repos)}** 个已采集项目")
            TYPE_ICON = {"model": "🧠", "dataset": "📦", "space": "🚀"}

            for repo in repos:
                full_name = repo["full_name"]
                hf_type = repo.get("hf_type", "model")
                icon = TYPE_ICON.get(hf_type, "🤗")
                likes = repo.get("likes", 0) or 0
                downloads = repo.get("downloads", 0) or 0
                pipeline_tag = repo.get("pipeline_tag") or ""
                scraped_at = repo.get("scraped_at", "")[:16]
                n_contribs = len(get_hf_contributors(full_name))
                refresh_jid = st.session_state["hf_refresh_jobs"].get(full_name)
                refresh_job = get_job(refresh_jid) if refresh_jid else None

                with st.container(border=True):
                    col_info, col_meta, col_btn = st.columns([5, 3, 2])
                    with col_info:
                        st.markdown(f"**{icon} {full_name}**")
                        st.caption(
                            f"类型：{hf_type}"
                            + (f" · {pipeline_tag}" if pipeline_tag else "")
                            + f"  ·  ❤️ {likes:,}  ·  ⬇️ {downloads:,}"
                        )
                    with col_meta:
                        st.caption(f"贡献者：**{n_contribs}** 人")
                        st.caption(f"采集时间：{scraped_at}")
                    with col_btn:
                        if refresh_job and refresh_job["status"] == "running":
                            st.caption("♻️ 刷新中…")
                        elif st.button("♻️ 刷新 Profile", key=f"refresh_proj_{full_name}",
                                       help="重新抓取所有贡献者的 Profile（修复 orgs/bio/account_created 等字段）"):
                            tok = _default_token()
                            jid = create_job(full_name, job_type="refresh")
                            st.session_state["hf_refresh_jobs"][full_name] = jid
                            threading.Thread(
                                target=run_hf_proj_refresh_job,
                                args=(jid, full_name, hf_type, tok),
                                daemon=False,
                            ).start()
                            st.rerun()
                        if st.button("🗑️ 删除", key=f"del_proj_{full_name}", type="secondary"):
                            delete_hf_repo(full_name)
                            st.session_state["hf_refresh_jobs"].pop(full_name, None)
                            st.success(f"已删除 {full_name}")
                            st.rerun()

                    # 进度显示
                    if refresh_job:
                        if refresh_job["status"] == "running":
                            _refresh_progress(refresh_job)
                            time.sleep(1)
                            st.rerun()
                        elif refresh_job["status"] == "complete":
                            st.success(f"✅ {full_name} Profile 刷新完成！")
                            cleanup_job(refresh_jid)
                            st.session_state["hf_refresh_jobs"].pop(full_name, None)
                        elif refresh_job["status"] == "error":
                            st.error(f"❌ 刷新失败：{refresh_job['error'][:200]}")
                            cleanup_job(refresh_jid)
                            st.session_state["hf_refresh_jobs"].pop(full_name, None)

        # ── 贡献者数据 ──
        with tab_contribs:
            repo_names = [r["full_name"] for r in repos]
            selected = st.selectbox("选择项目", repo_names, key="hf_history_proj_select")

            if selected:
                rows = get_hf_contributors(selected)
                if not rows:
                    st.warning("该项目暂无贡献者数据。")
                else:
                    df = pd.DataFrame(rows)
                    col_loc, col_search = st.columns(2)
                    with col_loc:
                        loc_filter = st.text_input("按地区筛选", placeholder="例：China, US", key="proj_loc")
                    with col_search:
                        name_filter = st.text_input("按用户名/姓名筛选", placeholder="例：john", key="proj_name")

                    if loc_filter:
                        df = df[df["location"].fillna("").str.contains(loc_filter, case=False, na=False)]
                    if name_filter:
                        mask = (
                            df["username"].fillna("").str.contains(name_filter, case=False, na=False)
                            | df["fullname"].fillna("").str.contains(name_filter, case=False, na=False)
                        )
                        df = df[mask]

                    st.caption(f"显示 {len(df)} / {len(rows)} 位贡献者")
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

        # ── 导出 ──
        with tab_export:
            selected_export = st.selectbox("选择要导出的项目", [r["full_name"] for r in repos], key="hf_proj_export_select")
            if selected_export:
                rows = get_hf_contributors(selected_export)
                if not rows:
                    st.warning("该项目暂无数据可导出。")
                else:
                    df_exp = pd.DataFrame(rows)
                    HF_CSV_FIELDS = [
                        "rank", "username", "fullname", "location", "website", "bio",
                        "affiliation_type", "employer", "linkedin_url", "twitter_url",
                        "github_url", "bluesky_url", "scholar_url",
                        "is_pro", "num_followers", "num_following",
                        "num_models", "num_datasets", "num_spaces",
                        "num_discussions", "num_papers", "num_upvotes", "num_likes",
                        "orgs", "total_commits", "first_commit_at", "last_commit_at",
                        "profile_url", "avatar_url", "account_created",
                    ]
                    export_cols = [c for c in HF_CSV_FIELDS if c in df_exp.columns]
                    csv_bytes = df_exp[export_cols].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
                    st.metric("贡献者数量", len(rows))
                    st.download_button(
                        label=f"⬇️ 下载 CSV — {selected_export}",
                        data=csv_bytes,
                        file_name=f"hf_contributors_{selected_export.replace('/', '_')}.csv",
                        mime="text/csv",
                        type="primary",
                    )

# ══════════════════ 组织成员 Tab ══════════════════
with main_tab_org:
    if not orgs:
        st.info("暂无组织数据，请前往「🤗 HF 采集」→「🏛️ 组织成员」先采集。", icon="💡")
    else:
        tab_org_list, tab_org_members, tab_org_export = st.tabs(["📋 组织列表", "👥 成员数据", "⬇️ 导出"])

        # ── 组织列表 ──
        with tab_org_list:
            st.markdown(f"共 **{len(orgs)}** 个已采集组织")

            for org in orgs:
                org_name = org["name"]
                fullname = org.get("fullname") or org_name
                num_members = org.get("num_members", 0) or 0
                num_models = org.get("num_models", 0) or 0
                num_followers = org.get("num_followers", 0) or 0
                is_verified = bool(org.get("is_verified", 0))
                scraped_at = org.get("scraped_at", "")[:16]
                n_fetched = len(get_hf_org_members(org_name))
                refresh_jid = st.session_state["hf_refresh_jobs"].get(org_name)
                refresh_job = get_job(refresh_jid) if refresh_jid else None

                with st.container(border=True):
                    col_info, col_meta, col_btn = st.columns([5, 3, 2])
                    with col_info:
                        verified_badge = " ✅" if is_verified else ""
                        st.markdown(f"**🏛️ {fullname}{verified_badge}**")
                        st.caption(f"`{org_name}`  ·  👁 {num_followers:,} followers  ·  🧠 {num_models:,} models")
                    with col_meta:
                        st.caption(f"总成员：**{num_members:,}**（已抓取 {n_fetched}）")
                        st.caption(f"采集时间：{scraped_at}")
                    with col_btn:
                        if refresh_job and refresh_job["status"] == "running":
                            st.caption("♻️ 刷新中…")
                        elif st.button("♻️ 刷新 Profile", key=f"refresh_org_{org_name}",
                                       help="重新抓取所有成员的 Profile（修复 orgs/bio/account_created 等字段）"):
                            tok = _default_token()
                            jid = create_job(org_name, job_type="refresh")
                            st.session_state["hf_refresh_jobs"][org_name] = jid
                            threading.Thread(
                                target=run_hf_org_refresh_job,
                                args=(jid, org_name, tok),
                                daemon=False,
                            ).start()
                            st.rerun()
                        if st.button("🗑️ 删除", key=f"del_org_{org_name}", type="secondary"):
                            delete_hf_org(org_name)
                            st.session_state["hf_refresh_jobs"].pop(org_name, None)
                            st.success(f"已删除 {org_name}")
                            st.rerun()

                    # 进度显示（整行宽度）
                    if refresh_job:
                        if refresh_job["status"] == "running":
                            _refresh_progress(refresh_job)
                            time.sleep(1)
                            st.rerun()
                        elif refresh_job["status"] == "complete":
                            st.success(f"✅ {org_name} Profile 刷新完成！")
                            cleanup_job(refresh_jid)
                            st.session_state["hf_refresh_jobs"].pop(org_name, None)
                        elif refresh_job["status"] == "error":
                            st.error(f"❌ 刷新失败：{refresh_job['error'][:200]}")
                            cleanup_job(refresh_jid)
                            st.session_state["hf_refresh_jobs"].pop(org_name, None)

        # ── 成员数据 ──
        with tab_org_members:
            org_names = [o["name"] for o in orgs]
            selected_org = st.selectbox("选择组织", org_names, key="hf_org_history_select")

            if selected_org:
                rows = get_hf_org_members(selected_org)
                if not rows:
                    st.warning("该组织暂无成员数据。")
                else:
                    df = pd.DataFrame(rows)
                    col_loc, col_search = st.columns(2)
                    with col_loc:
                        loc_filter = st.text_input("按地区筛选", placeholder="例：Singapore", key="org_loc")
                    with col_search:
                        name_filter = st.text_input("按用户名/姓名筛选", placeholder="例：john", key="org_name_filter")

                    if loc_filter:
                        df = df[df["location"].fillna("").str.contains(loc_filter, case=False, na=False)]
                    if name_filter:
                        mask = (
                            df["username"].fillna("").str.contains(name_filter, case=False, na=False)
                            | df["fullname"].fillna("").str.contains(name_filter, case=False, na=False)
                        )
                        df = df[mask]

                    st.caption(f"显示 {len(df)} / {len(rows)} 位成员（按 Followers 降序）")
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

        # ── 导出 ──
        with tab_org_export:
            selected_org_export = st.selectbox("选择要导出的组织", [o["name"] for o in orgs], key="hf_org_export_select")
            if selected_org_export:
                rows = get_hf_org_members(selected_org_export)
                if not rows:
                    st.warning("该组织暂无数据可导出。")
                else:
                    df_exp = pd.DataFrame(rows)
                    ORG_CSV_FIELDS = [
                        "username", "fullname", "member_type", "location", "website", "bio",
                        "affiliation_type", "employer", "linkedin_url", "twitter_url",
                        "github_url", "bluesky_url", "scholar_url",
                        "is_pro", "num_followers", "num_following",
                        "num_models", "num_datasets", "num_spaces",
                        "num_discussions", "num_papers", "num_upvotes", "num_likes",
                        "orgs", "profile_url", "avatar_url", "account_created",
                    ]
                    export_cols = [c for c in ORG_CSV_FIELDS if c in df_exp.columns]
                    csv_bytes = df_exp[export_cols].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
                    st.metric("成员数量", len(rows))
                    st.download_button(
                        label=f"⬇️ 下载 CSV — {selected_org_export}",
                        data=csv_bytes,
                        file_name=f"hf_org_{selected_org_export}.csv",
                        mime="text/csv",
                        type="primary",
                    )
