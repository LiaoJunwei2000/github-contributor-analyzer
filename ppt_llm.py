"""
LLM 增强模块：调用 Claude Haiku 为贡献者批量生成人才标签和亮点。

使用方式：
    from ppt_llm import enrich_with_ai
    ai_labels = enrich_with_ai(rows, api_key, progress_cb=lambda done, total: ...)
    # ai_labels = {"login": {"ai_label": "...", "ai_highlights": ["...", ...]}}
"""

import json

_SYSTEM = """你是一位专业的技术人才分析师。
根据开发者信息，生成简洁专业的人才标签和亮点。
- ai_label：一行人才标签（≤80字符），格式：职位/身份 @ 机构 | 核心技能/领域
- ai_highlights：3条亮点（每条≤55字符），基于数据事实，有具体数字更好

规则：
- 如果 bio/name 包含中文，用中文回复；否则用英文
- ai_label 若无机构信息则省略" @ 机构"部分
- 返回纯 JSON，不要有任何其他文字或代码块标记
"""

_PROMPT_TMPL = """\
为以下 {n} 位开发者生成人才标签。

返回 JSON 格式（key 为 login）：
{{"<login>": {{"ai_label": "...", "ai_highlights": ["...", "...", "..."]}}}}

开发者列表：
{people_json}
"""


def enrich_with_ai(rows: list, api_key: str, progress_cb=None) -> dict:
    """
    批量调用 Claude Haiku 为贡献者生成 ai_label 和 ai_highlights。

    参数
    ----
    rows       : 贡献者 dict 列表，需含 login/username, name/fullname, bio,
                 company/employer, location, total_commits, followers/num_followers
    api_key    : Anthropic API Key
    progress_cb: 可选回调 progress_cb(done: int, total: int)

    返回
    ----
    {login: {"ai_label": str, "ai_highlights": [str, str, str]}}
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("请先安装 anthropic：pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)
    result: dict = {}
    batch_size = 10

    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        people = [
            {
                "login":         r.get("login") or r.get("username") or "?",
                "name":          r.get("name") or r.get("fullname") or "",
                "bio":           (r.get("bio") or "")[:200],
                "company":       r.get("company") or r.get("employer") or "",
                "location":      r.get("location") or "",
                "total_commits": r.get("total_commits") or 0,
                "followers":     r.get("followers") or r.get("num_followers") or 0,
            }
            for r in batch
        ]
        prompt = _PROMPT_TMPL.format(
            n=len(people),
            people_json=json.dumps(people, ensure_ascii=False, indent=2),
        )
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            # 有时模型会多输出 markdown 代码块，提取 JSON 部分
            if "```" in text:
                parts = text.split("```")
                for p in parts:
                    p = p.lstrip("json").strip()
                    if p.startswith("{"):
                        text = p
                        break
            parsed = json.loads(text)
            result.update(parsed)
        except Exception:
            # 单批次失败不中断，继续下一批
            pass

        if progress_cb:
            progress_cb(min(i + batch_size, len(rows)), len(rows))

    return result
