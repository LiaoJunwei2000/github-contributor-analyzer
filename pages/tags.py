import streamlit as st
from db import init_db, list_tags, create_tag, update_tag, delete_tag, list_repos, get_repo_tags, add_repo_tag, remove_repo_tag

st.set_page_config(page_title="标签管理", page_icon="🏷️", layout="centered")
init_db()

st.title("🏷️ 标签管理")
st.caption("创建标签并给仓库贴标签，方便分类筛选。")

st.markdown("---")

# ── 新增标签 ──────────────────────────────────────────────
st.subheader("新增标签")
nc1, nc2, nc3 = st.columns([3, 1, 1])
with nc1:
    new_name = st.text_input("标签名称", placeholder="例如：自动驾驶、NLP、推荐系统", key="new_tag_name")
with nc2:
    new_color = st.color_picker("颜色", value="#4f8bff", key="new_tag_color")
with nc3:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("➕ 添加", type="primary", use_container_width=True, disabled=not new_name.strip()):
        create_tag(new_name.strip(), new_color)
        st.success(f"已添加标签「{new_name.strip()}」")
        st.rerun()

st.markdown("---")

# ── 标签列表（编辑 / 删除）─────────────────────────────────
st.subheader("全部标签")
tags = list_tags()

if not tags:
    st.info("暂无标签，请在上方创建第一个标签。")
else:
    for tag in tags:
        tid = tag["id"]
        col_name, col_color, col_save, col_del = st.columns([3, 1.5, 1, 0.8])
        with col_name:
            edited_name = st.text_input(
                "名称", value=tag["name"],
                key=f"tag_name_{tid}", label_visibility="collapsed",
            )
        with col_color:
            edited_color = st.color_picker(
                "颜色", value=tag["color"],
                key=f"tag_color_{tid}", label_visibility="collapsed",
            )
        with col_save:
            def _do_save(t_id=tid):
                n = st.session_state.get(f"tag_name_{t_id}", "").strip()
                c = st.session_state.get(f"tag_color_{t_id}", "#6B6B6B")
                if n:
                    update_tag(t_id, name=n, color=c)

            st.button("💾", key=f"save_tag_{tid}", help="保存修改", on_click=_do_save)
        with col_del:
            def _do_delete(t_id=tid):
                delete_tag(t_id)

            st.button("🗑️", key=f"del_tag_{tid}", help="删除此标签", on_click=_do_delete)

        # 当前标签预览
        cur_name = st.session_state.get(f"tag_name_{tid}", tag["name"])
        cur_color = st.session_state.get(f"tag_color_{tid}", tag["color"])
        st.markdown(
            f"<span style='background:{cur_color};color:#fff;border-radius:4px;"
            f"padding:2px 8px;font-size:0.82rem'>{cur_name}</span>",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── 仓库打标签 ────────────────────────────────────────────
st.subheader("给仓库贴标签")
repos = list_repos()
if not repos:
    st.info("数据库中暂无仓库，请先在「数据采集」页面爬取数据。")
    st.stop()

tags = list_tags()
if not tags:
    st.info("请先在上方创建标签。")
    st.stop()

tag_map = {t["id"]: t for t in tags}
all_tag_names = {t["name"]: t["id"] for t in tags}

for repo in repos:
    rname = repo["full_name"]
    current_tags = get_repo_tags(rname)
    current_ids = {t["id"] for t in current_tags}

    with st.expander(f"📦 {rname}", expanded=False):
        # 显示已有标签
        if current_tags:
            badges = " ".join(
                f"<span style='background:{t['color']};color:#fff;border-radius:4px;"
                f"padding:2px 8px;font-size:0.82rem'>{t['name']}</span>"
                for t in current_tags
            )
            st.markdown(badges, unsafe_allow_html=True)
        else:
            st.caption("暂无标签")

        # 添加标签
        add_col, rem_col = st.columns(2)
        with add_col:
            addable = [t["name"] for t in tags if t["id"] not in current_ids]
            if addable:
                sel_add = st.multiselect(
                    "添加标签", addable,
                    key=f"repo_add_{rname}", label_visibility="collapsed",
                    placeholder="选择要添加的标签...",
                )
                if sel_add:
                    if st.button("✅ 添加", key=f"btn_add_{rname}"):
                        for tname in sel_add:
                            add_repo_tag(rname, all_tag_names[tname])
                        st.rerun()
            else:
                st.caption("已添加全部标签")

        with rem_col:
            if current_tags:
                sel_rem = st.multiselect(
                    "移除标签", [t["name"] for t in current_tags],
                    key=f"repo_rem_{rname}", label_visibility="collapsed",
                    placeholder="选择要移除的标签...",
                )
                if sel_rem:
                    if st.button("❌ 移除", key=f"btn_rem_{rname}"):
                        for tname in sel_rem:
                            remove_repo_tag(rname, all_tag_names[tname])
                        st.rerun()
