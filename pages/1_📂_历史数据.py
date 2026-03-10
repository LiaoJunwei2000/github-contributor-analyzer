import re
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from db import init_db, list_repos, get_contributors, delete_repo, list_tags, get_repo_tags, add_repo_tag, remove_repo_tag
from main import CSV_FIELDS

st.set_page_config(page_title="历史数据", page_icon="📂", layout="wide")
init_db()

st.title("📂 历史数据浏览")

# ── 仓库列表 ──────────────────────────────────────────────
repos = list_repos()
if not repos:
    st.warning("数据库为空，请先在「数据采集」页面爬取至少一个仓库。")
    st.stop()

# ── 仓库选择 + 元信息 ─────────────────────────────────────
col_sel, col_stars, col_forks, col_lang, col_time = st.columns([3, 1, 1, 1, 2])

with col_sel:
    repo_options = {f"{r['full_name']}": r for r in repos}
    selected_name = st.selectbox(
        "选择仓库",
        list(repo_options.keys()),
        format_func=lambda x: f"📦 {x}",
    )
    repo_meta = repo_options[selected_name]
    if repo_meta.get("description"):
        st.caption(repo_meta["description"])

with col_stars:
    st.metric("⭐ Stars", f"{repo_meta.get('stars', 0):,}")

with col_forks:
    st.metric("🍴 Forks", f"{repo_meta.get('forks', 0):,}")

with col_lang:
    st.metric("🌐 语言", repo_meta.get("language") or "N/A")

with col_time:
    st.metric("📅 采集时间", str(repo_meta.get("scraped_at", ""))[:16])

# ── 标签 ──────────────────────────────────────────────────
all_tags = list_tags()
repo_tags = get_repo_tags(selected_name)
current_tag_ids = {t["id"] for t in repo_tags}
all_tag_name_map = {t["name"]: t["id"] for t in all_tags}

tag_col, tag_add_col, tag_rem_col = st.columns([3, 2, 2])
with tag_col:
    if repo_tags:
        badges = " ".join(
            f"<span style='background:{t['color']};color:#fff;border-radius:4px;"
            f"padding:2px 8px;font-size:0.82rem'>{t['name']}</span>"
            for t in repo_tags
        )
        st.markdown("**🏷️ 标签** &nbsp;" + badges, unsafe_allow_html=True)
    else:
        st.markdown("**🏷️ 标签** &nbsp;<span style='color:#999;font-size:0.85rem'>暂无标签</span>", unsafe_allow_html=True)

with tag_add_col:
    if all_tags:
        addable = [t["name"] for t in all_tags if t["id"] not in current_tag_ids]
        sel_add = st.multiselect(
            "添加标签", addable,
            key=f"hist_add_{selected_name}", label_visibility="collapsed",
            placeholder="添加标签...",
        )
        if sel_add:
            if st.button("✅ 添加", key=f"hist_btn_add_{selected_name}"):
                for tname in sel_add:
                    add_repo_tag(selected_name, all_tag_name_map[tname])
                st.rerun()

with tag_rem_col:
    if repo_tags:
        sel_rem = st.multiselect(
            "移除标签", [t["name"] for t in repo_tags],
            key=f"hist_rem_{selected_name}", label_visibility="collapsed",
            placeholder="移除标签...",
        )
        if sel_rem:
            if st.button("❌ 移除", key=f"hist_btn_rem_{selected_name}"):
                for tname in sel_rem:
                    remove_repo_tag(selected_name, all_tag_name_map[tname])
                st.rerun()

# 危险操作：删除（checkbox 确认后才显示删除按钮）
with st.expander("⚠️ 危险操作"):
    st.warning(f"删除后将移除 **{selected_name}** 的全部贡献者记录，不可恢复。")
    confirm = st.checkbox(f"我确认要删除 **{selected_name}** 的所有数据", key="confirm_delete")
    if confirm:
        if st.button("🗑️ 确认删除此仓库数据", type="secondary"):
            delete_repo(selected_name)
            st.success("已删除！")
            st.rerun()

st.markdown("---")

# ── 加载数据 ──────────────────────────────────────────────
raw = get_contributors(selected_name)
if not raw:
    st.error("该仓库暂无贡献者数据。")
    st.stop()

df = pd.DataFrame(raw)
num_cols = [
    "total_commits", "total_additions", "total_deletions",
    "net_lines", "total_changes", "followers", "following",
    "public_repos", "avg_changes_per_commit", "contributions_on_default_branch",
]
for col in num_cols:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

# ══════════════════════════════════════════════════════════
# Tabs
# ══════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs(["👤 开发者画像", "🌐 多维分析", "📋 数据表格", "⬇️ 导出"])


# ──────────────────────────────────────────────────────────
# TAB 1：开发者画像
# ──────────────────────────────────────────────────────────
with tab1:
    left, right = st.columns([1, 2], gap="large")

    with left:
        st.subheader("贡献者列表")
        search = st.text_input("🔍 搜索用户名 / 姓名", placeholder="输入关键词...", key="search1")
        companies = ["全部"] + sorted(df["company"].dropna().unique().tolist())
        filter_company = st.selectbox("筛选公司", companies, key="company1")

        filtered = df.copy()
        if search:
            mask = (
                filtered["login"].str.contains(search, case=False, na=False) |
                filtered["name"].str.contains(search, case=False, na=False)
            )
            filtered = filtered[mask]
        if filter_company != "全部":
            filtered = filtered[filtered["company"] == filter_company]
        filtered = filtered.reset_index(drop=True)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}

        def make_label(r):
            try:
                rank = int(r["rank"])
            except (ValueError, TypeError):
                rank = 0
            prefix = medals.get(rank, f"#{rank}")
            raw_name = r.get("name")
            name_part = f"  ({raw_name})" if raw_name and str(raw_name) not in ("None", "") else ""
            login = str(r["login"]) if r.get("login") else "unknown"
            return f"{prefix}  {login}{name_part}"

        options = [make_label(r) for _, r in filtered.iterrows()]

        if not options:
            st.info("无匹配结果")
        else:
            if len(options) > 10:
                st.caption("↕ 可滚动")
            selected_idx = st.radio(
                "点击查看详情",
                range(len(options)),
                format_func=lambda i: options[i],
                label_visibility="collapsed",
                key="radio1",
            )

            with right:
                person = filtered.iloc[selected_idx]
                st.subheader("开发者详情")

                avatar_col, info_col = st.columns([1, 3])
                with avatar_col:
                    if person.get("avatar_url"):
                        st.image(person["avatar_url"], width=100)
                with info_col:
                    display_name = person.get("name") or person["login"]
                    st.markdown(f"### {display_name}")
                    try:
                        rank_display = int(person["rank"])
                    except (ValueError, TypeError):
                        rank_display = "?"
                    st.markdown(f"**@{person['login']}**  ·  排名 #{rank_display}")
                    if person.get("bio"):
                        st.caption(person["bio"])

                st.markdown("---")

                with st.container(border=True):
                    detail_cols = st.columns(2)
                    fields = [
                        ("🏢 公司", "company"), ("📍 地区", "location"),
                        ("📧 邮箱", "email"), ("🐦 Twitter", "twitter_username"),
                    ]
                    for i, (label, key) in enumerate(fields):
                        val = person.get(key)
                        if val and str(val) not in ("None", "0", ""):
                            detail_cols[i % 2].markdown(f"**{label}**  \n{val}")

                    # hireable 显示
                    hireable_val = person.get("hireable")
                    try:
                        hireable_int = int(hireable_val) if hireable_val is not None else 0
                    except (ValueError, TypeError):
                        hireable_int = 0
                    hire_display = "✅ 开放求职" if hireable_int == 1 else "—"
                    detail_cols[0].markdown(f"**💼 求职状态**  \n{hire_display}")

                    blog_val = person.get("blog")
                    if blog_val and str(blog_val) not in ("None", ""):
                        detail_cols[1].markdown(f"**🌐 主页**  \n{blog_val}")

                if person.get("profile_url"):
                    st.link_button("🔗 查看 GitHub 主页", person["profile_url"])

                st.markdown("---")
                st.markdown("#### 代码贡献")
                m1, m2, m3, m4 = st.columns(4)
                try:
                    m1.metric("Commits", f"{int(person.get('total_commits', 0)):,}")
                    m2.metric("新增行", f"{int(person.get('total_additions', 0)):,}")
                    m3.metric("删除行", f"{int(person.get('total_deletions', 0)):,}")
                    m4.metric("净增行", f"{int(person.get('net_lines', 0)):,}")
                except (ValueError, TypeError):
                    pass
                m5, m6, m7, _ = st.columns(4)
                try:
                    m5.metric("Followers", f"{int(person.get('followers', 0)):,}")
                    m6.metric("公开 Repos", f"{int(person.get('public_repos', 0)):,}")
                    m7.metric("默认分支贡献", f"{int(person.get('contributions_on_default_branch', 0)):,}")
                except (ValueError, TypeError):
                    pass

                st.markdown("#### 贡献指标对比（与 Top10 均值）")
                top10 = df.nsmallest(10, "rank")
                compare_data = pd.DataFrame({
                    "指标": ["Commits", "新增行(千)", "删除行(千)", "净增行(千)"],
                    "本人": [
                        person.get("total_commits", 0),
                        person.get("total_additions", 0) / 1000,
                        person.get("total_deletions", 0) / 1000,
                        max(person.get("net_lines", 0), 0) / 1000,
                    ],
                    "Top10均值": [
                        top10["total_commits"].mean(),
                        top10["total_additions"].mean() / 1000,
                        top10["total_deletions"].mean() / 1000,
                        top10["net_lines"].clip(lower=0).mean() / 1000,
                    ],
                })
                fig_bar = px.bar(
                    compare_data.melt(id_vars="指标", var_name="类别", value_name="值"),
                    x="指标", y="值", color="类别", barmode="group",
                    color_discrete_map={"本人": "#4f8bff", "Top10均值": "#aaaaaa"},
                    height=280,
                )
                fig_bar.update_layout(margin=dict(t=10, b=10), legend_title_text="")
                st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})


# ──────────────────────────────────────────────────────────
# TAB 2：多维分析
# ──────────────────────────────────────────────────────────
with tab2:
    num_contributors = len(df)
    num_with_location = int(df["location"].notna().sum())

    o1, o2, o3, o4, o5 = st.columns(5)
    o1.metric("贡献者总数", f"{num_contributors:,}")
    o2.metric("总 Commits", f"{int(df['total_commits'].sum()):,}", f"共 {num_contributors} 人贡献")
    o3.metric("总新增行", f"{int(df['total_additions'].sum()):,}")
    o4.metric("总删除行", f"{int(df['total_deletions'].sum()):,}")
    o5.metric("有地区信息", f"{num_with_location} 人", f"占 {num_with_location / max(num_contributors, 1):.0%}")

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 🏢 公司贡献分析")
        company_df = df[df["company"].notna() & (df["company"] != "")].copy()
        company_df["company"] = company_df["company"].str.strip().str.lstrip("@")
        if not company_df.empty:
            company_counts = (
                company_df.groupby("company")
                .agg(人数=("login", "count"), Commits=("total_commits", "sum"))
                .sort_values("人数", ascending=False).head(15).reset_index()
            )
            fig_company = px.bar(
                company_counts, x="人数", y="company", orientation="h",
                color="Commits", color_continuous_scale="Blues",
                labels={"company": ""}, height=400,
            )
            fig_company.update_layout(yaxis=dict(autorange="reversed"), margin=dict(t=10))
            st.plotly_chart(fig_company, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("无公司信息数据")

    with col2:
        st.markdown("#### 📊 Commits 分布直方图")
        commit_df = df[df["total_commits"] > 0]
        fig_hist = px.histogram(
            commit_df, x="total_commits", nbins=40,
            labels={"total_commits": "Commit 数量"},
            color_discrete_sequence=["#4f8bff"], height=400,
        )
        fig_hist.update_layout(bargap=0.05, margin=dict(t=10), yaxis_title="人数")
        st.plotly_chart(fig_hist, use_container_width=True, config={"displayModeBar": False})

    col3, col4 = st.columns(2)
    with col3:
        st.markdown("#### 🌍 地区分布（Top 15）")
        loc_df = df[df["location"].notna() & (df["location"] != "")].copy()
        if not loc_df.empty:
            def extract_region(loc):
                parts = str(loc).split(",")
                region = parts[-1].strip()
                if re.match(r"^\d[\d\s\-]*$", region) or region == "":
                    region = parts[-2].strip() if len(parts) >= 2 else region
                return region

            loc_df["region"] = loc_df["location"].apply(extract_region)
            loc_df = loc_df[loc_df["region"] != ""]
            region_counts = (
                loc_df.groupby("region").agg(人数=("login", "count"))
                .sort_values("人数", ascending=False).head(15).reset_index()
            )
            fig_loc = px.bar(
                region_counts, x="人数", y="region", orientation="h",
                color="人数", color_continuous_scale="Greens",
                labels={"region": ""}, height=400,
            )
            fig_loc.update_layout(
                yaxis=dict(autorange="reversed"),
                coloraxis_showscale=False, margin=dict(t=10),
            )
            st.plotly_chart(fig_loc, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("无地区信息数据")

    with col4:
        st.markdown("#### 👥 Followers vs Commits")
        scatter_df = df[(df["followers"] > 0) & (df["total_commits"] > 0)].copy()
        if not scatter_df.empty:
            fig_scatter = px.scatter(
                scatter_df, x="total_commits", y="followers",
                hover_name="login",
                hover_data={"name": True, "company": True, "location": True},
                color="total_changes", color_continuous_scale="Viridis",
                size="total_changes", size_max=30,
                labels={"total_commits": "Commits", "followers": "Followers", "total_changes": "总变更行"},
                height=400,
            )
            fig_scatter.update_layout(margin=dict(t=10))
            st.plotly_chart(fig_scatter, use_container_width=True, config={"displayModeBar": False})

    col5, col6 = st.columns(2)
    with col5:
        st.markdown("#### 💼 开放求职状态")
        hire_counts = df["hireable"].map(
            lambda x: "开放求职 ✅" if pd.to_numeric(x, errors="coerce") == 1 else "未开放 / 未知"
        ).value_counts().reset_index()
        hire_counts.columns = ["状态", "人数"]
        fig_hire = px.pie(
            hire_counts, names="状态", values="人数",
            color_discrete_sequence=["#2ecc71", "#bdc3c7"], height=300,
        )
        fig_hire.update_layout(margin=dict(t=10))
        st.plotly_chart(fig_hire, use_container_width=True, config={"displayModeBar": False})

    with col6:
        st.markdown("#### 📈 Top 20 新增 vs 删除行")
        top20 = df.nsmallest(20, "rank")[["login", "total_additions", "total_deletions"]]
        fig_stacked = go.Figure([
            go.Bar(name="新增行", x=top20["login"], y=top20["total_additions"], marker_color="#2ecc71"),
            go.Bar(name="删除行", x=top20["login"], y=top20["total_deletions"], marker_color="#e74c3c"),
        ])
        fig_stacked.update_layout(
            barmode="group", xaxis_tickangle=-40, height=300,
            margin=dict(t=10), legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_stacked, use_container_width=True, config={"displayModeBar": False})


# ──────────────────────────────────────────────────────────
# TAB 3：数据表格
# ──────────────────────────────────────────────────────────
with tab3:
    st.subheader(f"完整数据表  ·  共 {len(df)} 位贡献者")

    # 筛选控件：一行三列
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        kw = st.text_input("🔍 搜索", placeholder="用户名 / 姓名 / 公司...", key="search_table")
    with fc2:
        sort_by = st.selectbox("排序字段", ["rank", "total_commits", "total_changes", "followers", "total_additions"], key="sort_table")
    with fc3:
        top_n = st.slider("显示前 N 名", 10, len(df), min(100, len(df)), 10, key="topn_table")

    tdf = df.copy()
    if kw:
        mask = (
            tdf["login"].str.contains(kw, case=False, na=False) |
            tdf["name"].str.contains(kw, case=False, na=False) |
            tdf["company"].str.contains(kw, case=False, na=False)
        )
        tdf = tdf[mask]
    tdf = tdf.sort_values(sort_by).head(top_n).reset_index(drop=True)

    # 确保 hireable 列为数值，使 CheckboxColumn 能正确渲染
    if "hireable" in tdf.columns:
        tdf["hireable"] = pd.to_numeric(tdf["hireable"], errors="coerce").fillna(0).astype(bool)

    display_cols = [
        "rank", "login", "name", "company", "location",
        "total_commits", "total_additions", "total_deletions",
        "net_lines", "total_changes", "avg_changes_per_commit",
        "followers", "public_repos", "contributions_on_default_branch",
        "email", "blog", "twitter_username", "hireable", "account_created",
    ]
    display_cols = [c for c in display_cols if c in tdf.columns]

    st.caption("💡 点击列标题可排序，拖拽可调整列宽")
    st.dataframe(
        tdf[display_cols],
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "rank": st.column_config.NumberColumn("排名", width="small"),
            "login": st.column_config.TextColumn("用户名"),
            "name": st.column_config.TextColumn("姓名"),
            "company": st.column_config.TextColumn("公司"),
            "location": st.column_config.TextColumn("地区"),
            "total_commits": st.column_config.NumberColumn("Commits", format="%d"),
            "total_additions": st.column_config.NumberColumn("新增行", format="%d"),
            "total_deletions": st.column_config.NumberColumn("删除行", format="%d"),
            "net_lines": st.column_config.NumberColumn("净增行", format="%d"),
            "total_changes": st.column_config.NumberColumn("总变更", format="%d"),
            "avg_changes_per_commit": st.column_config.NumberColumn("均变更/commit", format="%.1f"),
            "followers": st.column_config.NumberColumn("Followers", format="%d"),
            "public_repos": st.column_config.NumberColumn("公开Repos", format="%d"),
            "contributions_on_default_branch": st.column_config.NumberColumn("默认分支贡献", format="%d"),
            "email": st.column_config.TextColumn("邮箱"),
            "blog": st.column_config.LinkColumn("主页"),
            "twitter_username": st.column_config.TextColumn("Twitter"),
            "hireable": st.column_config.CheckboxColumn("开放求职"),
            "account_created": st.column_config.TextColumn("注册时间"),
        },
    )


# ──────────────────────────────────────────────────────────
# TAB 4：导出
# ──────────────────────────────────────────────────────────
with tab4:
    st.subheader("下载数据")

    ec1, ec2 = st.columns(2)

    with ec1:
        st.markdown("#### 📄 完整 CSV（全部字段）")
        all_cols = [c for c in CSV_FIELDS if c in df.columns]
        st.caption(f"包含所有 {len(all_cols)} 个字段，含 email、主页、bio、avatar 等")
        csv_full = df[all_cols].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            label=f"⬇️ 下载完整 CSV  （{len(df)} 条 × {len(all_cols)} 字段）",
            data=csv_full,
            file_name=f"contributors_{selected_name.replace('/', '_')}_full.csv",
            mime="text/csv",
            type="primary",
            use_container_width=True,
        )

    with ec2:
        st.markdown("#### 📊 精简 CSV（核心贡献字段）")
        slim_cols = [
            "rank", "login", "name", "company", "location",
            "total_commits", "total_additions", "total_deletions",
            "net_lines", "total_changes", "avg_changes_per_commit",
            "followers", "public_repos", "contributions_on_default_branch",
        ]
        slim_cols = [c for c in slim_cols if c in df.columns]
        st.caption(f"包含 {len(slim_cols)} 个核心字段，排名、commits、代码行数等，适合快速分析")
        csv_slim = df[slim_cols].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            label=f"⬇️ 下载精简 CSV  （{len(df)} 条 × {len(slim_cols)} 字段）",
            data=csv_slim,
            file_name=f"contributors_{selected_name.replace('/', '_')}_slim.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.markdown("---")
    st.markdown(f"#### 🗂️ 数据库中的所有仓库（共 {len(repos)} 个）")
    repos_df = pd.DataFrame(repos)[["full_name", "stars", "forks", "language", "scraped_at"]]
    repos_df.columns = ["仓库", "Stars", "Forks", "主要语言", "采集时间"]
    st.dataframe(repos_df, use_container_width=True, hide_index=True)
