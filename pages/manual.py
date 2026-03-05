import streamlit as st

st.title("📖 使用手册")
st.caption("GitHub Contributor Analyzer · 完整使用教程")

st.markdown("---")

# ══════════════════════════════════════════════════════
# 目录
# ══════════════════════════════════════════════════════
with st.expander("📑 目录（点击跳转）", expanded=True):
    st.markdown("""
1. [工具简介](#1)
2. [准备工作：获取 GitHub Token](#2)
3. [数据采集：爬取仓库贡献者](#3)
4. [历史数据：查看与分析](#4)
5. [字段说明](#5)
6. [常见问题](#6)
    """)

st.markdown("---")

# ══════════════════════════════════════════════════════
# 1. 工具简介
# ══════════════════════════════════════════════════════
st.subheader("1. 工具简介", anchor="1")

col1, col2, col3 = st.columns(3)
col1.info("**📥 数据采集**\n\n输入仓库地址，自动抓取所有贡献者信息并存入本地数据库")
col2.info("**📊 可视化分析**\n\n开发者画像、公司分布、地区热图、Commits 趋势等多维看板")
col3.info("**⬇️ 数据导出**\n\n支持完整 CSV（25 个字段）和精简 CSV 两种格式下载")

st.markdown("""
本工具通过 **GitHub REST API** 获取以下数据：
- 贡献者列表（基于 git commit 历史）
- 每位贡献者的 commit 数量、代码新增/删除行数
- 贡献者 GitHub 主页信息（公司、地区、邮箱、Followers 等）

> ⚠️ **注意**：API 返回的贡献者数量可能少于 GitHub 网页显示数量。
> 原因是大量项目使用 Squash Merge，合并后 commit 作者变为维护者，原 PR 作者不被计入。
> 这是 GitHub API 的已知限制。
""")

st.markdown("---")

# ══════════════════════════════════════════════════════
# 2. 获取 GitHub Token
# ══════════════════════════════════════════════════════
st.subheader("2. 准备工作：获取 GitHub Token", anchor="2")

st.markdown("""
使用 GitHub Token 可将 API 速率限制从 **60 次/小时**提升至 **5000 次/小时**，
分析大型仓库时必须配置。
""")

st.markdown("#### 步骤")

s1, s2, s3, s4, s5 = st.tabs(["步骤 1", "步骤 2", "步骤 3", "步骤 4", "步骤 5"])

with s1:
    st.markdown("**登录 GitHub，打开 Token 创建页面**")
    st.link_button("🔗 前往 github.com/settings/tokens/new", "https://github.com/settings/tokens/new")

with s2:
    st.markdown("**填写 Token 信息**")
    st.markdown("""
    - **Note（名称）**：填写任意名称，例如 `gh-analyzer`
    - **Expiration（有效期）**：建议选 `90 days` 或 `No expiration`
    """)
    st.image("https://docs.github.com/assets/cb-34573/mw-1440/images/help/settings/token_description.webp", width=500)

with s3:
    st.markdown("**勾选权限**")
    st.markdown("""
    仅需勾选 `repo` 下的 **`public_repo`**，即可读取公开仓库数据。

    | 权限 | 是否需要 |
    |------|---------|
    | `public_repo` | ✅ 必须 |
    | 其他 | ❌ 不需要 |
    """)

with s4:
    st.markdown("**生成并复制 Token**")
    st.markdown("""
    点击最下方 **Generate token** 按钮，页面会显示以 `ghp_` 开头的字符串。

    > ⚠️ **Token 只显示一次**，请立即复制并妥善保存。如果遗失需重新生成。
    """)
    st.code("ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", language="text")

with s5:
    st.markdown("**配置到工具中**")
    st.markdown("""
    **方式 A（推荐）：存入 `.env` 文件**

    在项目根目录创建 `.env` 文件（如已存在则编辑）：
    """)
    st.code('GITHUB_TOKEN=ghp_你的token', language="bash")
    st.markdown("""
    重启工具后左侧 Token 输入框会自动填充。

    **方式 B：每次手动输入**

    在左侧侧边栏的 Token 输入框中直接粘贴即可。
    """)

st.markdown("---")

# ══════════════════════════════════════════════════════
# 3. 数据采集
# ══════════════════════════════════════════════════════
st.subheader("3. 数据采集：爬取仓库贡献者", anchor="3")

st.markdown("#### 操作流程")

col_flow = st.columns(5)
steps = [
    ("①", "配置 Token", "左侧输入框填入 GitHub Token"),
    ("②", "输入仓库", "格式：`owner/repo`\n例：`facebook/react`"),
    ("③", "点击分析", "点击「🚀 开始分析」按钮"),
    ("④", "等待完成", "依次完成 5 个抓取步骤"),
    ("⑤", "查看结果", "表格预览 + 下载 CSV"),
]
for col, (num, title, desc) in zip(col_flow, steps):
    col.metric(num, title)
    col.caption(desc)

st.markdown("#### 仓库地址格式")
st.code("""
# 正确格式
facebook/react
vllm-project/vllm
microsoft/vscode

# 错误格式（不要包含完整 URL）
https://github.com/facebook/react  ❌
""", language="text")

st.markdown("#### 抓取步骤说明")
st.markdown("""
| 步骤 | 说明 | 耗时 |
|------|------|------|
| 📋 获取仓库信息 | 验证仓库存在，获取 Stars/Forks 等基础信息 | 1-2 秒 |
| 👥 抓取贡献者列表 | 分页获取所有贡献者，每页 100 人 | 视贡献者数量而定 |
| 📊 获取增删统计 | 轮询 GitHub 统计端点，首次计算需等待 | 10-60 秒 |
| 🔀 合并整理数据 | 本地计算，合并统计结果 | <1 秒 |
| 🔍 抓取用户 Profile | 并发获取每位贡献者的主页信息 | 视人数而定 |
| 💾 保存数据库 | 存入本地 SQLite，支持历史对比 | <1 秒 |
""")

st.info("💡 **大型仓库（500+ 贡献者）** 完整抓取可能需要 3-10 分钟，请耐心等待。", icon="⏱️")

st.markdown("---")

# ══════════════════════════════════════════════════════
# 4. 历史数据
# ══════════════════════════════════════════════════════
st.subheader("4. 历史数据：查看与分析", anchor="4")

st.markdown("""
采集完成后，数据自动保存到本地数据库。前往「📂 历史数据」页面查看：
""")

t1, t2, t3, t4 = st.tabs(["👤 开发者画像", "🌐 多维分析", "📋 数据表格", "⬇️ 导出"])

with t1:
    st.markdown("""
    **功能：点击贡献者查看完整个人信息**

    - 左侧列表支持按**用户名/姓名搜索**和按**公司筛选**
    - 右侧展示：头像、公司、地区、邮箱、主页、Twitter、求职状态
    - 贡献指标与 **Top 10 均值对比柱状图**
    - 点击「🔗 查看 GitHub 主页」直接跳转
    """)

with t2:
    st.markdown("""
    **功能：多维度可视化分析**

    | 图表 | 说明 |
    |------|------|
    | 🏢 公司贡献分析 | Top 15 公司的人数和 Commits 分布 |
    | 📊 Commits 直方图 | 贡献者 commit 数量分布 |
    | 🌍 地区分布 | Top 15 地区/国家贡献者人数 |
    | 👥 Followers vs Commits | 影响力与贡献量散点图 |
    | 💼 求职状态 | 开放求职比例饼图 |
    | 📈 新增 vs 删除 | Top 20 贡献者代码增删对比 |
    """)

with t3:
    st.markdown("""
    **功能：完整数据表格**

    - **搜索**：支持用户名、姓名、公司关键词过滤
    - **排序**：点击任意列标题升/降序排列
    - **Top N**：滑块控制显示前 N 名
    - 包含邮箱、主页等完整字段
    """)

with t4:
    st.markdown("""
    **功能：下载数据**

    | 格式 | 字段数 | 适用场景 |
    |------|--------|---------|
    | 完整 CSV | 25 个 | 完整分析，含所有隐私字段 |
    | 精简 CSV | 14 个 | 快速查看贡献数据 |

    下载的 CSV 使用 **UTF-8 BOM** 编码，可直接用 Excel 打开中文不乱码。
    """)

st.markdown("---")

# ══════════════════════════════════════════════════════
# 5. 字段说明
# ══════════════════════════════════════════════════════
st.subheader("5. 字段说明", anchor="5")

col_f1, col_f2 = st.columns(2)

with col_f1:
    st.markdown("**贡献数据字段**")
    st.dataframe(
        {
            "字段名": ["rank", "login", "total_commits", "total_additions", "total_deletions",
                      "net_lines", "total_changes", "avg_changes_per_commit",
                      "addition_deletion_ratio", "contributions_on_default_branch"],
            "说明": ["贡献排名（按总变更行数降序）", "GitHub 用户名",
                    "历史总 commit 数", "累计新增代码行数", "累计删除代码行数",
                    "净增行数（新增 − 删除）", "总变更行数（新增 + 删除）",
                    "每次 commit 平均变更行数", "新增行 / 删除行比值",
                    "默认分支 commit 贡献数（来自 /contributors 端点）"],
        },
        use_container_width=True,
        hide_index=True,
    )

with col_f2:
    st.markdown("**用户主页字段**")
    st.dataframe(
        {
            "字段名": ["name", "company", "location", "email", "blog",
                      "twitter_username", "hireable", "bio",
                      "followers", "following", "public_repos",
                      "public_gists", "account_created", "last_updated",
                      "avatar_url", "profile_url"],
            "说明": ["真实姓名", "所在公司/机构", "所在地区", "公开邮箱", "个人主页/网站",
                    "Twitter 用户名", "是否开放求职", "个人简介",
                    "Followers 数量", "Following 数量", "公开仓库数量",
                    "公开 Gist 数量", "账号注册时间", "账号最后更新时间",
                    "头像图片 URL", "GitHub 主页 URL"],
        },
        use_container_width=True,
        hide_index=True,
    )

st.markdown("---")

# ══════════════════════════════════════════════════════
# 6. 常见问题
# ══════════════════════════════════════════════════════
st.subheader("6. 常见问题", anchor="6")

faqs = [
    (
        "Q：为什么 API 获取的贡献者人数比 GitHub 网页少很多？",
        """
GitHub 网页统计的是**所有提交过 PR 并被 merge 的用户**，而 API 只统计 git commit 历史中有记录的用户。

大量项目使用 **Squash Merge**，合并后 commit 只保留一个，原 PR 作者不在 commit 历史中，因此不被 API 计入。这是 GitHub API 的已知限制，并非工具 bug。

**受影响的典型项目**：vllm、PyTorch、Transformers 等大型开源项目。
        """
    ),
    (
        "Q：为什么部分贡献者没有代码行数数据（additions/deletions 为 0）？",
        """
GitHub 的 `/stats/contributors` 端点对**超大型仓库**（commit 数量极多）可能返回 0，这是官方文档中注明的已知限制。

此类情况下 `total_commits` 字段仍然准确（来自 `/contributors` 端点），但增删行数无法获取。
        """
    ),
    (
        "Q：抓取过程中提示「Rate limit hit」怎么办？",
        """
这表示 GitHub API 速率限制已触发。工具会自动等待限制重置（通常不超过 1 小时）后继续。

**预防措施**：
- 确保配置了有效的 GitHub Token（未配置时限制为 60 次/小时）
- 分析大型仓库时避免同时运行多次分析
        """
    ),
    (
        "Q：数据保存在哪里？如何备份？",
        """
数据保存在项目根目录的 `contributors.db`（SQLite 数据库文件）中。

**备份方法**：直接复制 `contributors.db` 文件即可。

**注意**：`contributors.db` 已被 `.gitignore` 排除，不会上传到 GitHub，数据仅在本地保存。
        """
    ),
    (
        "Q：下载的 CSV 用 Excel 打开乱码？",
        """
本工具导出的 CSV 使用 **UTF-8 BOM** 编码，理论上 Excel 可直接识别。

如果仍然乱码：
1. 打开 Excel → 数据 → 从文本/CSV 导入
2. 选择文件编码为 `UTF-8`
3. 点击加载
        """
    ),
    (
        "Q：可以分析私有仓库吗？",
        """
可以，但需要 Token 具有对应私有仓库的读取权限：

1. 创建 Token 时勾选完整的 `repo` 权限（而非仅 `public_repo`）
2. Token 必须属于有权限访问该私有仓库的 GitHub 账号
        """
    ),
]

for question, answer in faqs:
    with st.expander(question):
        st.markdown(answer)

st.markdown("---")
st.caption("如有问题或建议，欢迎在 [GitHub Issues](https://github.com/LiaoJunwei2000/github-contributor-analyzer/issues) 中反馈。")
