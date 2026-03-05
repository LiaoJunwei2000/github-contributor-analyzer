import streamlit as st
from dotenv import load_dotenv
from db import init_db

load_dotenv()
init_db()

st.set_page_config(
    page_title="GitHub Contributor Analyzer",
    page_icon="🐙",
    layout="wide",
)

scraper = st.Page(
    "pages/scraper.py",
    title="数据采集",
    icon="🔍",
    default=True,
)
history = st.Page(
    "pages/1_📂_历史数据.py",
    title="历史数据",
    icon="📂",
)
manual = st.Page(
    "pages/manual.py",
    title="使用手册",
    icon="📖",
)

pg = st.navigation([scraper, history, manual])
pg.run()
