"""洞察报告生成器：按地区分组人才，生成企业汇报级人才洞察 PPT（16:9）。"""

import functools
import math
import os
import traceback
import urllib.request
import json as _json

import pandas as pd
import streamlit as st

from db import (
    list_repos, get_contributors,
    list_tags, get_repos_by_tags, get_all_repo_tags,
    get_all_location_cache, upsert_location_regions,
)
from insight_llm import (
    generate_talent_profiles, generate_overview,
    OPENROUTER_MODELS, DEFAULT_MODEL,
    ALL_REGION_GROUPS, static_classify_location, classify_locations,
)
from insight_ppt import build_insight_ppt, THEMES


# ════════════════════════════════════════════════════════════
# OpenRouter 模型列表获取 & 费用预估
# ════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_openrouter_models() -> list:
    """从 OpenRouter API 获取全量模型列表（含定价 + 最大输出 Token）。缓存 1 小时。"""
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = _json.loads(r.read())
        result = []
        for m in raw.get("data", []):
            arch    = m.get("architecture") or {}
            out_mod = arch.get("output_modalities") or []
            if "text" not in out_mod:
                continue
            p       = m.get("pricing") or {}
            p_in    = float(p.get("prompt",     0) or 0)
            p_out   = float(p.get("completion", 0) or 0)
            ctx     = m.get("context_length") or 0
            max_out = (m.get("top_provider") or {}).get("max_completion_tokens") or 0
            is_free = (p_in == 0 and p_out == 0)
            result.append({
                "id":      m["id"],
                "name":    m.get("name", m["id"]),
                "p_in":    p_in,
                "p_out":   p_out,
                "ctx":     ctx,
                "max_out": max_out,
                "free":    is_free,
            })
        # 付费模型按 p_in 升序，免费模型放后面
        result.sort(key=lambda x: (x["free"], x["p_in"]))
        return result
    except Exception:
        return [
            {"id": m, "name": m, "p_in": 0.0, "p_out": 0.0,
             "ctx": 0, "max_out": 0, "free": False}
            for m in OPENROUTER_MODELS
        ]


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_model_endpoints(model_id: str, api_key_prefix: str = "") -> list:
    """
    获取指定模型的供应商端点列表（uptime、延迟、吞吐量）。缓存 5 分钟。
    api_key_prefix 仅用于区分缓存键，不传实际 key（由调用方处理头部）。
    """
    return []   # 占位，实际由 _fetch_model_endpoints_live 完成


def _fetch_model_endpoints_live(model_id: str, api_key: str = "") -> list:
    """实时获取模型端点数据（带 API Key 可获取延迟 / 吞吐数据）。"""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(
            f"https://openrouter.ai/api/v1/models/{model_id}/endpoints",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = _json.loads(r.read())
        return [
            {
                "provider": ep.get("provider_name", "?"),
                "ctx":      ep.get("context_length") or 0,
                "max_out":  ep.get("max_completion_tokens"),
                "uptime":   ep.get("uptime_last_30m"),
                "latency":  ep.get("latency_last_30m"),
                "tps":      ep.get("throughput_last_30m"),
                "status":   ep.get("status", 0),
            }
            for ep in (raw.get("data") or {}).get("endpoints", [])
        ]
    except Exception:
        return []


def _fmt_model_label(m: dict) -> str:
    """将模型 dict 格式化为 selectbox 显示标签。"""
    if m["free"]:
        price = "🆓 免费"
    else:
        p_in_m  = m["p_in"]  * 1_000_000
        p_out_m = m["p_out"] * 1_000_000
        if p_in_m < 0.01:
            fmt = lambda v: f"${v:.4f}"
        elif p_in_m < 1:
            fmt = lambda v: f"${v:.3f}"
        else:
            fmt = lambda v: f"${v:.2f}"
        price = f"↑{fmt(p_in_m)} / ↓{fmt(p_out_m)} /M token"
    parts = []
    if m.get("ctx", 0) >= 1000:
        parts.append(f"{m['ctx']//1000}K ctx")
    max_out = m.get("max_out") or 0
    if max_out >= 1000:
        parts.append(f"{max_out//1000}K out")
    elif max_out > 0:
        parts.append(f"{max_out} out")
    suffix = f"  [{'  |  '.join(parts)}]" if parts else ""
    return f"{m['name']}  —  {price}{suffix}"


def _safe_int(v) -> int:
    """NaN / None / 空值安全转 int。"""
    try:
        f = float(v)
        return 0 if pd.isna(f) else int(f)
    except (TypeError, ValueError):
        return 0


def _estimate_cost(n_talents: int, n_repos: int,
                   model_info: dict, talents_flat: list = None) -> dict:
    """
    估算生成人才档案 + 总览的 API 调用费用。
    定价单位：$ per token（来自 OpenRouter API）。
    """
    if n_talents == 0:
        return {"total_in": 0, "total_out": 0, "cost_usd": 0.0,
                "batches": 0, "free": model_info.get("free", False)}

    # 数据丰富度系数（基于实际字段长度）
    richness = 1.0
    if talents_flat:
        avg_chars = sum(
            sum(len(str(v)) for v in t.values()
                if v and str(v).strip() not in ("", "None"))
            for t in talents_flat
        ) / max(len(talents_flat), 1)
        richness = min(2.0, max(0.6, avg_chars / 300))

    BATCH = 8
    batches = math.ceil(n_talents / BATCH)

    # ── 人才档案生成（按批次） ──
    # 每批：system(~250) + repos JSON(80/repo) + 8人数据(120/人)
    batch_in    = 250 + n_repos * 80 + BATCH * int(120 * richness)
    profile_in  = batches * batch_in
    profile_out = n_talents * 220      # 每人输出约 220 tokens JSON

    # ── 总览生成（1次调用） ──
    overview_in  = 200 + n_repos * 100 + n_talents * 60
    overview_out = 400 + n_repos * 100

    total_in  = profile_in  + overview_in
    total_out = profile_out + overview_out

    p_in  = model_info.get("p_in",  0.0)
    p_out = model_info.get("p_out", 0.0)
    cost  = total_in * p_in + total_out * p_out

    return {
        "total_in":  total_in,
        "total_out": total_out,
        "cost_usd":  cost,
        "batches":   batches,
        "free":      model_info.get("free", False),
    }

# ── 常量 ──────────────────────────────────────────────────────
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


@st.cache_data(ttl=60, show_spinner=False)
def _load_all_contributors(repo_tuple: tuple) -> pd.DataFrame:
    """加载所有选中仓库的贡献者，返回含 _repo 列的 DataFrame。"""
    dfs = []
    for repo in repo_tuple:
        rows = get_contributors(repo)
        if rows:
            df = pd.DataFrame(rows)
            df["_repo"] = repo
            dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True)
    for col in ("company", "location"):
        if col in combined.columns:
            combined[col] = combined[col].fillna("").str.strip()
    return combined


def _build_regions_talents(logins_by_region: dict, df_all: pd.DataFrame) -> dict:
    """构建 regions_talents: {region: [talent_dict, ...]}"""
    regions_talents: dict = {}
    for region, logins in logins_by_region.items():
        talents = []
        for login in logins:
            rows = df_all[df_all["login"] == login]
            if rows.empty:
                continue
            t = rows.iloc[0].to_dict()
            t["_repos"] = list(dict.fromkeys(
                r for r in rows["_repo"].tolist() if r
            ))
            if "total_commits" in rows.columns:
                t["total_commits"] = rows["total_commits"].fillna(0).astype(float).sum()
            talents.append(t)
        if talents:
            regions_talents[region] = talents
    return regions_talents


def _ensure_templates_dir():
    os.makedirs(_TEMPLATES_DIR, exist_ok=True)


def _list_templates() -> list:
    _ensure_templates_dir()
    return sorted(f for f in os.listdir(_TEMPLATES_DIR) if f.lower().endswith(".pptx"))


def _theme_preview_html(theme_name: str) -> str:
    """生成主题 HTML/CSS 预览（双卡片：封面 + 人才详情页）。"""
    t = THEMES.get(theme_name, THEMES["华为经典"])
    primary  = t["swatch"]
    bg_panel = "rgb({},{},{})".format(*t["bg_panel"])
    text_main = "rgb({},{},{})".format(*t["text_main"])
    text_sub  = "rgb({},{},{})".format(*t["text_sub"])
    bg_card   = "rgb({},{},{})".format(*t["bg_card"])
    border    = "rgb({},{},{})".format(*t["border"])

    sw, sh = 320, 180   # slide preview px

    panel_w = int(sw * 0.42)
    cover_html = f"""
<div style="width:{sw}px;height:{sh}px;position:relative;border:1px solid #ccc;
            display:inline-block;overflow:hidden;background:#fff;font-family:Arial,sans-serif;">
  <div style="position:absolute;left:0;top:0;width:{panel_w}px;height:{sh}px;background:{bg_panel};">
    <div style="position:absolute;left:0;top:0;width:{panel_w}px;height:8px;background:{primary};"></div>
    <div style="position:absolute;left:0;bottom:0;width:{panel_w}px;height:8px;background:{primary};"></div>
    <div style="position:absolute;left:12px;top:56px;color:rgba(255,200,200,0.9);font-size:9px;">
      开源人才洞察报告
    </div>
    <div style="position:absolute;left:12px;top:74px;color:#fff;font-size:13px;font-weight:bold;line-height:1.4;">
      项目名称<br>贡献者分析
    </div>
    <div style="position:absolute;left:12px;top:126px;color:rgba(255,255,255,0.5);font-size:8px;">
      2024 / 01 / 01
    </div>
  </div>
  <div style="position:absolute;left:{panel_w+10}px;top:14px;color:{text_main};font-size:10px;font-weight:bold;">
    本报告摘要
  </div>
  <div style="position:absolute;left:{panel_w+10}px;top:32px;width:{sw - panel_w - 18}px;">
    <div style="background:{bg_card};border-radius:3px;padding:4px 6px;margin-bottom:5px;
                border-left:3px solid {primary};">
      <div style="color:{text_main};font-size:8px;font-weight:bold;">贡献者总数</div>
      <div style="color:{primary};font-size:15px;font-weight:bold;">128</div>
    </div>
    <div style="background:{bg_card};border-radius:3px;padding:4px 6px;margin-bottom:5px;
                border-left:3px solid {primary};">
      <div style="color:{text_main};font-size:8px;font-weight:bold;">覆盖地区</div>
      <div style="color:{primary};font-size:15px;font-weight:bold;">4</div>
    </div>
    <div style="background:{bg_card};border-radius:3px;padding:4px 6px;
                border-left:3px solid {primary};">
      <div style="color:{text_main};font-size:8px;font-weight:bold;">仓库数</div>
      <div style="color:{primary};font-size:15px;font-weight:bold;">3</div>
    </div>
  </div>
</div>"""

    content_html = f"""
<div style="width:{sw}px;height:{sh}px;position:relative;border:1px solid #ccc;
            display:inline-block;overflow:hidden;background:#fff;
            font-family:Arial,sans-serif;margin-left:14px;">
  <div style="position:absolute;left:0;top:0;width:{sw}px;height:40px;background:#fff;
              border-bottom:1px solid {border};">
    <div style="position:absolute;left:0;top:0;width:5px;height:40px;background:{primary};"></div>
    <div style="position:absolute;left:12px;top:6px;color:{text_main};font-size:11px;font-weight:bold;">
      @contributor_name
    </div>
    <div style="position:absolute;left:12px;top:23px;color:{text_sub};font-size:8px;">
      Full Name · Example Corp · 📍 Hong Kong
    </div>
  </div>
  <div style="position:absolute;left:6px;top:46px;width:{int(sw*0.37)}px;
              height:{sh - 54}px;background:{bg_card};border-radius:3px;padding:5px;">
    <div style="width:38px;height:38px;border-radius:50%;background:{border};
                margin:0 auto 5px;"></div>
    <div style="color:{primary};font-size:7.5px;text-align:center;font-weight:bold;">
      全栈工程师
    </div>
    <div style="color:{text_sub};font-size:7px;text-align:center;margin-top:2px;">
      开源贡献者 · Commits 高
    </div>
  </div>
  <div style="position:absolute;right:5px;top:46px;width:{int(sw*0.58)}px;">
    <div style="background:{bg_card};border-radius:2px;padding:3px 5px;margin-bottom:3px;">
      <div style="color:{text_sub};font-size:7px;">项目贡献排名</div>
      <div style="height:5px;background:{border};border-radius:2px;margin-top:2px;">
        <div style="width:80%;height:100%;background:{primary};border-radius:2px;"></div>
      </div>
    </div>
    <div style="background:{bg_card};border-radius:2px;padding:3px 5px;">
      <div style="color:{text_sub};font-size:7px;">技术亮点</div>
      <div style="color:{text_main};font-size:7px;margin-top:2px;line-height:1.5;">
        · 核心贡献者，排名前 5%<br>
        · 主导多个关键模块开发
      </div>
    </div>
  </div>
  <div style="position:absolute;left:0;bottom:0;width:{sw}px;height:13px;
              background:{bg_card};border-top:2px solid {primary};">
    <div style="position:absolute;right:8px;top:1px;color:{text_sub};font-size:7px;">P.1 / 20</div>
  </div>
</div>"""

    return f"""
<div style="margin:8px 0 4px;">
  <div style="margin-bottom:8px;color:#666;font-size:12px;">
    <b>{theme_name}</b> 主题预览：左为封面页，右为人才详情页
  </div>
  <div style="white-space:nowrap;overflow-x:auto;">{cover_html}{content_html}</div>
</div>"""


# ════════════════════════════════════════════════════════════
# Streamlit UI
# ════════════════════════════════════════════════════════════

st.title("🔬 洞察报告生成器")
st.caption("按地区分组人才，生成企业汇报级人才洞察 PPT（16:9）。")

# ── OpenRouter API Key（置顶）────────────────────────────────
with st.container(border=True):
    st.markdown(
        "🔑 **OpenRouter API Key**　｜　"
        "该界面内所有涉及到 AI 的功能（大模型分析、地区分类等）均需要填入 API Key，"
        "不填写可跳过 AI 增强步骤，但报告内容将不含智能分析。"
    )
    _key_from_secrets = ""
    try:
        _key_from_secrets = st.secrets.get("OPENROUTER_API_KEY", "")
    except Exception:
        pass
    if _key_from_secrets:
        api_key = _key_from_secrets
        st.caption("✅ 已从 Secrets 加载 API Key。")
    else:
        api_key = st.text_input(
            "OpenRouter API Key",
            type="password",
            key="ir_api_key",
            placeholder="sk-or-v1-...",
            label_visibility="collapsed",
        )

repos = list_repos()
if not repos:
    st.warning("数据库为空，请先在「数据采集」页面爬取至少一个仓库。")
    st.stop()

all_repo_names = [r["full_name"] for r in repos]
_all_tags      = list_tags()
_repo_tag_map  = get_all_repo_tags()   # {repo: [{"id", "name", "color"}, ...]}

# ── 初始化 session state ──────────────────────────────────
if "ir_repos" not in st.session_state:
    st.session_state["ir_repos"] = []

# ════════════════════════════════════════════════════════════
# Step 1：选择仓库（双栏，与 PPT 生成器一致）
# ════════════════════════════════════════════════════════════

st.subheader("① 选择仓库")

col_repo_l, col_repo_r = st.columns(2)

with col_repo_l:
    st.markdown("**全部仓库**")

    # 标签筛选（有标签时显示）
    if _all_tags:
        tag_filter = st.multiselect(
            "按标签筛选", [t["name"] for t in _all_tags],
            key="ir_tag_filter", placeholder="选择标签（不选=显示全部）",
            label_visibility="collapsed",
        )
        if tag_filter:
            _filter_tag_ids = [t["id"] for t in _all_tags if t["name"] in tag_filter]
            _tagged_repos   = set(get_repos_by_tags(_filter_tag_ids))
            _base_repos     = [r for r in all_repo_names if r in _tagged_repos]
        else:
            _base_repos = all_repo_names
    else:
        _base_repos = all_repo_names

    repo_search = st.text_input(
        "搜索仓库", placeholder="输入关键词筛选...",
        key="ir_repo_search", label_visibility="collapsed",
    )
    kw = repo_search.strip().lower()
    visible_repos = [r for r in _base_repos if kw in r.lower()] if kw else _base_repos

    def _select_all_visible(vlist=None):
        for r in (vlist or []):
            st.session_state[f"ir_cb_{r}"] = True
            if r not in st.session_state["ir_repos"]:
                st.session_state["ir_repos"].append(r)

    def _deselect_all_visible(vlist=None):
        for r in (vlist or []):
            st.session_state[f"ir_cb_{r}"] = False
            if r in st.session_state["ir_repos"]:
                st.session_state["ir_repos"].remove(r)

    sa_col, da_col = st.columns(2)
    with sa_col:
        st.button(
            "全选" if not kw else f"全选结果（{len(visible_repos)}）",
            key="ir_select_all",
            use_container_width=True,
            on_click=functools.partial(_select_all_visible, visible_repos),
        )
    with da_col:
        st.button(
            "取消全选",
            key="ir_deselect_all",
            use_container_width=True,
            on_click=functools.partial(_deselect_all_visible, visible_repos),
        )

    def _tag_badges_html(repo_name: str, font_size: str = "0.75rem") -> str:
        tags = _repo_tag_map.get(repo_name, [])
        if not tags:
            return ""
        return " ".join(
            f"<span style='background:{t['color']};color:#fff;border-radius:3px;"
            f"padding:1px 6px;font-size:{font_size};display:inline-block;margin:1px'>"
            f"{t['name']}</span>"
            for t in tags
        )

    for repo in visible_repos:
        checked = st.checkbox(f"📦 {repo}", key=f"ir_cb_{repo}")
        if checked and repo not in st.session_state["ir_repos"]:
            st.session_state["ir_repos"].append(repo)
        elif not checked and repo in st.session_state["ir_repos"]:
            st.session_state["ir_repos"].remove(repo)
        badges_html = _tag_badges_html(repo)
        if badges_html:
            st.markdown(
                f"<div style='margin:-8px 0 4px 26px'>{badges_html}</div>",
                unsafe_allow_html=True,
            )

with col_repo_r:
    st.markdown("**已选仓库**")
    if not st.session_state["ir_repos"]:
        st.caption("← 从左侧勾选仓库")
    else:
        for repo in list(st.session_state["ir_repos"]):
            rc1, rc2 = st.columns([6, 1])
            with rc1:
                st.markdown(f"📦 `{repo}`")
                badges_html = _tag_badges_html(repo)
                if badges_html:
                    st.markdown(badges_html, unsafe_allow_html=True)
            with rc2:
                if st.button("×", key=f"ir_rm_repo_{repo}", help="移除该仓库"):
                    st.session_state["ir_repos"].remove(repo)
                    st.session_state[f"ir_cb_{repo}"] = False
                    st.rerun()

if not st.session_state["ir_repos"]:
    st.info("请先选择至少一个仓库。")
    st.stop()

# ════════════════════════════════════════════════════════════
# Step 2：筛选贡献者
# ════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("② 筛选贡献者")

df_all = _load_all_contributors(tuple(st.session_state["ir_repos"]))
if df_all.empty:
    st.warning("所选仓库暂无贡献者数据。")
    st.stop()

# 公司 / 地区 dropdown
def _clean_opts(series: pd.Series) -> list:
    return sorted(
        {v.lstrip("@").strip() for v in series if v and v.strip()},
        key=str.lower,
    )

company_opts  = _clean_opts(df_all["company"])  if "company"  in df_all.columns else []
location_opts = _clean_opts(df_all["location"]) if "location" in df_all.columns else []

fc1, fc2 = st.columns(2)
with fc1:
    sel_cos = st.multiselect(
        "🏢 公司", company_opts,
        placeholder="选择公司（可多选，不选=全部）…",
        key="ir_cs_co",
    )
with fc2:
    sel_locs = st.multiselect(
        "📍 地区", location_opts,
        placeholder="选择地区（可多选，不选=全部）…",
        key="ir_cs_loc",
    )

df_filtered = df_all.copy()
if sel_cos and "company" in df_filtered.columns:
    df_filtered = df_filtered[
        df_filtered["company"].str.lstrip("@").str.strip().isin(sel_cos)
    ]
if sel_locs and "location" in df_filtered.columns:
    df_filtered = df_filtered[df_filtered["location"].isin(sel_locs)]
df_filtered = df_filtered.sort_values(["_repo", "rank"]).reset_index(drop=True)

n_repos_hit = df_filtered["_repo"].nunique()
st.markdown(f"**结果：{df_filtered['login'].nunique()} 位贡献者，来自 {n_repos_hit} 个仓库**")

_display_cols = [c for c in [
    "_repo", "rank", "login", "name", "company", "location",
    "total_commits", "total_additions", "total_deletions",
    "followers", "email", "blog", "profile_url",
] if c in df_filtered.columns]

st.dataframe(
    df_filtered[_display_cols],
    use_container_width=True,
    hide_index=True,
    height=380,
    column_config={
        "_repo":           st.column_config.TextColumn("仓库"),
        "rank":            st.column_config.NumberColumn("排名", width="small"),
        "login":           st.column_config.TextColumn("用户名"),
        "name":            st.column_config.TextColumn("姓名"),
        "company":         st.column_config.TextColumn("公司"),
        "location":        st.column_config.TextColumn("地区"),
        "total_commits":   st.column_config.NumberColumn("Commits",  format="%d"),
        "total_additions": st.column_config.NumberColumn("新增行",   format="%d"),
        "total_deletions": st.column_config.NumberColumn("删除行",   format="%d"),
        "followers":       st.column_config.NumberColumn("Followers", format="%d"),
        "email":           st.column_config.TextColumn("邮箱"),
        "blog":            st.column_config.LinkColumn("主页"),
        "profile_url":     st.column_config.LinkColumn("GitHub"),
    },
)

if df_filtered.empty:
    st.info("没有符合条件的贡献者，请调整筛选条件。")
    st.stop()

# ════════════════════════════════════════════════════════════
# Step 3：地区分组
# ════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("③ 地区分组")

# 按唯一 login 去重（基于 df_filtered）
df_unique = df_filtered.drop_duplicates(subset=["login"])

# 初始化 region_map（login → list[str]）
if "ir_region_map" not in st.session_state:
    st.session_state["ir_region_map"] = {}

# 加载 DB 缓存快照（一次查询）
_loc_db_cache: dict = get_all_location_cache()

# 分类：静态 → DB缓存 → 标记待 AI
_needs_ai_locs: list = []  # raw_location 字符串（需要 AI 分类）
for _, row in df_unique.iterrows():
    login = str(row.get("login") or "")
    if not login or login in st.session_state["ir_region_map"]:
        continue
    loc = str(row.get("location") or "")
    static_result = static_classify_location(loc)
    if static_result is not None:
        # 静态命中
        st.session_state["ir_region_map"][login] = static_result
        if loc and loc not in _loc_db_cache:
            upsert_location_regions(loc, static_result)
    elif loc in _loc_db_cache:
        # DB 缓存命中
        st.session_state["ir_region_map"][login] = _loc_db_cache[loc]
    else:
        # 需要 AI
        st.session_state["ir_region_map"][login] = ["未分类"]
        if loc and loc not in _needs_ai_locs:
            _needs_ai_locs.append(loc)

# 当前活跃 logins
active_logins = set(df_unique["login"].tolist())
ir_region_map_active: dict = {
    k: v for k, v in st.session_state["ir_region_map"].items()
    if k in active_logins
}

# ── AI 分类按钮 ──────────────────────────────────────────────
_unclassified_locs = list({
    str(df_unique[df_unique["login"] == lg]["location"].iloc[0] or "")
    for lg, regions in ir_region_map_active.items()
    if regions == ["未分类"]
    if not df_unique[df_unique["login"] == lg].empty
})
_all_unknown = list(dict.fromkeys(_needs_ai_locs + [
    l for l in _unclassified_locs if l not in _needs_ai_locs
]))
_all_unknown = [l for l in _all_unknown if l and l.strip() not in ("", "None")]

_ai_model = st.session_state.get("ir_model", DEFAULT_MODEL)

if _all_unknown:
    _col_ai1, _col_ai2, _col_ai3 = st.columns([5, 2, 2])
    with _col_ai1:
        st.caption(
            f"⚠️ **{len(_all_unknown)}** 个地点无法静态识别"
            + ("，可点击右侧按钮用 AI 智能分类。" if api_key else "。请在上方填写 OpenRouter API Key 后解锁 AI 分类。")
        )
    with _col_ai2:
        _ai_btn = st.button(
            "✨ AI 分类地区",
            disabled=not api_key,
            use_container_width=True,
        )
    with _col_ai3:
        _reset_btn = st.button("🔄 重置分类", use_container_width=True,
                               help="清除本次会话分类，重新从数据库/静态规则加载")
    if _reset_btn:
        for lg in list(active_logins):
            st.session_state["ir_region_map"].pop(lg, None)
        st.session_state.pop("ir_table_df", None)
        st.rerun()
    if _ai_btn and api_key:
        with st.spinner(f"AI 正在分类 {len(_all_unknown)} 个地点…"):
            _ai_result = classify_locations(_all_unknown, api_key, _ai_model)
        for loc, regions in _ai_result.items():
            upsert_location_regions(loc, regions)
        for _, row in df_unique.iterrows():
            login = str(row.get("login") or "")
            loc   = str(row.get("location") or "")
            if loc in _ai_result:
                st.session_state["ir_region_map"][login] = _ai_result[loc]
        st.session_state.pop("ir_table_df", None)
        st.rerun()
else:
    if st.button("🔄 重置分类", help="清除本次会话分类，重新从数据库/静态规则加载"):
        for lg in list(active_logins):
            st.session_state["ir_region_map"].pop(lg, None)
        st.session_state.pop("ir_table_df", None)
        st.rerun()

all_regions_with_default = ALL_REGION_GROUPS + ["未分类"]

# ── 构建/同步 表格数据 ───────────────────────────────────────
# 当活跃 logins 集合变化时，强制重建（换了仓库或筛选条件）
_active_hash = frozenset(active_logins)
if st.session_state.get("_ir_logins_hash") != _active_hash:
    st.session_state.pop("ir_table_df", None)
    st.session_state["_ir_logins_hash"] = _active_hash

_fresh_rows = []
for _, row in df_unique.iterrows():
    login = str(row.get("login") or "")
    if not login or login not in ir_region_map_active:
        continue
    regions = ir_region_map_active.get(login, ["未分类"])
    primary = regions[0] if regions else "未分类"
    _fresh_rows.append({
        "selected":  True,
        "login":     login,
        "name":      str(row.get("name") or ""),
        "location":  str(row.get("location") or ""),
        "region":    primary,
        "commits":   _safe_int(row.get("total_commits")),
        "followers": _safe_int(row.get("followers")),
    })

_fresh_df = (
    pd.DataFrame(_fresh_rows)
    .sort_values(["region", "commits"], ascending=[True, False])
    .reset_index(drop=True)
    if _fresh_rows else
    pd.DataFrame(columns=["selected","login","name","location","region","commits","followers"])
)

if "ir_table_df" not in st.session_state:
    st.session_state["ir_table_df"] = _fresh_df
else:
    # 新增人员用默认值；已有人员保留用户的选择和地区改动
    _existing = st.session_state["ir_table_df"].set_index("login")
    for i, r in _fresh_df.iterrows():
        lg = r["login"]
        if lg in _existing.index:
            _fresh_df.at[i, "selected"] = bool(_existing.at[lg, "selected"])
            ex_reg = _existing.at[lg, "region"]
            if ex_reg in all_regions_with_default:
                _fresh_df.at[i, "region"] = ex_reg
    st.session_state["ir_table_df"] = _fresh_df

# ── 地区筛选（先于汇总，因为影响 selected 状态）────────────────
_tbl = st.session_state["ir_table_df"]
_region_counts = _tbl.groupby("region").size().to_dict()
_present = [r for r in all_regions_with_default if r in _region_counts]

_f1, _f2, _f3 = st.columns([4, 1, 1])
with _f1:
    _region_filter = st.multiselect(
        "按地区选择",
        options=_present,
        default=[],
        placeholder="选择地区 → 仅对应人员入选（不选=手动勾选）",
        label_visibility="collapsed",
        key="ir_region_filter",
    )

# 筛选变化时：自动更新 selected 状态
_prev_filter = st.session_state.get("_ir_prev_filter", [])
if sorted(_region_filter) != sorted(_prev_filter):
    st.session_state["_ir_prev_filter"] = list(_region_filter)
    if _region_filter:
        # 仅选中筛选地区的人，其余全部取消
        _mask = st.session_state["ir_table_df"]["region"].isin(_region_filter)
        st.session_state["ir_table_df"].loc[ _mask, "selected"] = True
        st.session_state["ir_table_df"].loc[~_mask, "selected"] = False
    else:
        # 清空筛选 → 恢复全选
        st.session_state["ir_table_df"]["selected"] = True
    st.rerun()

with _f2:
    if st.button("全选", use_container_width=True, key="ir_tbl_all"):
        if _region_filter:
            _mask = st.session_state["ir_table_df"]["region"].isin(_region_filter)
            st.session_state["ir_table_df"].loc[_mask, "selected"] = True
        else:
            st.session_state["ir_table_df"]["selected"] = True
        st.rerun()
with _f3:
    if st.button("全不选", use_container_width=True, key="ir_tbl_none"):
        if _region_filter:
            _mask = st.session_state["ir_table_df"]["region"].isin(_region_filter)
            st.session_state["ir_table_df"].loc[_mask, "selected"] = False
        else:
            st.session_state["ir_table_df"]["selected"] = False
        st.rerun()

# ── 地区汇总 ─────────────────────────────────────────────────
_tbl = st.session_state["ir_table_df"]
if _present:
    _mcols = st.columns(min(len(_present), 7))
    for _i, _rg in enumerate(_present):
        _selected_n = int(_tbl[(_tbl["region"] == _rg) & (_tbl["selected"])].shape[0])
        _mcols[_i % len(_mcols)].metric(
            _rg,
            f"{_selected_n} / {_region_counts[_rg]} 人",
        )

# ── 可编辑表格 ───────────────────────────────────────────────
_src  = st.session_state["ir_table_df"]
_show = (
    _src[_src["region"].isin(_region_filter)].copy()
    if _region_filter else _src.copy()
)
_height = min(600, max(200, len(_show) * 35 + 50))

_edited = st.data_editor(
    _show,
    use_container_width=True,
    hide_index=True,
    height=_height,
    column_config={
        "selected":  st.column_config.CheckboxColumn("✓", width="small"),
        "login":     st.column_config.TextColumn("用户名", width="medium"),
        "name":      st.column_config.TextColumn("姓名", width="medium"),
        "location":  st.column_config.TextColumn("位置", width="large"),
        "region":    st.column_config.SelectboxColumn(
                         "地区分组",
                         options=all_regions_with_default,
                         width="medium",
                     ),
        "commits":   st.column_config.NumberColumn("Commits", format="%d", width="small"),
        "followers": st.column_config.NumberColumn("Followers", format="%d", width="small"),
    },
    disabled=["login", "name", "location", "commits", "followers"],
)

# 将编辑结果合并回完整表格
if not _edited.empty:
    _full_idx = st.session_state["ir_table_df"].set_index("login")
    _db_updates: list = []
    for _, erow in _edited.iterrows():
        lg = erow["login"]
        if lg not in _full_idx.index:
            continue
        _full_idx.at[lg, "selected"] = bool(erow["selected"])
        new_reg = erow["region"]
        if new_reg and new_reg != _full_idx.at[lg, "region"]:
            _full_idx.at[lg, "region"] = new_reg
            st.session_state["ir_region_map"][lg] = [new_reg]
            _loc_df = df_unique[df_unique["login"] == lg]
            _loc_str = str(_loc_df["location"].iloc[0] or "") if not _loc_df.empty else ""
            if _loc_str:
                _db_updates.append((_loc_str, [new_reg]))
    st.session_state["ir_table_df"] = _full_idx.reset_index()
    for _loc_str, _regs in _db_updates:
        upsert_location_regions(_loc_str, _regs)

# ── 构建 final_region_map ────────────────────────────────────
final_region_map: dict = {}
for _, row in st.session_state["ir_table_df"].iterrows():
    if row["selected"]:
        region = row["region"]
        login  = row["login"]
        final_region_map.setdefault(region, [])
        if login not in final_region_map[region]:
            final_region_map[region].append(login)

total_selected = int(st.session_state["ir_table_df"]["selected"].sum())
st.caption(
    f"已选中 **{total_selected}** 位人才，"
    f"分布于 **{len(final_region_map)}** 个地区。"
)

if not final_region_map or total_selected == 0:
    st.info("请至少选择一位人才。")
    st.stop()

# 预构建 regions_talents（用于后续 LLM 和 PPT）
regions_talents  = _build_regions_talents(final_region_map, df_all)
all_talents_flat = [t for ts in regions_talents.values() for t in ts]

repos_info: dict = {}
for repo in st.session_state["ir_repos"]:
    repo_meta = next((r for r in repos if r["full_name"] == repo), {})
    repos_info[repo] = {
        "description": repo_meta.get("description") or "",
        "language":    repo_meta.get("language") or "",
        "stars":       repo_meta.get("stars") or 0,
    }

# ════════════════════════════════════════════════════════════
# Step 4：报告配置（主题 + 预览）
# ════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("④ 报告配置")

col_cfg1, col_cfg2 = st.columns(2)
with col_cfg1:
    report_title = st.text_input("报告标题", value="开源人才洞察报告", key="ir_title")

    theme_names = list(THEMES.keys())
    theme_idx = st.radio(
        "配色主题",
        options=range(len(theme_names)),
        format_func=lambda i: theme_names[i],
        horizontal=True,
        key="ir_theme",
    )
    selected_theme = theme_names[theme_idx]

    # 色块预览
    swatch_html = "".join(
        f"<span style='display:inline-block;width:18px;height:18px;"
        f"border-radius:3px;background:{THEMES[name]['swatch']};"
        f"margin-right:6px;vertical-align:middle;"
        f"{'outline:2px solid #555;' if name == selected_theme else ''}'"
        f"title='{name}'></span>"
        for name in theme_names
    )
    st.markdown(swatch_html, unsafe_allow_html=True)

with col_cfg2:
    # ── 模型列表（实时从 OpenRouter 拉取）──────────────────────
    with st.spinner("正在加载模型列表…"):
        _all_models = _fetch_openrouter_models()

    # 搜索过滤
    _model_search = st.text_input(
        "搜索模型",
        placeholder="输入名称/厂商关键词，如 gemini / gpt / claude…",
        key="ir_model_search",
        label_visibility="collapsed",
    )
    _kw = _model_search.strip().lower()
    _filtered = [
        m for m in _all_models
        if not _kw or _kw in m["id"].lower() or _kw in m["name"].lower()
    ] or _all_models   # 无结果时展示全部

    # 构建 id → label 映射（供 format_func）
    _label_map = {m["id"]: _fmt_model_label(m) for m in _filtered}
    _model_ids = [m["id"] for m in _filtered]

    # 确定默认选中（保留上次选择，否则用 DEFAULT_MODEL）
    _prev = st.session_state.get("ir_model", DEFAULT_MODEL)
    _def_idx = _model_ids.index(_prev) if _prev in _model_ids else 0

    selected_model = st.selectbox(
        f"选择模型（共 {len(_filtered)} 个）",
        options=_model_ids,
        index=_def_idx,
        format_func=lambda mid: _label_map.get(mid, mid),
        key="ir_model",
        help="定价单位：$ per million tokens。↑=输入，↓=输出。[ctx]=上下文长度，[out]=最大输出。",
    )

    # 当前选中模型基本信息
    _sel_info = next((m for m in _all_models if m["id"] == selected_model), None)
    if _sel_info:
        if _sel_info["free"]:
            st.caption("🆓 该模型当前免费，适合测试。")
        else:
            _in_m  = _sel_info["p_in"]  * 1_000_000
            _out_m = _sel_info["p_out"] * 1_000_000
            _ctx_k = _sel_info["ctx"] // 1000 if _sel_info["ctx"] >= 1000 else _sel_info["ctx"]
            _out_k = (_sel_info["max_out"] // 1000
                      if (_sel_info.get("max_out") or 0) >= 1000
                      else (_sel_info.get("max_out") or "—"))
            st.caption(
                f"输入 **${_in_m:.4g}** / M　"
                f"输出 **${_out_m:.4g}** / M　"
                f"上下文 **{_ctx_k}K**　"
                f"最大输出 **{_out_k}{'K' if isinstance(_out_k, int) else ''}**"
            )

    # ── 供应商端点状态 ──────────────────────────────────────
    _endpoints = _fetch_model_endpoints_live(selected_model, api_key)
    if _endpoints:
        _has_perf = any(ep["latency"] is not None or ep["tps"] is not None
                        for ep in _endpoints)
        with st.expander(
            f"🌐 供应商状态（{len(_endpoints)} 个）"
            + ("　含实时性能数据" if _has_perf else "　填写 API Key 可获取延迟/速率"),
            expanded=False,
        ):
            def _num(v):
                """从 number 或 dict（取第一个值）安全提取 float。"""
                if v is None:
                    return None
                if isinstance(v, dict):
                    v = next(iter(v.values()), None)
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            for ep in sorted(_endpoints,
                             key=lambda e: (e["status"] != 0,
                                            -((_num(e["uptime"]) or 0)))):

                status_icon = "🟢" if ep["status"] == 0 else "🔴"
                _up  = _num(ep["uptime"])
                _lat = _num(ep["latency"])
                _tps = _num(ep["tps"])
                uptime  = f"{_up:.1f}%"  if _up  is not None else "N/A"
                latency = f"{_lat:.0f} ms" if _lat is not None else "—"
                tps     = f"{_tps:.0f} t/s" if _tps is not None else "—"
                ctx_k   = f"{ep['ctx']//1000}K" if (ep["ctx"] or 0) >= 1000 else "—"
                max_out = (f"{ep['max_out']//1000}K"
                           if (ep["max_out"] or 0) >= 1000
                           else (str(ep["max_out"]) if ep["max_out"] else "—"))
                st.markdown(
                    f"{status_icon} **{ep['provider']}**　"
                    f"可用率 `{uptime}`　"
                    f"延迟 `{latency}`　"
                    f"速率 `{tps}`　"
                    f"上下文 `{ctx_k}`　"
                    f"最大输出 `{max_out}`"
                )

# ── 费用预估（全宽，放在两栏之下）────────────────────────────
_sel_model_info = next(
    (m for m in _fetch_openrouter_models() if m["id"] == selected_model),
    {"id": selected_model, "p_in": 0, "p_out": 0, "free": True},
)
est = _estimate_cost(
    n_talents=total_selected,
    n_repos=len(st.session_state.get("ir_repos", [])),
    model_info=_sel_model_info,
    talents_flat=all_talents_flat,
)

with st.expander("💰 API 费用预估", expanded=True):
    ec1, ec2, ec3, ec4 = st.columns(4)
    ec1.metric("候选人", f"{total_selected} 人")
    ec2.metric("输入 Token", f"~{est['total_in']:,}")
    ec3.metric("输出 Token", f"~{est['total_out']:,}")

    if est["free"]:
        ec4.metric("预估费用", "免费 🆓")
        cost_bar_val = 0.0
        cost_note = "当前所选模型免费，不产生费用。"
        cost_color = "green"
    else:
        cost_usd = est["cost_usd"]
        if cost_usd < 0.001:
            cost_str = f"< $0.001"
        else:
            cost_str = f"≈ ${cost_usd:.4f}"
        ec4.metric("预估费用（USD）", cost_str)

        # 颜色判断
        if cost_usd < 0.01:
            cost_color, cost_note = "green",  f"🟢 经济型  约 ${cost_usd:.4f} USD"
        elif cost_usd < 0.10:
            cost_color, cost_note = "orange", f"🟡 中等    约 ${cost_usd:.4f} USD"
        else:
            cost_color, cost_note = "red",    f"🔴 较高    约 ${cost_usd:.4f} USD，建议换更便宜的模型"

        # 进度条（以 $0.20 为满量程，超过则截断到 1.0）
        cost_bar_val = min(1.0, cost_usd / 0.20)

    st.progress(cost_bar_val)
    st.caption(
        f"{cost_note}　｜　"
        f"{est['batches']} 批次人才档案 + 1 次总览调用　｜　"
        f"共 ~{est['total_in'] + est['total_out']:,} tokens"
    )

    # 仅在费用偏高时给出替换建议
    if not est["free"] and est["cost_usd"] >= 0.05:
        _cheap = [m for m in _fetch_openrouter_models()
                  if not m["free"] and m["p_in"] > 0]
        if _cheap:
            _cheapest = _cheap[0]
            _cheap_est = _estimate_cost(total_selected,
                                        len(st.session_state.get("ir_repos", [])),
                                        _cheapest, all_talents_flat)
            st.info(
                f"💡 切换到 **{_cheapest['name']}**（↑${_cheapest['p_in']*1e6:.4g}/M）"
                f" 可降至约 ${_cheap_est['cost_usd']:.4f} USD。"
            )

# 主题 HTML 预览
with st.expander("🎨 当前主题预览", expanded=False):
    st.markdown(_theme_preview_html(selected_theme), unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════
# Step 5：PPT 模板管理
# ════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("⑤ PPT 模板管理")

_ensure_templates_dir()

tpl_col1, tpl_col2 = st.columns(2)

with tpl_col1:
    st.markdown("**下载样板文件**")
    st.caption(
        "生成包含封面、地区分节、人才详情示例的样板 PPTX。"
        "可在 PowerPoint / WPS 中编辑（调色、添加 Logo 等），完成后上传至右侧。"
    )
    if st.button("📥 生成并下载样板 PPTX", key="ir_gen_sample"):
        with st.spinner("正在生成样板…"):
            try:
                _sample_repo = (list(repos_info.keys())[0]
                                if repos_info else "example/repo")
                sample_buf = build_insight_ppt(
                    regions_talents={
                        "示例地区": [{
                            "login":       "example_user",
                            "name":        "示例用户",
                            "company":     "Example Corp",
                            "location":    "Example City",
                            "avatar_url":  "",
                            "followers":   100,
                            "total_commits": 200,
                            "bio":         "示例简介，展示 PPT 格式与排版。",
                            "_repos":      [_sample_repo],
                        }]
                    },
                    repos_info={
                        _sample_repo: {
                            "description": "示例仓库描述",
                            "language":    "Python",
                            "stars":       1000,
                        }
                    },
                    llm_content={},
                    theme=selected_theme,
                    report_title="【样板】开源人才洞察报告",
                )
                st.download_button(
                    "💾 保存样板文件",
                    data=sample_buf,
                    file_name=f"template_sample_{selected_theme}.pptx",
                    mime=(
                        "application/vnd.openxmlformats-officedocument"
                        ".presentationml.presentation"
                    ),
                    key="ir_save_sample",
                )
            except Exception as e:
                st.error(f"生成失败：{e}")

with tpl_col2:
    st.markdown("**上传自定义模板**")
    st.caption(
        "上传编辑好的 .pptx 文件。生成时将使用该文件的幻灯片母版"
        "（背景色、Logo、字体等）作为底板，在其上绘制内容。"
    )
    uploaded_tpl = st.file_uploader(
        "上传模板 .pptx", type=["pptx"], key="ir_upload_template",
        label_visibility="collapsed",
    )
    if uploaded_tpl:
        tpl_save_path = os.path.join(_TEMPLATES_DIR, uploaded_tpl.name)
        with open(tpl_save_path, "wb") as f:
            f.write(uploaded_tpl.getvalue())
        st.success(f"模板「{uploaded_tpl.name}」已保存至 templates/ 目录。")

# 模板选择 & 删除
available_templates = _list_templates()
selected_template_path = None

if available_templates:
    tpl_options = ["（使用内置主题，不套模板）"] + available_templates
    tpl_choice = st.selectbox(
        "生成时使用的模板",
        options=tpl_options,
        key="ir_tpl_choice",
    )
    if tpl_choice != "（使用内置主题，不套模板）":
        selected_template_path = os.path.join(_TEMPLATES_DIR, tpl_choice)
        st.caption(f"已选模板：`{tpl_choice}`  — 生成时将使用该文件的幻灯片母版。")
        if st.button(f"🗑️ 删除模板「{tpl_choice}」", key="ir_del_tpl"):
            os.remove(selected_template_path)
            st.success(f"已删除「{tpl_choice}」")
            st.rerun()
else:
    st.caption("暂无自定义模板。下载样板文件，编辑后上传即可使用。")

# ════════════════════════════════════════════════════════════
# Step 6：AI 内容生成（可选）
# ════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("⑥ AI 内容生成（可选）")

llm_content: dict = st.session_state.get("ir_llm_content") or {}

if llm_content:
    profiles_count = len(llm_content.get("profiles") or {})
    has_overview   = bool(llm_content.get("overview"))
    st.success(
        f"已缓存 AI 内容：**{profiles_count}** 位人才档案，"
        f"总览：{'✅' if has_overview else '❌'}"
    )
    if st.button("🗑️ 清除 AI 内容", key="ir_clear_llm"):
        st.session_state["ir_llm_content"] = {}
        st.rerun()

col_ai1, col_ai2 = st.columns(2)

with col_ai1:
    gen_profiles_disabled = not bool(api_key)
    if st.button("✨ 生成人才档案", key="ir_gen_profiles",
                 disabled=gen_profiles_disabled):
        prog = st.progress(0, text="正在生成人才档案…")
        def _cb(done, total):
            prog.progress(done / max(total, 1), text=f"已处理 {done}/{total} 位…")
        try:
            profiles = generate_talent_profiles(
                all_talents_flat, repos_info, api_key,
                model=st.session_state.get("ir_model", DEFAULT_MODEL),
                progress_cb=_cb,
            )
            existing = st.session_state.get("ir_llm_content") or {}
            existing["profiles"] = profiles
            st.session_state["ir_llm_content"] = existing
            prog.empty()
            st.success(f"已生成 {len(profiles)} 位人才档案。")
            st.rerun()
        except Exception as e:
            prog.empty()
            st.error(f"生成失败：{e}")

with col_ai2:
    gen_overview_disabled = not bool(api_key)
    if st.button("✨ 生成总览内容", key="ir_gen_overview",
                 disabled=gen_overview_disabled):
        with st.spinner("正在生成总览内容…"):
            try:
                ov = generate_overview(
                    all_talents_flat, repos_info, api_key,
                    model=st.session_state.get("ir_model", DEFAULT_MODEL),
                )
                existing = st.session_state.get("ir_llm_content") or {}
                existing["overview"] = ov
                st.session_state["ir_llm_content"] = existing
                st.success("总览内容生成成功。")
                st.rerun()
            except Exception as e:
                st.error(f"生成失败：{e}")

if not api_key:
    st.info("未填写 OpenRouter API Key，将使用占位符生成 PPT（AI 字段留空）。")

# ════════════════════════════════════════════════════════════
# Step 7：生成报告
# ════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("⑦ 生成报告")

n_regions_final = len(regions_talents)
n_talents_final = len(all_talents_flat)
n_repos_final      = len(st.session_state["ir_repos"])
n_index_pages      = max(1, math.ceil(n_repos_final / 6))
n_huawei_pages_est = max(1, math.ceil(n_repos_final / 6))
est_pages          = 1 + 1 + n_huawei_pages_est + n_regions_final + n_talents_final + n_index_pages

tpl_note = (
    f"  ·  模板：**{os.path.basename(selected_template_path)}**"
    if selected_template_path else ""
)
st.caption(
    f"预估页数：**{est_pages}** 页  "
    f"（封面 1 · 总览 2 · 匹配分析 {n_huawei_pages_est} · "
    f"地区分节 {n_regions_final} · 人才详情 {n_talents_final} · 索引 {n_index_pages}）{tpl_note}"
)

if st.button("🚀 生成洞察报告 PPT", type="primary", key="ir_build"):
    with st.spinner("正在构建 PPT，请稍候…"):
        try:
            current_llm = st.session_state.get("ir_llm_content") or {}

            # 加载自定义模板（如有）
            template_bytes = None
            if selected_template_path and os.path.exists(selected_template_path):
                with open(selected_template_path, "rb") as f:
                    template_bytes = f.read()

            ppt_buf = build_insight_ppt(
                regions_talents=regions_talents,
                repos_info=repos_info,
                llm_content=current_llm,
                theme=selected_theme,
                report_title=report_title or "开源人才洞察报告",
                template_bytes=template_bytes,
            )
            safe_title = (report_title or "insight")[:20].replace(" ", "_")
            fname = f"insight_report_{safe_title}.pptx"
            st.download_button(
                label="📥 下载洞察报告 PPT",
                data=ppt_buf,
                file_name=fname,
                mime=(
                    "application/vnd.openxmlformats-officedocument"
                    ".presentationml.presentation"
                ),
                key="ir_download",
            )
            st.success(f"PPT 生成成功，共 {est_pages} 页。")
        except Exception as e:
            st.error(f"生成失败：{e}")
            st.code(traceback.format_exc())
