import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from db import init_db, list_repos, get_contributors

st.set_page_config(page_title="贡献者看板", page_icon="📊", layout="wide")
init_db()

st.title("📊 贡献者分析看板")

# ── 仓库选择 ──────────────────────────────────────────────
repos = list_repos()
if not repos:
    st.warning("数据库为空，请先在「数据采集」页面爬取至少一个仓库。")
    st.stop()

repo_options = {f"{r['full_name']}  （{r['scraped_at'][:16]} 采集）": r["full_name"] for r in repos}
selected_label = st.selectbox("选择仓库", list(repo_options.keys()))
repo_full_name = repo_options[selected_label]

raw = get_contributors(repo_full_name)
if not raw:
    st.error("该仓库暂无数据。")
    st.stop()

df = pd.DataFrame(raw)

# 数值列确保为数字类型
num_cols = [
    "total_commits", "total_additions", "total_deletions",
    "net_lines", "total_changes", "followers", "following",
    "public_repos", "avg_changes_per_commit", "contributions_on_default_branch",
]
for col in num_cols:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

st.markdown("---")

# ══════════════════════════════════════════════════════════
# Tab 布局
# ══════════════════════════════════════════════════════════
tab1, tab2 = st.tabs(["👤 开发者画像", "🌐 多维分析"])


# ──────────────────────────────────────────────────────────
# TAB 1：开发者画像
# ──────────────────────────────────────────────────────────
with tab1:
    left, right = st.columns([1, 2], gap="large")

    with left:
        st.subheader("贡献者列表")

        # 搜索 + 筛选
        search = st.text_input("🔍 搜索用户名 / 姓名", placeholder="输入关键词...")
        companies = ["全部"] + sorted(df["company"].dropna().unique().tolist())
        filter_company = st.selectbox("筛选公司", companies)

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

        # 贡献者选择列表
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        def _label(r):
            rank = int(r["rank"])
            prefix = medals.get(rank, f"#{rank}")
            name_part = f"  ({r['name']})" if r.get("name") else ""
            return f"{prefix}  {r['login']}{name_part}"

        options = [_label(r) for _, r in filtered.iterrows()]

        if not options:
            st.info("无匹配结果")
            st.stop()

        selected_idx = st.radio(
            "点击查看详情",
            range(len(options)),
            format_func=lambda i: options[i],
            label_visibility="collapsed",
        )

    with right:
        person = filtered.iloc[selected_idx]
        st.subheader("开发者详情")

        # 头像 + 基础信息
        avatar_col, info_col = st.columns([1, 3])
        with avatar_col:
            if person.get("avatar_url"):
                st.image(person["avatar_url"], width=100)
        with info_col:
            display_name = person.get("name") or person["login"]
            st.markdown(f"### {display_name}")
            st.markdown(f"**@{person['login']}**  ·  排名 #{int(person['rank'])}")
            if person.get("bio"):
                st.caption(person["bio"])

        st.markdown("---")

        # 个人信息卡片
        detail_cols = st.columns(2)
        fields = [
            ("🏢 公司", "company"),
            ("📍 地区", "location"),
            ("📧 邮箱", "email"),
            ("🌐 主页", "blog"),
            ("🐦 Twitter", "twitter_username"),
            ("💼 开放求职", "hireable"),
        ]
        for i, (label, key) in enumerate(fields):
            val = person.get(key)
            if val and str(val) not in ("None", "0", ""):
                display_val = "是" if key == "hireable" and val else str(val)
                detail_cols[i % 2].markdown(f"**{label}**  \n{display_val}")

        if person.get("profile_url"):
            st.markdown(f"[🔗 GitHub 主页]({person['profile_url']})")

        st.markdown("---")

        # 贡献数据指标
        st.markdown("#### 代码贡献")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Commits", f"{int(person.get('total_commits', 0)):,}")
        m2.metric("新增行", f"{int(person.get('total_additions', 0)):,}")
        m3.metric("删除行", f"{int(person.get('total_deletions', 0)):,}")
        m4.metric("净增行", f"{int(person.get('net_lines', 0)):,}")

        m5, m6, m7, _ = st.columns(4)
        m5.metric("Followers", f"{int(person.get('followers', 0)):,}")
        m6.metric("公开 Repos", f"{int(person.get('public_repos', 0)):,}")
        m7.metric("默认分支贡献", f"{int(person.get('contributions_on_default_branch', 0)):,}")

        # 贡献指标柱状图
        st.markdown("#### 贡献指标对比（与 Top 10 平均值）")
        top10 = df.nsmallest(10, "rank")
        compare_data = {
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
        }
        fig_bar = px.bar(
            pd.DataFrame(compare_data).melt(id_vars="指标", var_name="类别", value_name="值"),
            x="指标", y="值", color="类别", barmode="group",
            color_discrete_map={"本人": "#4f8bff", "Top10均值": "#aaaaaa"},
            height=300,
        )
        fig_bar.update_layout(margin=dict(t=10, b=10), legend_title_text="")
        st.plotly_chart(fig_bar, use_container_width=True)


# ──────────────────────────────────────────────────────────
# TAB 2：多维分析
# ──────────────────────────────────────────────────────────
with tab2:

    # 顶部总览指标
    o1, o2, o3, o4, o5 = st.columns(5)
    o1.metric("贡献者总数", f"{len(df):,}")
    o2.metric("总 Commits", f"{int(df['total_commits'].sum()):,}")
    o3.metric("总新增行", f"{int(df['total_additions'].sum()):,}")
    o4.metric("总删除行", f"{int(df['total_deletions'].sum()):,}")
    o5.metric("有地区信息", f"{df['location'].notna().sum()} 人")

    st.markdown("---")

    # ── Row 1: 公司分析 + Commit 分布 ─────────────────────
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 🏢 公司 / 机构贡献分析")
        company_df = df[df["company"].notna() & (df["company"] != "")].copy()
        company_df["company"] = company_df["company"].str.strip().str.lstrip("@")

        if not company_df.empty:
            company_counts = (
                company_df.groupby("company")
                .agg(人数=("login", "count"), Commits=("total_commits", "sum"))
                .sort_values("人数", ascending=False)
                .head(15)
                .reset_index()
            )
            fig_company = px.bar(
                company_counts,
                x="人数", y="company", orientation="h",
                color="Commits", color_continuous_scale="Blues",
                labels={"company": "", "人数": "贡献者人数"},
                height=420,
            )
            fig_company.update_layout(
                yaxis=dict(autorange="reversed"),
                coloraxis_colorbar=dict(title="Commits"),
                margin=dict(t=10),
            )
            st.plotly_chart(fig_company, use_container_width=True)
        else:
            st.info("无公司信息数据")

    with col2:
        st.markdown("#### 📊 Commits 分布直方图")
        commit_df = df[df["total_commits"] > 0]
        fig_hist = px.histogram(
            commit_df, x="total_commits", nbins=40,
            labels={"total_commits": "Commit 数量", "count": "人数"},
            color_discrete_sequence=["#4f8bff"],
            height=420,
        )
        fig_hist.update_layout(
            bargap=0.05, margin=dict(t=10),
            yaxis_title="贡献者人数",
        )
        st.plotly_chart(fig_hist, use_container_width=True)

    # ── Row 2: 地区分布 + Followers 散点 ─────────────────
    col3, col4 = st.columns(2)

    with col3:
        st.markdown("#### 🌍 地区分布（Top 15）")
        loc_df = df[df["location"].notna() & (df["location"] != "")].copy()
        if not loc_df.empty:
            # 简单提取城市/国家（取最后一个逗号分隔词）
            loc_df["region"] = loc_df["location"].str.strip().str.split(",").str[-1].str.strip()
            region_counts = (
                loc_df.groupby("region")
                .agg(人数=("login", "count"))
                .sort_values("人数", ascending=False)
                .head(15)
                .reset_index()
            )
            fig_loc = px.bar(
                region_counts,
                x="人数", y="region", orientation="h",
                color="人数", color_continuous_scale="Greens",
                labels={"region": "", "人数": "贡献者人数"},
                height=420,
            )
            fig_loc.update_layout(
                yaxis=dict(autorange="reversed"),
                showlegend=False,
                coloraxis_showscale=False,
                margin=dict(t=10),
            )
            st.plotly_chart(fig_loc, use_container_width=True)
        else:
            st.info("无地区信息数据")

    with col4:
        st.markdown("#### 👥 Followers vs Commits 散点")
        scatter_df = df[(df["followers"] > 0) & (df["total_commits"] > 0)].copy()
        if not scatter_df.empty:
            fig_scatter = px.scatter(
                scatter_df,
                x="total_commits", y="followers",
                hover_name="login",
                hover_data={"name": True, "company": True, "location": True},
                color="total_changes",
                color_continuous_scale="Viridis",
                size="total_changes",
                size_max=30,
                labels={
                    "total_commits": "Commit 数量",
                    "followers": "Followers",
                    "total_changes": "总变更行",
                },
                height=420,
            )
            fig_scatter.update_layout(
                margin=dict(t=10),
                coloraxis_colorbar=dict(title="总变更行"),
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
        else:
            st.info("数据不足")

    # ── Row 3: 求职状态 + 新增/删除比 ─────────────────────
    col5, col6 = st.columns(2)

    with col5:
        st.markdown("#### 💼 开放求职状态")
        hireable_counts = df["hireable"].map(
            lambda x: "开放求职 ✅" if x == 1 else "未开放 / 未知"
        ).value_counts().reset_index()
        hireable_counts.columns = ["状态", "人数"]
        fig_hire = px.pie(
            hireable_counts, names="状态", values="人数",
            color_discrete_sequence=["#2ecc71", "#bdc3c7"],
            height=300,
        )
        fig_hire.update_layout(margin=dict(t=10))
        st.plotly_chart(fig_hire, use_container_width=True)

    with col6:
        st.markdown("#### 📈 Top 20 贡献者新增 vs 删除行对比")
        top20 = df.nsmallest(20, "rank")[["login", "total_additions", "total_deletions"]].copy()
        fig_stacked = go.Figure()
        fig_stacked.add_trace(go.Bar(
            name="新增行", x=top20["login"], y=top20["total_additions"],
            marker_color="#2ecc71",
        ))
        fig_stacked.add_trace(go.Bar(
            name="删除行", x=top20["login"], y=top20["total_deletions"],
            marker_color="#e74c3c",
        ))
        fig_stacked.update_layout(
            barmode="group",
            xaxis_tickangle=-40,
            height=300,
            margin=dict(t=10),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_stacked, use_container_width=True)
