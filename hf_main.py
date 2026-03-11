"""Hugging Face Hub 贡献者爬取核心逻辑。"""

import os
import re
import sys
import time
import threading
from typing import List, Dict, Any, Optional, Callable
import requests
from concurrent.futures import ThreadPoolExecutor

# ============ 配置 ============
HF_BASE = "https://huggingface.co"
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
HF_CONCURRENCY = 2   # 5分钟窗口更短，保守并发数


# ============ 限速管理器 ============
class HfRateLimiter:
    """
    线程安全的 HF 限速管理器。
    HF 使用 5 分钟固定窗口，响应头：
        RateLimit: "api";r={remaining};t={seconds_until_reset}
    - remaining < 100 → 降速（每请求加 0.5s 延迟）
    - remaining < 20  → 暂停，等待 t 秒后自动恢复（每秒 tick，可被外部轮询）
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sleep_lock = threading.Lock()
        self._resume = threading.Event()
        self._resume.set()
        self.remaining: int = 1000
        self.reset_in: int = 300      # 距下次重置的秒数
        self._reset_recorded_at: float = time.time()
        self.status: str = "normal"   # "normal" | "slow" | "paused"
        self.wait_until: float = 0.0  # unix 时间戳，等待结束时间（0 表示未等待）

    def record(self, resp: Optional[requests.Response]):
        """从响应头解析 HF RateLimit 并更新状态。"""
        if resp is None:
            return
        header = resp.headers.get("RateLimit", "")
        if not header:
            return
        try:
            r_match = re.search(r'r=(\d+)', header)
            t_match = re.search(r't=(\d+)', header)
            if not r_match:
                return
            remaining = int(r_match.group(1))
            reset_in = int(t_match.group(1)) if t_match else 300
        except (ValueError, AttributeError):
            return

        with self._lock:
            self.remaining = remaining
            self.reset_in = reset_in
            self._reset_recorded_at = time.time()
            if remaining < 20:
                self.status = "paused"
                self._resume.clear()
            elif remaining < 100:
                self.status = "slow"
                self._resume.set()
            else:
                self.status = "normal"
                self._resume.set()

    def pause(self, reset_in: int = 300):
        """从 429 响应显式触发暂停。"""
        with self._lock:
            self.remaining = 0
            self.reset_in = reset_in
            self._reset_recorded_at = time.time()
            self.status = "paused"
            self._resume.clear()

    def wait_if_needed(self):
        """
        若处于暂停状态，阻塞当前线程直到限速重置。
        只有一个线程负责 sleep（每次 1 秒），其余线程等待 Event。
        sleep 期间持续更新 wait_until，供外部轮询倒计时。
        """
        if self._resume.is_set():
            return
        with self._sleep_lock:
            if self._resume.is_set():
                return
            elapsed = time.time() - self._reset_recorded_at
            wait_s = max(5, int(self.reset_in - elapsed) + 2)
            deadline = time.time() + wait_s
            with self._lock:
                self.wait_until = deadline
            _log(f"[WARN] HF Rate limit: pausing all threads for {wait_s}s...")
            while time.time() < deadline:
                time.sleep(1)
            with self._lock:
                self.remaining = 1000
                self.status = "normal"
                self.wait_until = 0.0
                self._resume.set()
        self._resume.wait()

    @property
    def request_delay(self) -> float:
        return 0.5 if self.status == "slow" else 0.0

    def wait_remaining_seconds(self) -> int:
        """返回限速等待剩余秒数（wait_until > 0 时使用精确值，否则估算）。"""
        if self.wait_until > 0:
            return max(0, int(self.wait_until - time.time()))
        elapsed = time.time() - self._reset_recorded_at
        return max(0, int(self.reset_in - elapsed) + 1)


# ============ 工具函数 ============
def _log(msg: str, file=sys.stdout):
    print(msg, file=file)


def _hf_request(
    url: str,
    token: str,
    rate_limiter: Optional[HfRateLimiter] = None,
    timeout: int = REQUEST_TIMEOUT,
) -> Optional[requests.Response]:
    """
    发起健壮的 HF API 请求。
    - 429: 触发暂停并重试，不计入 error_count
    - 404: 返回 None
    - 其他错误: 最多重试 MAX_RETRIES 次，指数退避
    """
    headers = {"User-Agent": "HFContribExport/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    error_count = 0
    while error_count < MAX_RETRIES:
        if rate_limiter:
            rate_limiter.wait_if_needed()
            delay = rate_limiter.request_delay
            if delay:
                time.sleep(delay)
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)

            if rate_limiter:
                rate_limiter.record(resp)

            if resp.status_code == 429:
                # 解析重置时间
                rl_header = resp.headers.get("RateLimit", "")
                t_match = re.search(r't=(\d+)', rl_header)
                reset_in = int(t_match.group(1)) if t_match else 300
                if rate_limiter:
                    rate_limiter.pause(reset_in)
                else:
                    time.sleep(reset_in + 2)
                continue  # 不增加 error_count

            if resp.status_code == 404:
                return None

            if resp.status_code == 200:
                return resp

            resp.raise_for_status()

        except requests.exceptions.RequestException as e:
            error_count += 1
            if error_count < MAX_RETRIES:
                time.sleep(2 ** (error_count - 1))
            else:
                _log(f"[ERROR] HF request failed for {url}: {e}", file=sys.stderr)
                return None
    return None


# ============ 输入解析 ============
def parse_hf_repo(raw: str) -> Optional[tuple]:
    """
    将用户输入转换为 (full_name, hf_type)。
    支持：
      - "meta-llama/Llama-3.1-8B"              → ("meta-llama/Llama-3.1-8B", "model")
      - "https://huggingface.co/meta-llama/..."  → ("meta-llama/...", "model")
      - "https://huggingface.co/datasets/..."    → (..., "dataset")
      - "https://huggingface.co/spaces/..."      → (..., "space")
    返回 None 表示格式无效。
    """
    s = raw.strip().rstrip("/")

    if s.startswith("http"):
        if "huggingface.co/" not in s:
            return None
        path = s.split("huggingface.co/", 1)[-1]
        # 去掉尾部可能的 /blob/main 等子路径
        parts = path.split("/")
        if parts[0] == "datasets":
            if len(parts) >= 3:
                return f"{parts[1]}/{parts[2]}", "dataset"
            return None
        if parts[0] == "spaces":
            if len(parts) >= 3:
                return f"{parts[1]}/{parts[2]}", "space"
            return None
        # 普通 model URL
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}", "model"
        return None

    # 非 URL：默认 model
    parts = s.split("/")
    if len(parts) == 2 and parts[0] and parts[1]:
        return s, "model"
    return None


# ============ 项目元数据 ============
def fetch_hf_repo_details(full_name: str, hf_type: str, token: str) -> Optional[Dict[str, Any]]:
    """
    GET /api/{hf_type}s/{full_name}
    返回标准化后的项目元数据 dict。
    """
    url = f"{HF_BASE}/api/{hf_type}s/{full_name}"
    resp = _hf_request(url, token)
    if not resp:
        return None
    data = resp.json()

    # 提取 license（可能在 cardData 或 tags 中）
    license_val = None
    card_data = data.get("cardData") or {}
    if isinstance(card_data, dict):
        license_val = card_data.get("license")
    if not license_val:
        # 也可能在 tags 列表中以 "license:xxx" 形式出现
        for tag in (data.get("tags") or []):
            if isinstance(tag, str) and tag.startswith("license:"):
                license_val = tag.split(":", 1)[1]
                break

    return {
        "full_name": full_name,
        "hf_type": hf_type,
        "description": data.get("description") or data.get("cardData", {}).get("description") if isinstance(data.get("cardData"), dict) else data.get("description"),
        "author": data.get("author"),
        "likes": data.get("likes", 0),
        "downloads": data.get("downloads", 0),
        "pipeline_tag": data.get("pipeline_tag"),
        "library_name": data.get("library_name"),
        "tags": data.get("tags") or [],
        "license": license_val,
        "gated": data.get("gated", False),
        "created_at": data.get("createdAt"),
        "last_modified": data.get("lastModified"),
        "sha": data.get("sha"),
    }


# ============ Commits 分页（贡献者聚合）============
def fetch_hf_commits(
    full_name: str,
    hf_type: str,
    token: str,
    progress_cb: Optional[Callable] = None,
    rate_limiter: Optional[HfRateLimiter] = None,
) -> List[Dict[str, Any]]:
    """
    分页抓取所有 commits，聚合出贡献者列表。
    HF commits API：GET /api/{hf_type}s/{full_name}/commits/{branch}?p={page}
    每页固定 100 条，page 从 0 开始，返回空数组时停止。

    返回列表，每项：
    {
        "rank": int,
        "username": str,
        "avatar_url": str,
        "total_commits": int,
        "first_commit_at": str,
        "last_commit_at": str,
        "profile_url": str,
    }
    按 total_commits 降序排序，已加 rank。
    """
    if rate_limiter is None:
        rate_limiter = HfRateLimiter()

    # username → {"dates": [str], "avatar_url": str}
    contrib_map: Dict[str, Dict] = {}
    page = 0
    total_commits_fetched = 0

    while True:
        url = f"{HF_BASE}/api/{hf_type}s/{full_name}/commits/main?p={page}"
        resp = _hf_request(url, token, rate_limiter=rate_limiter)
        if not resp:
            break
        data = resp.json()
        if not data:
            break

        for commit in data:
            total_commits_fetched += 1
            commit_date = commit.get("date", "")
            for author in (commit.get("authors") or []):
                # 实际 API 响应字段：user（用户名）和 avatar（头像 URL）
                uname = author.get("user") or author.get("username")
                if not uname:
                    continue
                if uname not in contrib_map:
                    contrib_map[uname] = {
                        "dates": [],
                        "avatar_url": author.get("avatar") or author.get("avatarUrl") or "",
                    }
                contrib_map[uname]["dates"].append(commit_date)

        if progress_cb:
            progress_cb(page + 1, total_commits_fetched)

        page += 1

    # 聚合并排序
    rows = []
    for uname, info in contrib_map.items():
        dates = sorted(d for d in info["dates"] if d)
        rows.append({
            "username": uname,
            "avatar_url": info["avatar_url"],
            "total_commits": len(info["dates"]),
            "first_commit_at": dates[0] if dates else None,
            "last_commit_at": dates[-1] if dates else None,
            "profile_url": f"{HF_BASE}/{uname}",
        })

    rows.sort(key=lambda x: x["total_commits"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return rows


# ============ Bio 解析工具 ============

def _extract_contact_urls(bio: str, website: str) -> Dict[str, Optional[str]]:
    """从 bio 和 website 中正则提取 LinkedIn / Google Scholar URL。"""
    text = f"{bio or ''} {website or ''}"
    linkedin_m = re.search(r'https?://(?:www\.)?linkedin\.com/in/[\w\-%]+', text)
    scholar_m = re.search(
        r'https?://scholar\.google\.com/citations\?[^\s"\'><]+', text
    )
    return {
        "linkedin_url": linkedin_m.group(0).rstrip(".,;)>") if linkedin_m else None,
        "scholar_url": scholar_m.group(0).rstrip(".,;)>") if scholar_m else None,
    }


def _parse_affiliation(bio: str) -> Dict[str, Optional[str]]:
    """
    从 bio 中用规则推断身份类型和所属机构。
    affiliation_type: "student" | "researcher" | "employee" | "unknown"
    employer: 字符串（@Org 或 "at Org" 模式提取）
    """
    text = bio or ""
    tl = text.lower()

    is_student = bool(re.search(
        r'\b(phd\s*(student|candidate|researcher)?|ph\.d|ms\s*student|msc\s*student'
        r'|master[\'s]*\s*student|undergraduate|grad\s*student|intern[^a-z]'
        r'|research\s*intern|博士生?|硕士生?|研究生|学生)\b',
        tl
    ))

    if is_student:
        aff_type = "student"
    elif re.search(
        r'\b(research\s*(scientist|engineer|lead|fellow)|principal\s*researcher'
        r'|senior\s*researcher|scientist|professor|prof\.|postdoc|faculty)\b', tl
    ):
        aff_type = "researcher"
    elif re.search(
        r'\b(engineer|developer|software|ml|ai|data\s*scientist|tech\s*lead'
        r'|cto|ceo|founder|co\-founder|staff|sde|swe)\b', tl
    ):
        aff_type = "employee"
    else:
        aff_type = "unknown"

    # 提取机构：优先 @Handle，其次 "at/@ Company"
    employer = None
    at_m = re.search(r'(?<!\w)@([\w\-]{2,40})', text)
    if at_m:
        employer = at_m.group(1)
    else:
        at_sent = re.search(
            r'\bat\s+([A-Z][A-Za-z0-9&\s\-]{1,35}?)(?:\s*[,.|;]|\s+and\s|\s*$)',
            text
        )
        if at_sent:
            employer = at_sent.group(1).strip()

    return {"affiliation_type": aff_type, "employer": employer}


# ============ 个人主页 HTML 解析（signup block）============

def _fetch_signup_block(username: str, token: str) -> Dict[str, Any]:
    """
    抓取 https://huggingface.co/{username} 页面，解析嵌入的 signup JSON。
    返回包含 twitter / github / linkedin / homepage / bluesky 等字段的 dict。
    用户未填写时对应 key 值为空字符串或不存在。
    """
    url = f"{HF_BASE}/{username}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; HFContribExport/1.0)"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return {}
        import html as html_lib, json as _json
        decoded = html_lib.unescape(resp.text)
        idx = decoded.find('"signup":')
        if idx == -1:
            return {}
        start = decoded.index('{', idx)
        obj, _ = _json.JSONDecoder().raw_decode(decoded, start)
        return obj if isinstance(obj, dict) else {}
    except Exception as e:
        _log(f"[WARN] signup block fetch failed for {username}: {e}", file=sys.stderr)
        return {}


def _build_social_urls(signup: Dict[str, Any]) -> Dict[str, Any]:
    """将 signup block 里的 handle 转换为完整 URL。"""
    tw  = (signup.get("twitter") or "").strip()
    gh  = (signup.get("github") or "").strip()
    li  = (signup.get("linkedin") or "").strip()
    bs  = (signup.get("bluesky") or "").strip()
    hp  = (signup.get("homepage") or "").strip()
    return {
        "website":      hp or None,
        "twitter_url":  f"https://x.com/{tw}"       if tw else None,
        "github_url":   f"https://github.com/{gh}"  if gh else None,
        "linkedin_url": f"https://linkedin.com/in/{li}" if li and not li.startswith("http") else (li or None),
        "bluesky_url":  f"https://bsky.app/profile/{bs}" if bs and not bs.startswith("http") else (bs or None),
    }


# ============ 用户 Profile ============
def fetch_hf_user_profile(
    username: str,
    token: str,
    rate_limiter: Optional[HfRateLimiter] = None,
) -> Optional[Dict[str, Any]]:
    """
    双源合并抓取用户 Profile：
    - huggingface_hub.get_user_overview()：orgs / 数量统计（REST API 的 orgsNames 有时返回 null）
    - REST /api/users/{username}/overview：bio / location / website（库不提供这三个字段）
    两者独立调用，互为补充。
    """
    # ── 主源：库（orgs 数据完整可靠）──────────────────────────
    hub_data: Dict[str, Any] = {}
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token or None)
        u = api.get_user_overview(username)
        hub_data = {
            "fullname":     getattr(u, "fullname", None),
            "is_pro":       bool(getattr(u, "is_pro", False)),
            "num_followers": getattr(u, "num_followers", 0) or 0,
            "num_following": getattr(u, "num_following", 0) or 0,
            "num_models":    getattr(u, "num_models", 0) or 0,
            "num_datasets":  getattr(u, "num_datasets", 0) or 0,
            "num_spaces":    getattr(u, "num_spaces", 0) or 0,
            "orgs":          [o.name for o in (getattr(u, "orgs", None) or [])],
            "account_created": str(getattr(u, "createdAt", None) or ""),
        }
    except Exception as e:
        _log(f"[WARN] hub get_user_overview failed for {username}: {e}", file=sys.stderr)

    # ── 补充源：REST API（bio / location / website）──────────
    url = f"{HF_BASE}/api/users/{username}/overview"
    resp = _hf_request(url, token, rate_limiter=rate_limiter)

    # 若 user 端点 404，尝试 org 端点（google/allenai 等以 org handle 提交）
    is_org = False
    if not resp:
        org_resp = _hf_request(
            f"{HF_BASE}/api/organizations/{username}/overview", token, rate_limiter=rate_limiter
        )
        if org_resp:
            resp = org_resp
            is_org = True

    if not resp and not hub_data:
        return None

    rest_data: Dict[str, Any] = {}
    if resp:
        d = resp.json()
        if is_org:
            # org 端点字段与 user 端点不同
            rest_data = {
                "fullname":        d.get("fullname"),
                "bio":             d.get("details"),
                "location":        d.get("location"),
                "website":         d.get("websiteUrl") or d.get("website"),
                "is_pro":          False,
                "num_followers":   d.get("numFollowers", 0) or 0,
                "num_following":   0,
                "num_models":      d.get("numModels", 0) or 0,
                "num_datasets":    d.get("numDatasets", 0) or 0,
                "num_spaces":      d.get("numSpaces", 0) or 0,
                "num_discussions": 0,
                "num_papers":      d.get("numPapers", 0) or 0,
                "num_upvotes":     0,
                "num_likes":       0,
                "orgs":            [],
                "account_created": None,
            }
        else:
            # orgs 在 REST 中为 list of dicts: [{"name": "...", "fullname": "...", ...}]
            rest_orgs = [o.get("name") for o in (d.get("orgs") or []) if isinstance(o, dict) and o.get("name")]
            rest_data = {
                "fullname":        d.get("fullname"),
                "bio":             d.get("details"),    # REST 字段为 details，不是 bio
                "location":        d.get("location"),
                "website":         d.get("website"),
                "is_pro":          bool(d.get("isPro", False)),
                "num_followers":   d.get("numFollowers", 0) or 0,
                "num_following":   d.get("numFollowing", 0) or 0,
                "num_models":      d.get("numModels", 0) or 0,
                "num_datasets":    d.get("numDatasets", 0) or 0,
                "num_spaces":      d.get("numSpaces", 0) or 0,
                "num_discussions": d.get("numDiscussions", 0) or 0,
                "num_papers":      d.get("numPapers", 0) or 0,
                "num_upvotes":     d.get("numUpvotes", 0) or 0,
                "num_likes":       d.get("numLikes", 0) or 0,
                # orgs 只在 hub_data 中可靠，REST 的 orgs 可能为 null，作为兜底
                "orgs":            rest_orgs,
                "account_created": d.get("createdAt"),
            }

    # ── 合并：hub_data 优先，REST 填补缺失字段 ─────────────
    # 先以 REST 为基础，再用 hub_data 中非空的值覆盖（避免空字符串覆盖有效数据）
    merged = dict(rest_data)
    for k, v in hub_data.items():
        if v is not None and v != "" and v != [] and v != 0:
            merged[k] = v
        elif k not in merged or merged[k] is None:
            merged[k] = v
    # orgs：hub 有则用 hub，hub 为空则用 REST 兜底
    if not hub_data.get("orgs") and rest_data.get("orgs"):
        merged["orgs"] = rest_data["orgs"]
    # bio / location / website / 活跃度统计 只来自 REST（hub 库不提供）
    merged["bio"]             = rest_data.get("bio")
    merged["location"]        = rest_data.get("location")
    merged["website"]         = rest_data.get("website")
    merged["num_discussions"] = rest_data.get("num_discussions", 0) or 0
    merged["num_papers"]      = rest_data.get("num_papers", 0) or 0
    merged["num_upvotes"]     = rest_data.get("num_upvotes", 0) or 0
    merged["num_likes"]       = rest_data.get("num_likes", 0) or 0

    # ── 社交链接：从 HTML signup block 抓取（优先于 bio 正则提取）──
    signup = _fetch_signup_block(username, token)
    social = _build_social_urls(signup)
    # website 用 signup block 覆盖（signup 里的是主页，更准确）
    if social.get("website"):
        merged["website"] = social["website"]
    merged["twitter_url"] = social.get("twitter_url")
    merged["github_url"]  = social.get("github_url")
    merged["bluesky_url"] = social.get("bluesky_url")
    # linkedin_url：signup block 优先，其次 bio 正则提取
    if social.get("linkedin_url"):
        merged["linkedin_url"] = social["linkedin_url"]

    bio     = merged.get("bio")
    website = merged.get("website")
    contact = _extract_contact_urls(bio, website)
    affil   = _parse_affiliation(bio)

    # contact 里的 linkedin_url 只在 signup 没有时生效
    if not merged.get("linkedin_url") and contact.get("linkedin_url"):
        merged["linkedin_url"] = contact["linkedin_url"]
    merged["scholar_url"] = contact.get("scholar_url")

    return {**merged, **affil}


# ============ 并发补全 Profile ============

_HF_PROFILE_FIELDS = [
    "fullname", "bio", "location", "website", "is_pro",
    "num_followers", "num_following", "num_models", "num_datasets", "num_spaces",
    "num_discussions", "num_papers", "num_upvotes", "num_likes",
    "orgs", "account_created",
    "linkedin_url", "scholar_url", "affiliation_type", "employer",
    "twitter_url", "github_url", "bluesky_url",
]


def enrich_hf_contributors(
    rows: List[Dict[str, Any]],
    token: str,
    skip_usernames: Optional[set] = None,
    progress_cb: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """
    并发抓取每位贡献者的 HF Profile。
    skip_usernames：续传时跳过已有完整 Profile 的用户。
    progress_cb(done, total, rate_limiter)：进度回调（rate limit 等待期间每秒仍被调用）。
    """
    import concurrent.futures as cf
    skip = set(skip_usernames or [])
    out = [dict(r) for r in rows]
    username_to_row = {r["username"]: r for r in out if r.get("username") and r["username"] not in skip}

    rate_limiter = HfRateLimiter()
    total = len(username_to_row)
    done = 0

    with ThreadPoolExecutor(max_workers=HF_CONCURRENCY) as ex:
        future_to_uname = {
            ex.submit(fetch_hf_user_profile, uname, token, rate_limiter): uname
            for uname in username_to_row.keys()
        }
        pending = set(future_to_uname.keys())

        while pending:
            finished, pending = cf.wait(pending, timeout=1.0)
            for fut in finished:
                uname = future_to_uname[fut]
                try:
                    profile = fut.result()
                except Exception as e:
                    _log(f"[WARN] Failed to fetch HF profile for {uname}: {e}", file=sys.stderr)
                    profile = None
                if profile:
                    username_to_row[uname].update(profile)
                done += 1
            # 无论是否有完成的 future，每秒都上报进度（rate limit 等待期间也能更新 UI）
            if progress_cb:
                progress_cb(done, total, rate_limiter)

    return out


# ============ 速率限制状态查询（供 UI 侧边栏使用）============
def fetch_hf_rate_limit_status(token: str) -> Dict[str, Any]:
    """
    发一个轻量请求并从响应头中读取当前限速状态。
    返回 {"remaining": int, "reset_in": int, "limit": int}
    """
    url = f"{HF_BASE}/api/users/huggingface/overview"
    headers = {"User-Agent": "HFContribExport/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        header = resp.headers.get("RateLimit", "")
        r_match = re.search(r'r=(\d+)', header)
        t_match = re.search(r't=(\d+)', header)
        policy = resp.headers.get("RateLimit-Policy", "")
        q_match = re.search(r'q=(\d+)', policy)
        remaining = int(r_match.group(1)) if r_match else None
        reset_in = int(t_match.group(1)) if t_match else None
        limit = int(q_match.group(1)) if q_match else None
        return {"remaining": remaining, "reset_in": reset_in, "limit": limit}
    except Exception:
        return {"remaining": None, "reset_in": None, "limit": None}


# ============ 组织（Org）相关 ============

def parse_hf_org(raw: str) -> Optional[str]:
    """
    将输入解析为 org name。
    支持：
      - "NanyangTechnologicalUniversity"
      - "https://huggingface.co/NanyangTechnologicalUniversity"
    返回 None 表示格式无效或明显是 model/dataset/space 路径。
    """
    s = raw.strip().rstrip("/")
    if s.startswith("http"):
        if "huggingface.co/" not in s:
            return None
        path = s.split("huggingface.co/", 1)[-1]
        parts = path.split("/")
        # datasets/xxx/yyy 或 spaces/xxx/yyy 或 xxx/yyy 都不是 org
        if parts[0] in ("datasets", "spaces", "models"):
            return None
        # org URL 只有一段，如 /NanyangTechnologicalUniversity
        if len(parts) >= 1 and parts[0]:
            return parts[0]
        return None
    # 纯名称：不含斜杠
    if "/" not in s and s:
        return s
    return None


def fetch_hf_org_overview(org_name: str, token: str) -> Optional[Dict[str, Any]]:
    """GET /api/organizations/{org_name}/overview"""
    url = f"{HF_BASE}/api/organizations/{org_name}/overview"
    resp = _hf_request(url, token)
    if not resp:
        return None
    data = resp.json()
    return {
        "name": org_name,
        "fullname": data.get("fullname"),
        "avatar_url": data.get("avatarUrl"),
        "is_verified": bool(data.get("isVerified", False)),
        "num_members": data.get("numUsers", 0),
        "num_models": data.get("numModels", 0),
        "num_datasets": data.get("numDatasets", 0),
        "num_spaces": data.get("numSpaces", 0),
        "num_papers": data.get("numPapers", 0),
        "num_followers": data.get("numFollowers", 0),
    }


def fetch_hf_org_members(org_name: str, token: str) -> List[Dict[str, Any]]:
    """
    获取组织全部成员，无人数上限。

    优先使用 huggingface_hub 库（内部走不同接口，可突破 REST API 的 500 人硬上限）。
    若库不可用则回退到 REST API（最多 500 人）。
    """
    try:
        return _fetch_org_members_hub(org_name, token)
    except Exception as e:
        _log(f"[WARN] huggingface_hub fallback to REST API for {org_name}: {e}", file=sys.stderr)
        return _fetch_org_members_rest(org_name, token)


def _fetch_org_members_hub(org_name: str, token: str) -> List[Dict[str, Any]]:
    """使用 huggingface_hub.list_organization_members 获取全量成员。"""
    from huggingface_hub import list_organization_members
    members = []
    for m in list_organization_members(org_name, token=token or None):
        avatar = getattr(m, "avatar_url", "") or ""
        if avatar and avatar.startswith("/"):
            avatar = f"{HF_BASE}{avatar}"
        uname = getattr(m, "username", None)
        if not uname:
            continue
        members.append({
            "username": uname,
            "fullname": getattr(m, "fullname", None),
            "is_pro": bool(getattr(m, "is_pro", False)),
            "avatar_url": avatar,
            "member_type": getattr(m, "user_type", "user"),
            "profile_url": f"{HF_BASE}/{uname}",
        })
    return members


def _fetch_org_members_rest(org_name: str, token: str) -> List[Dict[str, Any]]:
    """REST API 回退：最多 500 人（HF 服务端硬上限）。"""
    url = f"{HF_BASE}/api/organizations/{org_name}/members"
    resp = _hf_request(url, token)
    if not resp:
        return []
    data = resp.json()
    if not isinstance(data, list):
        return []
    members = []
    for m in data:
        uname = m.get("user") or m.get("username")
        if not uname:
            continue
        avatar = m.get("avatarUrl", "")
        if avatar and avatar.startswith("/"):
            avatar = f"{HF_BASE}{avatar}"
        members.append({
            "username": uname,
            "fullname": m.get("fullname"),
            "is_pro": bool(m.get("isPro", False)),
            "avatar_url": avatar,
            "member_type": m.get("type", "user"),
            "profile_url": f"{HF_BASE}/{uname}",
        })
    return members


def enrich_hf_org_members(
    members: List[Dict[str, Any]],
    token: str,
    skip_usernames: Optional[set] = None,
    progress_cb: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """
    并发抓取每位成员的完整 HF Profile。
    与 enrich_hf_contributors 逻辑完全对称（含 rate limit 等待期间每秒上报进度）。
    """
    import concurrent.futures as cf
    skip = set(skip_usernames or [])
    out = [dict(m) for m in members]
    username_to_row = {m["username"]: m for m in out if m.get("username") and m["username"] not in skip}

    rate_limiter = HfRateLimiter()
    total = len(username_to_row)
    done = 0

    with ThreadPoolExecutor(max_workers=HF_CONCURRENCY) as ex:
        future_to_uname = {
            ex.submit(fetch_hf_user_profile, uname, token, rate_limiter): uname
            for uname in username_to_row.keys()
        }
        pending = set(future_to_uname.keys())

        while pending:
            finished, pending = cf.wait(pending, timeout=1.0)
            for fut in finished:
                uname = future_to_uname[fut]
                try:
                    profile = fut.result()
                except Exception as e:
                    _log(f"[WARN] Failed to fetch HF profile for {uname}: {e}", file=sys.stderr)
                    profile = None
                if profile:
                    username_to_row[uname].update(profile)
                done += 1
            if progress_cb:
                progress_cb(done, total, rate_limiter)

    return out
