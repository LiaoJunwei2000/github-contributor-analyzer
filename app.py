import os
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from main import (
    fetch_repo_details,
    fetch_all_contributors,
    poll_contributor_stats,
    merge_contrib_and_stats,
    enrich_with_user_details,
    CSV_FIELDS,
)

load_dotenv()

st.set_page_config(
    page_title="GitHub Contributor Analyzer",
    page_icon="🐙",
    layout="wide",
)

# ============ Sidebar ============
with st.sidebar:
    st.title("🐙 GH Contributor Analyzer")
    st.markdown("---")

    token = st.text_input(
        "GitHub Token",
        value=os.getenv("GITHUB_TOKEN", ""),
        type="password",
        placeholder="ghp_xxxxxxxxxxxx",
    )
    st.caption("需要 `public_repo` 权限。[创建 Token ↗](https://github.com/settings/tokens/new)")

    st.markdown("---")

    with st.expander("⚠️ 为什么人数比 GitHub 主页少？", expanded=True):
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

    st.markdown("---")

    with st.expander("📋 全部可获取字段说明", expanded=False):
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

# ============ Main ============
st.header("GitHub 仓库贡献者分析")
st.caption("基于 GitHub REST API，统计仓库贡献者的 commit 数、代码行数变更及个人主页信息。")

repo_input = st.text_input(
    "仓库地址",
    placeholder="owner/repo，例如：facebook/react",
    label_visibility="collapsed",
)

col1, col2 = st.columns([1, 5])
with col1:
    run_btn = st.button("开始分析", type="primary", use_container_width=True)
with col2:
    include_anon = st.checkbox("包含匿名贡献者", value=False)

# ============ 执行分析 ============
if run_btn:
    if not token:
        st.error("请在左侧填写 GitHub Token")
        st.stop()
    if not repo_input or "/" not in repo_input:
        st.error("请输入正确的仓库格式，例如：facebook/react")
        st.stop()

    repo = repo_input.strip()

    with st.status("正在分析中...", expanded=True) as status:

        # Step 1: 仓库信息
        st.write("📋 获取仓库基本信息...")
        details = fetch_repo_details(repo, token)
        if not details:
            status.update(label="分析失败", state="error")
            st.error("无法获取仓库信息，请检查仓库名称和 Token 权限。")
            st.stop()

        with st.container():
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("⭐ Stars", f"{details.get('stargazers_count', 0):,}")
            c2.metric("🍴 Forks", f"{details.get('forks_count', 0):,}")
            c3.metric("👁 Watchers", f"{details.get('subscribers_count', 0):,}")
            c4.metric("🌐 Language", details.get("language") or "N/A")

        # Step 2: 贡献者列表
        st.write("👥 分页抓取贡献者列表...")
        all_contribs = fetch_all_contributors(repo, token, include_anon)
        if not all_contribs:
            status.update(label="分析失败", state="error")
            st.error("未能获取贡献者数据。")
            st.stop()
        st.write(f"✅ 获取到 **{len(all_contribs)}** 位贡献者（基于 git commit 历史，见左侧说明）")

        # Step 3: 统计数据
        st.write("📊 获取代码增删统计（首次可能需要等待 GitHub 计算）...")
        stats = poll_contributor_stats(repo, token)
        st.write("✅ 统计数据获取完成")

        # Step 4: 合并数据
        st.write("🔀 合并与整理数据...")
        merged = merge_contrib_and_stats(all_contribs, stats)

        # Step 5: 用户详情
        st.write(f"🔍 并发抓取 {len(merged)} 位用户的 Profile 信息...")
        enriched = enrich_with_user_details(merged, token)

        status.update(label="分析完成 ✅", state="complete", expanded=False)

    # ============ 展示结果 ============
    st.markdown("---")

    st.subheader(f"📊 {details.get('full_name')} 贡献者排行")

    st.info(
        f"共获取到 **{len(enriched)}** 位贡献者。"
        "表格仅展示核心字段，**email、个人主页、Twitter、bio 等完整信息请下载 CSV**。"
        "左侧「全部可获取字段说明」中查看所有字段详情。",
        icon="💡",
    )

    df = pd.DataFrame(enriched)

    # 展示列
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

    # ============ 下载 CSV ============
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
    st.caption(
        f"CSV 包含 {len(CSV_FIELDS)} 个字段：rank、login、name、company、location、**email**、"
        "**blog**、**twitter_username**、hireable、public_repos、public_gists、followers、following、"
        "total_commits、total_additions、total_deletions、net_lines、total_changes、"
        "avg_changes_per_commit、addition_deletion_ratio、contributions_on_default_branch、"
        "**profile_url**、**avatar_url**、account_created、last_updated"
    )
