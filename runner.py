"""共享的爬取任务执行逻辑，无 UI 依赖，可被多页面调用。"""

from db import save_repo, save_contributors, get_complete_profiles
from main import (
    fetch_repo_details,
    fetch_all_contributors,
    poll_contributor_stats,
    merge_contrib_and_stats,
    enrich_with_user_details,
    RateLimiter,
)
from background_jobs import update_job, finish_job

_PROFILE_FIELDS = [
    "name", "email", "location", "company", "blog", "bio",
    "twitter_username", "hireable", "public_repos", "public_gists",
    "followers", "following", "account_created", "last_updated", "avatar_url",
]


def parse_repo(raw: str) -> str | None:
    """
    将用户输入统一转换为 owner/repo 格式。
    支持：
      - owner/repo
      - https://github.com/owner/repo
      - https://github.com/owner/repo/tree/main  （及其他子路径）
    """
    s = raw.strip().rstrip("/")
    if s.startswith("http"):
        if "github.com/" not in s:
            return None
        path = s.split("github.com/", 1)[-1]
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return None
    parts = s.split("/")
    if len(parts) == 2 and parts[0] and parts[1]:
        return s
    return None


def run_scrape_job(job_id: str, repo: str, token: str,
                   include_anon: bool = False, resume_mode: bool = False):
    """核心爬取逻辑，无 UI 依赖，可被多页面调用。"""
    try:
        # 1. 仓库基本信息
        update_job(job_id, phase="repo_info")
        details = fetch_repo_details(repo, token)
        if not details:
            finish_job(job_id, error="无法获取仓库信息，请检查仓库名称和 Token 权限。")
            return
        update_job(job_id, details=details)

        # 2. 贡献者列表
        update_job(job_id, phase="contributors")
        all_contribs = fetch_all_contributors(repo, token, include_anon)
        if not all_contribs:
            finish_job(job_id, error="未能获取贡献者数据。")
            return
        update_job(job_id, contrib_count=len(all_contribs))

        # 3. 代码增删统计
        update_job(job_id, phase="stats")
        stats = poll_contributor_stats(repo, token)

        # 4. 合并数据
        update_job(job_id, phase="merging")
        merged = merge_contrib_and_stats(all_contribs, stats)

        # 5. 续传处理
        skip_logins = set()
        existing_profiles = {}
        if resume_mode:
            existing_profiles = get_complete_profiles(repo)
            skip_logins = set(existing_profiles.keys())
        update_job(job_id, skip_count=len(skip_logins), total=len(merged))

        # 6. 并发抓取用户 Profile
        update_job(job_id, phase="enriching", done=0, total=len(merged))

        def progress_cb(done: int, total: int, rl: RateLimiter):
            update_job(job_id,
                done=done, total=total,
                rl_status=rl.status,
                rl_remaining=rl.remaining,
                rl_wait_s=rl.wait_remaining_seconds(),
            )

        enriched = enrich_with_user_details(
            merged, token, skip_logins=skip_logins, progress_cb=progress_cb
        )

        # 续传：将跳过用户的 Profile 字段从 DB 记录回填
        for row in enriched:
            login = row.get("login")
            if login in existing_profiles and row.get("followers") is None:
                ep = existing_profiles[login]
                for f in _PROFILE_FIELDS:
                    if row.get(f) is None and ep.get(f) is not None:
                        row[f] = ep[f]

        # 7. 保存到数据库
        update_job(job_id, phase="saving")
        save_repo(details)
        save_contributors(details["full_name"], enriched)

        finish_job(job_id)

    except Exception as e:
        import traceback
        finish_job(job_id, error=f"{e}\n\n{traceback.format_exc()}")
