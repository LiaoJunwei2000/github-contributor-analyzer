import streamlit as st
import pandas as pd
from db import init_db, list_repos, get_contributors

st.set_page_config(page_title="跨仓库搜索", page_icon="🔎", layout="wide")
init_db()

st.title("🔎 跨仓库贡献者搜索")
st.caption("从多个仓库中按公司或地区筛选贡献者。")

st.markdown("---")

# ── 仓库选择 ──────────────────────────────────────────────
repos = list_repos()
if not repos:
    st.warning("数据库为空，请先在「数据采集」页面爬取至少一个仓库。")
    st.stop()

all_repo_names = [r["full_name"] for r in repos]

selected_repos = st.multiselect(
    "选择仓库（可多选，不选则搜索全部）",
    all_repo_names,
    placeholder="选择要搜索的仓库...",
    key="cs_repos",
)
target_repos = selected_repos if selected_repos else all_repo_names

# ── 加载数据 ──────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def _load_combined(repo_list: tuple) -> pd.DataFrame:
    frames = []
    for rname in repo_list:
        rows = get_contributors(rname)
        if rows:
            df = pd.DataFrame(rows)
            df["_repo"] = rname
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    for col in ("company", "location"):
        if col in combined.columns:
            combined[col] = combined[col].fillna("").str.strip()
    return combined

with st.spinner("加载数据..."):
    df_all = _load_combined(tuple(target_repos))

if df_all.empty:
    st.info("所选仓库暂无贡献者数据。")
    st.stop()

# ── 清洗可选项 ─────────────────────────────────────────────
def _clean_options(series: pd.Series) -> list[str]:
    return sorted(
        {v.lstrip("@").strip() for v in series if v and v.strip()},
        key=str.lower,
    )

company_options = _clean_options(df_all["company"]) if "company" in df_all.columns else []
location_options = _clean_options(df_all["location"]) if "location" in df_all.columns else []

# ── 搜索条件 ──────────────────────────────────────────────
st.markdown("**筛选条件**（两项均可多选，同时填写时取交集）")
fc1, fc2 = st.columns(2)
with fc1:
    sel_companies = st.multiselect(
        "🏢 公司",
        company_options,
        placeholder="从下拉中选择公司...",
        key="cs_company",
    )
with fc2:
    sel_locations = st.multiselect(
        "📍 地区",
        location_options,
        placeholder="从下拉中选择地区...",
        key="cs_location",
    )

# ── 过滤逻辑 ──────────────────────────────────────────────
df = df_all.copy()

if sel_companies:
    cleaned = df["company"].str.lstrip("@").str.strip()
    df = df[cleaned.isin(sel_companies)]

if sel_locations:
    df = df[df["location"].isin(sel_locations)]

# ── 结果展示 ──────────────────────────────────────────────
st.markdown("---")

n_repos_hit = df["_repo"].nunique() if not df.empty else 0
st.subheader(f"结果：{len(df)} 位贡献者，来自 {n_repos_hit} 个仓库")

if df.empty:
    st.info("没有符合条件的贡献者，请调整搜索条件。")
    st.stop()

display_cols_ordered = [
    "_repo", "rank", "login", "name", "company", "location",
    "total_commits", "total_additions", "total_deletions",
    "followers", "email", "blog", "profile_url",
]
display_cols = [c for c in display_cols_ordered if c in df.columns]

df_show = df[display_cols].copy()
if "total_commits" in df_show.columns:
    df_show = df_show.sort_values(["_repo", "rank"], ascending=True)

st.dataframe(
    df_show,
    use_container_width=True,
    hide_index=True,
    height=600,
    column_config={
        "_repo":            st.column_config.TextColumn("仓库"),
        "rank":             st.column_config.NumberColumn("排名", width="small"),
        "login":            st.column_config.TextColumn("用户名"),
        "name":             st.column_config.TextColumn("姓名"),
        "company":          st.column_config.TextColumn("公司"),
        "location":         st.column_config.TextColumn("地区"),
        "total_commits":    st.column_config.NumberColumn("Commits", format="%d"),
        "total_additions":  st.column_config.NumberColumn("新增行", format="%d"),
        "total_deletions":  st.column_config.NumberColumn("删除行", format="%d"),
        "followers":        st.column_config.NumberColumn("Followers", format="%d"),
        "email":            st.column_config.TextColumn("邮箱"),
        "blog":             st.column_config.LinkColumn("主页"),
        "profile_url":      st.column_config.LinkColumn("GitHub"),
    },
)

# ── 导出 ──────────────────────────────────────────────────
csv = df_show.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
st.download_button(
    "⬇️ 导出结果 CSV",
    data=csv,
    file_name="cross_search_results.csv",
    mime="text/csv",
)
