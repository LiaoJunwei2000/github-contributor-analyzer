import streamlit as st
from dotenv import load_dotenv
from db import init_db

load_dotenv()
init_db()

st.set_page_config(
    page_title="GitHub Contributor Analyzer",
    page_icon="🐙",
    layout="centered",
)

scraper = st.Page(
    "pages/scraper.py",
    title="数据采集",
    icon="🔍",
    default=True,
)
batch = st.Page(
    "pages/batch_scraper.py",
    title="批量采集",
    icon="📥",
)
history = st.Page(
    "pages/1_📂_历史数据.py",
    title="历史数据",
    icon="📂",
)
ppt = st.Page(
    "pages/ppt_generator.py",
    title="PPT 生成",
    icon="📊",
)
tags = st.Page(
    "pages/tags.py",
    title="标签管理",
    icon="🏷️",
)
manual = st.Page(
    "pages/manual.py",
    title="使用手册",
    icon="📖",
)
hf_scraper = st.Page(
    "pages/hf_scraper.py",
    title="HF 采集",
    icon="🤗",
)
hf_history = st.Page(
    "pages/hf_history.py",
    title="HF 历史",
    icon="🗂️",
)

pg = st.navigation({
    "🐙 GitHub": [scraper, batch, history, ppt, tags],
    "🤗 Hugging Face": [hf_scraper, hf_history],
    "其他": [manual],
})

# ── 移动端响应式 CSS ──
st.markdown("""
<style>
/* 手机竖屏：列自动竖排 */
@media (max-width: 640px) {
    /* 多列布局改为竖排 */
    [data-testid="stHorizontalBlock"] {
        flex-direction: column !important;
    }
    [data-testid="stColumn"] {
        width: 100% !important;
        flex: none !important;
        min-width: 100% !important;
    }

    /* 按钮撑满宽度 */
    [data-testid="stButton"] > button {
        width: 100% !important;
    }

    /* 减少主内容区左右内边距 */
    .block-container {
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }

    /* 指标卡字号缩小避免溢出 */
    [data-testid="stMetric"] label {
        font-size: 0.75rem !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
    }

    /* 表格横向可滚动 */
    [data-testid="stDataFrame"] {
        overflow-x: auto !important;
    }
}

/* 平板横屏：适当收窄 */
@media (max-width: 1024px) and (min-width: 641px) {
    .block-container {
        padding-left: 2rem !important;
        padding-right: 2rem !important;
    }
}
</style>
""", unsafe_allow_html=True)

pg.run()
