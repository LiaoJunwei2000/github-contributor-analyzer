"""
洞察报告 LLM 模块：通过 OpenRouter API 批量生成人才档案和项目总览。

使用方式：
    from insight_llm import generate_talent_profiles, generate_overview
    profiles = generate_talent_profiles(talents, repos_info, api_key,
                                        model="google/gemini-flash-1.5",
                                        progress_cb=...)
    overview = generate_overview(talents, repos_info, api_key)
"""

import json

# ── 默认模型 ──────────────────────────────────────────────────
DEFAULT_MODEL = "google/gemini-2.0-flash-001"

OPENROUTER_MODELS = [
    "google/gemini-2.0-flash-001",
    "google/gemini-flash-1.5-8b",
    "anthropic/claude-haiku-4-5",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-chat-v3-0324",
    "openai/gpt-4o-mini",
]

# ── 地区分组 ──────────────────────────────────────────────────
ALL_REGION_GROUPS = [
    "香港", "新加坡", "台湾", "澳门",
    "中国大陆", "北美", "日本", "韩国",
    "欧洲", "东南亚", "中东", "其他",
]

# 静态关键字（全小写，用于 in 匹配）
_STATIC_REGION_KEYWORDS: dict = {
    "香港": ["hong kong", "香港", "hksar"],
    "新加坡": ["singapore", "新加坡"],
    "台湾": ["taiwan", "台湾", "taipei", "台北", "taichung", "台中",
             "高雄", "kaohsiung", "hsinchu", "新竹", "tainan", "台南"],
    "澳门": ["macau", "macao", "澳门"],
    "中国大陆": [
        "china", "beijing", "shanghai", "shenzhen", "guangzhou",
        "北京", "上海", "深圳", "广州", "成都", "杭州", "西安", "武汉",
        "南京", "中国", "chengdu", "hangzhou", "wuhan", "nanjing",
        "xi'an", "xian", "tianjin", "天津", "重庆", "chongqing",
        "suzhou", "苏州", "qingdao", "青岛", "厦门", "xiamen",
        "zhengzhou", "郑州", "长沙", "changsha", "合肥", "hefei",
    ],
    "日本": [
        "japan", "tokyo", "osaka", "kyoto", "yokohama",
        "日本", "東京", "大阪", "名古屋", "nagoya", "fukuoka", "福岡",
        "sapporo", "札幌", "sendai", "仙台",
    ],
    "韩国": [
        "south korea", "korea", "seoul", "busan",
        "대한민국", "한국", "서울", "부산",
    ],
    "北美": [
        "united states", "usa", "u.s.a", "u.s.", " america",
        "canada", "new york", "san francisco", "seattle", "los angeles",
        "chicago", "boston", "toronto", "vancouver", "montreal",
        "bay area", "silicon valley", "austin", "denver", "portland",
        "atlanta", "miami", "washington", "philadelphia", "houston",
        "san jose", "san diego", "minneapolis", "phoenix", "detroit",
        "calgary", "ottawa", "edmonton", "winnipeg",
        "new jersey", "massachusetts", "california", "texas", "florida",
        "cambridge, ma", "menlo park", "mountain view", "palo alto",
        "sunnyvale", "cupertino", "redmond", "bellevue",
    ],
    "欧洲": [
        "germany", "france", "united kingdom", "england", "london", "paris",
        "berlin", "amsterdam", "netherlands", "switzerland", "sweden",
        "norway", "denmark", "finland", "austria", "italy", "spain",
        "portugal", "poland", "czech", "russia", "ireland", "scotland",
        "wales", "belgium", "luxembourg", "munich", "hamburg", "zurich",
        "stockholm", "oslo", "helsinki", "rome", "milan", "barcelona",
        "madrid", "lisbon", "warsaw", "prague", "budapest", "ukraine",
        "rotterdam", "edinburgh", "glasgow", "europe", "vienna", "wien",
        "gothenburg", "malmo", "cologne", "düsseldorf", "frankfurt",
        "stuttgart", "zurich", "geneva", "lausanne",
    ],
    "东南亚": [
        "indonesia", "malaysia", "thailand", "vietnam", "philippines",
        "myanmar", "cambodia", "laos", "brunei", "jakarta", "bangkok",
        "ho chi minh", "hanoi", "manila", "kuala lumpur", "penang",
        "singapore" ,  # 新加坡已在上面，这里不加
    ],
    "中东": [
        "israel", "turkey", "iran", "saudi", "dubai", "abu dhabi", "uae",
        "qatar", "kuwait", "bahrain", "jordan", "lebanon", "egypt",
        "tel aviv", "ankara", "istanbul",
    ],
}
# 移除东南亚里误加的 singapore（新加坡已有独立分组）
_STATIC_REGION_KEYWORDS["东南亚"] = [
    k for k in _STATIC_REGION_KEYWORDS["东南亚"] if k != "singapore"
]


def static_classify_location(location: str) -> list | None:
    """
    纯静态关键字匹配（无 API）。
    返回 list[str]（可多地区），或 None（需 AI 处理）。
    空/None → ["未分类"]。
    """
    if not location or str(location).strip() in ("", "None", "none"):
        return ["未分类"]
    loc_lower = location.lower()
    found = []
    for region, keywords in _STATIC_REGION_KEYWORDS.items():
        for kw in keywords:
            if kw in loc_lower:
                if region not in found:
                    found.append(region)
                break
    return found if found else None


# ── 地区分类 Prompts ──────────────────────────────────────────

_CLASSIFY_SYSTEM = """你是一个地理位置分类器，将开发者的 location 字段归类到所属地区。
可选地区：香港、新加坡、台湾、澳门、中国大陆、北美、日本、韩国、欧洲、东南亚、中东、其他。
规则：
- 返回纯 JSON，格式 {"<raw_location>": ["地区1", "地区2"]}
- 一个位置可属于多个地区（如"Taipei / Seattle"同时归入台湾+北美）
- 无法识别、虚构地名、网络梗、乱码 → 归入 ["其他"]
- 不返回任何多余解释"""

_CLASSIFY_PROMPT = """\
请为以下 {n} 个 location 字段分类：
{locations_json}"""


def classify_locations(locations: list, api_key: str,
                       model: str = DEFAULT_MODEL) -> dict:
    """
    调用 AI 批量分类地点字符串（50/批）。

    参数
    ----
    locations : 待分类的 raw_location 字符串列表（已去重，只含需 AI 处理的）
    api_key   : OpenRouter API Key
    model     : OpenRouter 模型 ID

    返回
    ----
    {raw_location: list[str]}
    """
    client = _make_client(api_key)
    result = {}
    batch_size = 50

    for i in range(0, len(locations), batch_size):
        batch = locations[i: i + batch_size]
        prompt = _CLASSIFY_PROMPT.format(
            n=len(batch),
            locations_json=json.dumps(batch, ensure_ascii=False),
        )
        try:
            text = _chat(client, model, _CLASSIFY_SYSTEM, prompt, max_tokens=1024)
            parsed = _extract_json(text)
            result.update(parsed)
        except Exception:
            for loc in batch:
                result[loc] = ["其他"]

    return result


# ── Prompt 模板 ───────────────────────────────────────────────

_PROFILE_SYSTEM = """你是一位专业的技术人才分析师，为企业招聘报告生成简洁专业的人才档案。
规则：
- 简洁专业中文，适合 PPT 展示，避免空洞词汇
- 优先使用数据（commits/followers 数字）
- tech_direction: 技术方向，≤120字符，如"分布式存储 / Rust系统编程"
- contribution_summary: 贡献摘要，≤150字符，含具体数字
- key_skills: 3个技能标签，每个≤20字符
- match_score: 0-100整数，评估其技术与华为开源战略的契合度
- match_level: "A"（≥80）/ "B"（60-79）/ "C"（40-59）/ "D"（<40）
- match_reason: ≤120字符，说明匹配理由或差距
- 返回纯 JSON，不要有任何其他文字或代码块标记
"""

_PROFILE_PROMPT = """\
为以下 {n} 位开发者生成人才档案。

仓库信息（供参考）：
{repos_json}

返回 JSON 格式（key 为 login）：
{{"<login>": {{
  "tech_direction": "...",
  "contribution_summary": "...",
  "key_skills": ["...", "...", "..."],
  "match_score": 85,
  "match_level": "A",
  "match_reason": "..."
}}}}

开发者列表：
{people_json}
"""

_OVERVIEW_SYSTEM = """你是一位专业的技术人才分析师，为企业高管汇报生成简洁有力的人才总览。
规则：
- 简洁专业中文，适合 PPT 高管汇报
- 数据优先，突出量化指标
- 返回纯 JSON，不要有任何其他文字或代码块标记
"""

_OVERVIEW_PROMPT = """\
根据以下开源人才数据，生成人才总览分析。

仓库信息：
{repos_json}

人才概况（共 {n} 人）：
{summary_json}

返回以下 JSON 结构：
{{
  "quality_summary": "3-4句话，对这批人才的整体质量评估",
  "density_stats": "1-2句话，含具体人数和地区分布数字",
  "project_tech_map": {{
    "<repo>": {{
      "description": "≤100字，项目技术简介",
      "tech_areas": ["技术领域1", "技术领域2"],
      "huawei_value": "≤120字，若加入华为，这些人才可贡献..."
    }}
  }}
}}
"""


# ── 工具函数 ──────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """提取 JSON，处理 markdown 代码块包裹的情况。"""
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break
    return json.loads(text)


def _make_client(api_key: str):
    """创建 OpenRouter 客户端（OpenAI-compatible）。"""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("请先安装 openai：pip install openai")
    return OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://github.com/openSourceGithubScraper",
            "X-Title": "OpenSource Talent Insight",
        },
    )


def _chat(client, model: str, system: str, user: str, max_tokens: int = 2048) -> str:
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return resp.choices[0].message.content


# ── 主接口 ────────────────────────────────────────────────────

def generate_talent_profiles(talents: list, repos_info: dict,
                              api_key: str,
                              model: str = DEFAULT_MODEL,
                              progress_cb=None) -> dict:
    """
    批量调用 LLM 为贡献者生成人才档案（8人/批）。

    参数
    ----
    talents     : 贡献者 dict 列表（含 _repos: list[str] 字段）
    repos_info  : {repo: {"description", "language", "stars"}}
    api_key     : OpenRouter API Key
    model       : OpenRouter 模型 ID
    progress_cb : 可选回调 progress_cb(done: int, total: int)

    返回
    ----
    {login: {tech_direction, contribution_summary, key_skills,
             match_score, match_level, match_reason}}
    """
    client = _make_client(api_key)
    result: dict = {}
    batch_size = 8
    last_error = None

    repos_json = json.dumps(
        {r: info for r, info in repos_info.items()},
        ensure_ascii=False, indent=2,
    )

    for i in range(0, len(talents), batch_size):
        batch = talents[i: i + batch_size]
        people = [
            {
                "login":         t.get("login") or t.get("username") or "?",
                "name":          t.get("name") or t.get("fullname") or "",
                "bio":           (t.get("bio") or "")[:200],
                "company":       t.get("company") or t.get("employer") or "",
                "location":      t.get("location") or "",
                "total_commits": t.get("total_commits") or 0,
                "followers":     t.get("followers") or t.get("num_followers") or 0,
                "repos":         t.get("_repos") or [],
            }
            for t in batch
        ]
        prompt = _PROFILE_PROMPT.format(
            n=len(people),
            repos_json=repos_json,
            people_json=json.dumps(people, ensure_ascii=False, indent=2),
        )
        try:
            text = _chat(client, model, _PROFILE_SYSTEM, prompt)
            parsed = _extract_json(text)
            result.update(parsed)
        except Exception as exc:
            last_error = exc

        if progress_cb:
            progress_cb(min(i + batch_size, len(talents)), len(talents))

    if not result and last_error is not None:
        raise last_error

    return result


def generate_overview(talents: list, repos_info: dict,
                      api_key: str,
                      model: str = DEFAULT_MODEL) -> dict:
    """
    单次调用生成总览内容（全量汇总，不分批）。

    返回
    ----
    {
      "quality_summary": str,
      "density_stats": str,
      "project_tech_map": {repo: {"description", "tech_areas", "huawei_value"}}
    }
    """
    client = _make_client(api_key)

    repos_json = json.dumps(
        {r: info for r, info in repos_info.items()},
        ensure_ascii=False, indent=2,
    )

    summary = [
        {
            "login":     t.get("login") or "?",
            "location":  t.get("location") or "",
            "commits":   t.get("total_commits") or 0,
            "followers": t.get("followers") or t.get("num_followers") or 0,
            "repos":     t.get("_repos") or [],
        }
        for t in talents
    ]

    prompt = _OVERVIEW_PROMPT.format(
        n=len(talents),
        repos_json=repos_json,
        summary_json=json.dumps(summary, ensure_ascii=False, indent=2),
    )

    text = _chat(client, model, _OVERVIEW_SYSTEM, prompt)
    return _extract_json(text)
