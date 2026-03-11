"""HF 爬取任务编排，无 UI 依赖，可被多页面调用。"""

import traceback
from db import (
    save_hf_repo, save_hf_contributors, get_hf_complete_profiles,
    save_hf_org, save_hf_org_members, get_hf_org_complete_profiles,
    get_hf_org_members, get_hf_contributors,
)
from hf_main import (
    fetch_hf_repo_details,
    fetch_hf_commits,
    enrich_hf_contributors,
    fetch_hf_org_overview,
    fetch_hf_org_members,
    enrich_hf_org_members,
    HfRateLimiter,
)
from background_jobs import update_job, finish_job

_PROFILE_FIELDS = [
    "fullname", "bio", "location", "website", "is_pro",
    "num_followers", "num_following", "num_models", "num_datasets", "num_spaces",
    "num_discussions", "num_papers", "num_upvotes", "num_likes",
    "orgs", "account_created",
    "linkedin_url", "scholar_url", "affiliation_type", "employer",
    "twitter_url", "github_url", "bluesky_url",
]


def run_hf_scrape_job(
    job_id: str,
    full_name: str,
    hf_type: str,
    token: str,
    resume_mode: bool = False,
):
    """
    HF 核心爬取逻辑，4 阶段：
    1. repo_info  — 获取项目元数据
    2. commits    — 分页抓取所有 commits，聚合贡献者
    3. enriching  — 并发抓取每位贡献者的 HF Profile
    4. saving     — 写入数据库
    """
    try:
        # 1. 项目元数据
        update_job(job_id, phase="repo_info")
        details = fetch_hf_repo_details(full_name, hf_type, token)
        if not details:
            finish_job(job_id, error="无法获取 HF 项目信息，请检查项目名称是否正确，以及项目是否为公开访问。")
            return
        update_job(job_id, details=details)

        # 2. 分页抓取 Commits
        update_job(job_id, phase="commits")

        def commits_progress(page: int, total_fetched: int):
            update_job(job_id, done=page, total=0, contrib_count=total_fetched)

        contributors = fetch_hf_commits(full_name, hf_type, token, progress_cb=commits_progress)
        if not contributors:
            finish_job(job_id, error="未能从 commits 中获取任何贡献者数据，项目可能为空或没有绑定 HF 账号的提交记录。")
            return
        update_job(job_id, contrib_count=len(contributors))

        # 3. 续传处理
        skip_usernames: set = set()
        existing_profiles: dict = {}
        if resume_mode:
            existing_profiles = get_hf_complete_profiles(full_name)
            skip_usernames = set(existing_profiles.keys())
        update_job(job_id, skip_count=len(skip_usernames), total=len(contributors))

        # 4. 并发抓取 Profile
        update_job(job_id, phase="enriching", done=0, total=len(contributors))

        def progress_cb(done: int, total: int, rl: HfRateLimiter):
            is_waiting = rl.status == "paused"
            update_job(
                job_id,
                phase="waiting" if is_waiting else "enriching",
                done=done,
                total=total,
                rl_status=rl.status,
                rl_remaining=rl.remaining,
                rl_wait_s=rl.wait_remaining_seconds(),
                rl_wait_until=rl.wait_until,
            )

        enriched = enrich_hf_contributors(
            contributors, token, skip_usernames=skip_usernames, progress_cb=progress_cb
        )

        # 回填跳过用户的缓存 Profile
        for row in enriched:
            uname = row.get("username")
            if uname in existing_profiles and row.get("num_followers") is None:
                ep = existing_profiles[uname]
                for f in _PROFILE_FIELDS:
                    if row.get(f) is None and ep.get(f) is not None:
                        row[f] = ep[f]

        # 5. 保存到数据库
        update_job(job_id, phase="saving")
        save_hf_repo(details)
        save_hf_contributors(full_name, hf_type, enriched)

        finish_job(job_id)

    except Exception as e:
        finish_job(job_id, error=f"{e}\n\n{traceback.format_exc()}")


_ORG_PROFILE_FIELDS = [
    "fullname", "bio", "location", "website", "is_pro",
    "num_followers", "num_following", "num_models", "num_datasets", "num_spaces",
    "num_discussions", "num_papers", "num_upvotes", "num_likes",
    "orgs", "account_created",
    "linkedin_url", "scholar_url", "affiliation_type", "employer",
    "twitter_url", "github_url", "bluesky_url",
]


def run_hf_org_scrape_job(
    job_id: str,
    org_name: str,
    token: str,
    resume_mode: bool = False,
):
    """
    HF 组织成员爬取，3 阶段：
    1. org_info  — 获取组织元数据
    2. members   — 获取成员列表（最多 500）
    3. enriching — 并发抓取每位成员的 HF Profile
    4. saving    — 写入数据库
    """
    try:
        # 1. 组织元数据
        update_job(job_id, phase="org_info")
        overview = fetch_hf_org_overview(org_name, token)
        if not overview:
            finish_job(job_id, error="无法获取组织信息，请检查组织名称是否正确，以及组织是否为公开访问。")
            return
        update_job(job_id, details=overview)

        # 2. 成员列表
        update_job(job_id, phase="members")
        members = fetch_hf_org_members(org_name, token)
        if not members:
            finish_job(job_id, error="未能获取组织成员数据，组织可能没有公开成员列表。")
            return
        update_job(job_id, contrib_count=len(members))

        # 3. 续传处理
        skip_usernames: set = set()
        existing_profiles: dict = {}
        if resume_mode:
            existing_profiles = get_hf_org_complete_profiles(org_name)
            skip_usernames = set(existing_profiles.keys())
        update_job(job_id, skip_count=len(skip_usernames), total=len(members))

        # 4. 并发抓取 Profile
        update_job(job_id, phase="enriching", done=0, total=len(members))

        def progress_cb(done: int, total: int, rl: HfRateLimiter):
            is_waiting = rl.status == "paused"
            update_job(
                job_id,
                phase="waiting" if is_waiting else "enriching",
                done=done,
                total=total,
                rl_status=rl.status,
                rl_remaining=rl.remaining,
                rl_wait_s=rl.wait_remaining_seconds(),
                rl_wait_until=rl.wait_until,
            )

        enriched = enrich_hf_org_members(
            members, token, skip_usernames=skip_usernames, progress_cb=progress_cb
        )

        # 回填跳过成员的缓存 Profile
        for row in enriched:
            uname = row.get("username")
            if uname in existing_profiles and row.get("num_followers") is None:
                ep = existing_profiles[uname]
                for f in _ORG_PROFILE_FIELDS:
                    if row.get(f) is None and ep.get(f) is not None:
                        row[f] = ep[f]

        # 5. 保存到数据库
        update_job(job_id, phase="saving")
        save_hf_org(overview)
        save_hf_org_members(org_name, enriched)

        finish_job(job_id)

    except Exception as e:
        finish_job(job_id, error=f"{e}\n\n{traceback.format_exc()}")


def run_hf_org_refresh_job(job_id: str, org_name: str, token: str):
    """
    仅重新抓取已有组织成员的 Profile，跳过成员列表重拉。
    所有成员都会被重新 enrich（不跳过任何人），用于修复存量数据。
    """
    try:
        members = get_hf_org_members(org_name)
        if not members:
            finish_job(job_id, error="数据库中无该组织成员数据，请先完整采集一次。")
            return

        update_job(job_id, phase="enriching", done=0,
                   total=len(members), contrib_count=len(members))

        def progress_cb(done: int, total: int, rl: HfRateLimiter):
            is_waiting = rl.status == "paused"
            update_job(
                job_id,
                phase="waiting" if is_waiting else "enriching",
                done=done, total=total,
                rl_status=rl.status, rl_remaining=rl.remaining,
                rl_wait_s=rl.wait_remaining_seconds(),
                rl_wait_until=rl.wait_until,
            )

        enriched = enrich_hf_org_members(
            members, token, skip_usernames=None, progress_cb=progress_cb
        )

        update_job(job_id, phase="saving")
        save_hf_org_members(org_name, enriched)
        finish_job(job_id)

    except Exception as e:
        finish_job(job_id, error=f"{e}\n\n{traceback.format_exc()}")


def run_hf_proj_refresh_job(job_id: str, repo_full_name: str, hf_type: str, token: str):
    """
    仅重新抓取已有项目贡献者的 Profile，跳过 commit 重拉。
    所有贡献者都会被重新 enrich（不跳过任何人），用于修复存量数据。
    """
    try:
        contributors = get_hf_contributors(repo_full_name)
        if not contributors:
            finish_job(job_id, error="数据库中无该项目贡献者数据，请先完整采集一次。")
            return

        update_job(job_id, phase="enriching", done=0,
                   total=len(contributors), contrib_count=len(contributors))

        def progress_cb(done: int, total: int, rl: HfRateLimiter):
            is_waiting = rl.status == "paused"
            update_job(
                job_id,
                phase="waiting" if is_waiting else "enriching",
                done=done, total=total,
                rl_status=rl.status, rl_remaining=rl.remaining,
                rl_wait_s=rl.wait_remaining_seconds(),
                rl_wait_until=rl.wait_until,
            )

        enriched = enrich_hf_contributors(
            contributors, token, skip_usernames=None, progress_cb=progress_cb
        )

        update_job(job_id, phase="saving")
        save_hf_contributors(repo_full_name, hf_type, enriched)
        finish_job(job_id)

    except Exception as e:
        finish_job(job_id, error=f"{e}\n\n{traceback.format_exc()}")
