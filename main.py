import os
import csv
import time
import sys
import threading
from typing import List, Dict, Any, Optional, Callable
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ============ 配置 ============
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
USER_DETAILS_CONCURRENCY = 4   # 降低并发，减少触发 secondary rate limit


# ============ 限速管理器 ============
class RateLimiter:
    """
    线程安全的限速管理器。
    - remaining < 200  → 降速模式（请求间加延迟）
    - remaining == 0   → 暂停所有线程，等待 reset 后自动恢复
    - rate limit 等待不消耗 retry 次数
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sleep_lock = threading.Lock()   # 保证只有一个线程执行 sleep
        self._resume = threading.Event()
        self._resume.set()                    # 初始未暂停
        self.remaining: int = 5000
        self.reset_at: float = 0.0
        self.status: str = "normal"           # "normal" | "slow" | "paused"

    def record(self, resp: Optional[requests.Response]):
        """从响应头读取限速状态并更新。"""
        if resp is None:
            return
        try:
            remaining = int(resp.headers.get("X-RateLimit-Remaining", -1))
            reset_at = float(resp.headers.get("X-RateLimit-Reset", 0))
        except (ValueError, TypeError):
            return
        if remaining < 0:
            return
        with self._lock:
            self.remaining = remaining
            self.reset_at = reset_at
            if remaining == 0:
                self.status = "paused"
                self._resume.clear()
            elif remaining < 200:
                self.status = "slow"
                self._resume.set()
            else:
                self.status = "normal"
                self._resume.set()

    def pause(self, reset_ts: float):
        """从 403 响应中显式触发暂停。"""
        with self._lock:
            self.remaining = 0
            self.reset_at = reset_ts
            self.status = "paused"
            self._resume.clear()

    def wait_if_needed(self):
        """若处于暂停状态，阻塞当前线程直到限速重置。只有一个线程负责 sleep，其余等待 Event。"""
        if self._resume.is_set():
            return
        with self._sleep_lock:
            if self._resume.is_set():   # double-check
                return
            wait_s = max(5, int(self.reset_at - time.time()) + 2)
            _log(f"[WARN] Rate limit: pausing all threads for {wait_s}s, then auto-resume...")
            time.sleep(wait_s)
            with self._lock:
                self.remaining = 5000
                self.status = "normal"
                self._resume.set()
        self._resume.wait()             # 其余线程在此等待

    @property
    def request_delay(self) -> float:
        """降速模式下每次请求额外等待的秒数。"""
        return 1.0 if self.status == "slow" else 0.0

    def wait_remaining_seconds(self) -> int:
        return max(0, int(self.reset_at - time.time()) + 1)

CSV_FIELDS = [
    "rank",
    "login", "user_id", "name", "company", "location", "email", "blog", "twitter_username", "hireable",
    "public_repos", "public_gists", "followers", "following",
    "total_commits", "total_additions", "total_deletions", "net_lines", "total_changes",
    "avg_changes_per_commit", "addition_deletion_ratio",
    "contributions_on_default_branch",
    "profile_url", "avatar_url", "account_created", "last_updated",
]

# ============ 基础模块 ============
def _log(msg: str, file=sys.stdout):
    """统一的日志输出函数。"""
    print(msg, file=file)

def _make_request(
    url: str,
    token: str,
    timeout: int = REQUEST_TIMEOUT,
    rate_limiter: Optional[RateLimiter] = None,
) -> Optional[requests.Response]:
    """
    发起健壮的 API 请求。
    - Rate limit 等待不消耗 retry 次数（while 循环分离两类错误）
    - 若传入 rate_limiter，请求前先等待限速恢复，响应后更新状态
    """
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitHubContribExport/full-1.0",
    }
    error_count = 0
    while error_count < MAX_RETRIES:
        # 若全局限速暂停，阻塞等待恢复
        if rate_limiter:
            rate_limiter.wait_if_needed()
            if rate_limiter.request_delay:
                time.sleep(rate_limiter.request_delay)
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)

            if rate_limiter:
                rate_limiter.record(resp)

            # Rate limit 403：触发暂停并重试，不计入 error_count
            if resp.status_code == 403 and "rate limit" in (resp.text or "").lower():
                reset_ts = float(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                if rate_limiter:
                    rate_limiter.pause(reset_ts)
                else:
                    wait_s = max(5, int(reset_ts - time.time()) + 2)
                    _log(f"[WARN] Rate limit hit. Waiting {wait_s}s...")
                    time.sleep(wait_s)
                continue  # 不增加 error_count

            if resp.status_code == 404:
                return None

            if resp.status_code in (200, 202):
                return resp

            resp.raise_for_status()

        except requests.exceptions.RequestException as e:
            error_count += 1
            if error_count < MAX_RETRIES:
                time.sleep(2 ** (error_count - 1))
            else:
                _log(f"[ERROR] Request failed for {url}: {e}", file=sys.stderr)
                return None
    return None

def fetch_repo_details(repo: str, token: str) -> Optional[Dict[str, Any]]:
    """获取单个仓库的详细信息。"""
    url = f"https://api.github.com/repos/{repo}"
    resp = _make_request(url, token)
    if resp and resp.status_code == 200:
        return resp.json()
    return None

# ============ 分页抓取所有 contributors ============
def _parse_next_link(link_header: Optional[str]) -> Optional[str]:
    """从 Link 响应头里解析 rel=next 的 URL（若有）。"""
    if not link_header:
        return None
    parts = [p.strip() for p in link_header.split(",")]
    for p in parts:
        segs = p.split(";")
        if len(segs) < 2:
            continue
        url = segs[0].strip()
        rel = segs[1].strip()
        if rel.endswith('rel="next"') and url.startswith("<") and url.endswith(">"):
            return url[1:-1]
    return None

def fetch_all_contributors(repo: str, token: str, include_anon: bool) -> List[Dict[str, Any]]:
    """
    使用 /repos/{owner}/{repo}/contributors 做分页抓取全部贡献者。
    - per_page=100，直到没有 next 链接或返回空页
    - include_anon=True 时附加 anon=1 获取匿名贡献者（login 可能为空）
    """
    base = f"https://api.github.com/repos/{repo}/contributors?per_page=100"
    if include_anon:
        base += "&anon=1"
    url = base
    all_rows: List[Dict[str, Any]] = []

    # tqdm 在 Streamlit 环境中输出到 stderr，不影响功能但会产生日志噪音
    with tqdm(desc="分页抓取 contributors", unit="页", leave=True) as bar:
        while url:
            resp = _make_request(url, token)
            if not resp:
                _log("[ERROR] Failed to fetch contributors page.", file=sys.stderr)
                break
            page_data = resp.json() or []
            if not page_data:
                break
            all_rows.extend(page_data)
            bar.update(1)
            url = _parse_next_link(resp.headers.get("Link"))
    return all_rows

# ============ 统计端点（增删行等，不分页） ============
def poll_contributor_stats(repo: str, token: str, attempts: int = 7, backoff_base: int = 8) -> Optional[List[Dict[str, Any]]]:
    """
    轮询 /stats/contributors，直到 200 或超时；
    注意：该端点不分页，可能对超大仓库将 additions/deletions 置为 0（官方说明）。
    """
    url = f"https://api.github.com/repos/{repo}/stats/contributors"
    for _ in tqdm(range(attempts), desc="等待统计数据计算", unit="轮询", leave=True):
        resp = _make_request(url, token)
        if not resp:
            return None
        if resp.status_code == 200:
            return resp.json() or []
        if resp.status_code == 202:
            time.sleep(backoff_base)
            continue
        _log(f"[ERROR] Unexpected status from stats endpoint: {resp.status_code}", file=sys.stderr)
        return None
    _log("[ERROR] Stats computation timed out.", file=sys.stderr)
    return None

# ============ 用户详情 ============
def fetch_user_detail(
    username: str,
    token: str,
    rate_limiter: Optional[RateLimiter] = None,
) -> Optional[Dict[str, Any]]:
    """获取单个用户的详细 profile 信息。"""
    url = f"https://api.github.com/users/{username}"
    resp = _make_request(url, token, rate_limiter=rate_limiter)
    if resp and resp.status_code == 200:
        return resp.json()
    return None

# ============ 合并与整理 ============
def merge_contrib_and_stats(all_contribs: List[Dict[str, Any]], stats: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    - all_contribs：来自 /contributors 的完整分页数据（含 contributions）
    - stats：来自 /stats/contributors 的聚合周统计（含 additions/deletions/total/weeks）
    输出：标准化后的行，供后续补齐用户详情、导出 CSV
    """
    stats_by_login: Dict[str, Dict[str, Any]] = {}
    if stats:
        for it in stats:
            author = it.get("author") or {}
            login = author.get("login") if isinstance(author, dict) else None
            weeks = it.get("weeks") or []
            additions = sum(w.get("a", 0) for w in weeks)
            deletions = sum(w.get("d", 0) for w in weeks)
            total_commits = int(it.get("total") or 0)
            if login:
                stats_by_login[login] = {
                    "total_commits": total_commits,
                    "total_additions": additions,
                    "total_deletions": deletions,
                    "net_lines": additions - deletions,
                    "total_changes": additions + deletions,
                }

    rows: List[Dict[str, Any]] = []
    for c in all_contribs:
        login = c.get("login")
        if not login:  # 跳过没有 login 的匿名贡献者
            continue

        contrib_commits = int(c.get("contributions") or 0)
        base = {
            "login": login,
            "user_id": c.get("id"),
            "contributions_on_default_branch": contrib_commits,
            "profile_url": c.get("html_url"),
            "avatar_url": c.get("avatar_url"),
        }

        if login in stats_by_login:
            s = stats_by_login[login]
            base.update(s)
            commits = s.get("total_commits") or 0
            additions = s.get("total_additions") or 0
            deletions = s.get("total_deletions") or 0
            changes = s.get("total_changes") or 0
            base["avg_changes_per_commit"] = (changes / commits) if commits else 0.0
            base["addition_deletion_ratio"] = (additions / deletions) if deletions else None
        else:
            base["total_commits"] = contrib_commits

        rows.append(base)

    rows.sort(key=lambda x: (x.get("total_changes") or 0, x.get("total_commits") or 0), reverse=True)

    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return rows

def enrich_with_user_details(
    rows: List[Dict[str, Any]],
    token: str,
    skip_logins: set = None,
    progress_cb: Callable = None,
) -> List[Dict[str, Any]]:
    """并发补全用户 profile 字段。
    skip_logins 中的用户跳过 API 请求（续传用）。
    progress_cb(done, total, rate_limiter) 每完成一个用户后调用（可选，用于 UI 进度显示）。
    """
    skip = set(skip_logins or [])
    out = [dict(r) for r in rows]
    login_to_row_map = {r['login']: r for r in out if r.get('login') and r['login'] not in skip}

    rate_limiter = RateLimiter()
    total = len(login_to_row_map)
    done = 0

    with ThreadPoolExecutor(max_workers=USER_DETAILS_CONCURRENCY) as ex:
        futures = {
            ex.submit(fetch_user_detail, login, token, rate_limiter): login
            for login in login_to_row_map.keys()
        }

        for fut in tqdm(as_completed(futures), total=total, desc="抓取用户详细信息", unit="人", leave=True):
            login = futures[fut]
            try:
                detail = fut.result()
            except Exception as e:
                _log(f"[WARN] Failed to process details for {login}: {e}", file=sys.stderr)
                detail = None

            if detail:
                row = login_to_row_map[login]
                row.update({
                    "name": detail.get("name"), "email": detail.get("email"),
                    "location": detail.get("location"), "company": detail.get("company"),
                    "blog": detail.get("blog"), "bio": detail.get("bio"),
                    "twitter_username": detail.get("twitter_username"), "hireable": detail.get("hireable"),
                    "public_repos": detail.get("public_repos"), "public_gists": detail.get("public_gists"),
                    "followers": detail.get("followers"), "following": detail.get("following"),
                    "account_created": detail.get("created_at"), "last_updated": detail.get("updated_at"),
                })

            done += 1
            if progress_cb:
                progress_cb(done, total, rate_limiter)

    return out

# ============ 导出 CSV ============
def write_csv(rows: List[Dict[str, Any]], path: str):
    """将数据写入 CSV 文件。"""
    if not rows:
        _log("[WARN] No data to write to CSV.", file=sys.stderr)
        return
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except IOError as e:
        _log(f"[ERROR] Failed to write CSV file at {path}: {e}", file=sys.stderr)

# ============ 主流程 ============
def main():
    """主执行函数。"""
    if not GITHUB_TOKEN:
        _log("请设置 GITHUB_TOKEN 环境变量或在代码中直接赋值。", file=sys.stderr)
        sys.exit(1)

    repo_name = input("请输入要爬取的 GitHub 仓库 (格式: owner/repo): ").strip()
    if "/" not in repo_name or len(repo_name.split("/")) != 2:
        _log("仓库格式不正确，请使用 'owner/repo' 格式。", file=sys.stderr)
        sys.exit(1)

    # 1) 获取并显示仓库基本信息
    _log("\n" + "="*50)
    _log(f"正在获取仓库 '{repo_name}' 的信息...")
    details = fetch_repo_details(repo_name, GITHUB_TOKEN)
    if not details:
        _log(f"无法获取仓库 '{repo_name}' 的信息。请检查仓库名称是否正确，以及 Token 是否有权限访问。", file=sys.stderr)
        sys.exit(1)

    _log("仓库信息获取成功:")
    _log(f"  - 名称: {details.get('full_name')}")
    _log(f"  - 描述: {details.get('description')}")
    _log(f"  - 主页: {details.get('html_url')}")
    _log(f"  - 星标: {details.get('stargazers_count', 0)} | Forks: {details.get('forks_count', 0)} | Watchers: {details.get('subscribers_count', 0)}")
    _log(f"  - 主要语言: {details.get('language')}")
    _log("="*50 + "\n")

    # 2) 分页抓取"所有"贡献者（含 contributions 字段）
    _log("步骤 1/4: 开始分页抓取贡献者列表...")
    all_contribs = fetch_all_contributors(repo_name, GITHUB_TOKEN, include_anon=False)
    if not all_contribs:
        _log("未能从 API 获取任何贡献者数据。", file=sys.stderr)
        sys.exit(2)
    _log(f"初步获取到 {len(all_contribs)} 位贡献者。\n")

    # 3) 轮询 stats/contributors（增删行/周维度等）
    _log("步骤 2/4: 开始获取详细贡献统计（这可能需要一些时间）...")
    stats = poll_contributor_stats(repo_name, GITHUB_TOKEN)
    _log("详细统计数据获取完成。\n")

    # 4) 合并、排序、打 rank
    _log("步骤 3/4: 正在合并与整理数据...")
    merged = merge_contrib_and_stats(all_contribs, stats)
    _log("数据合并与排序完成。\n")

    # 5) 补齐用户详情
    _log("步骤 4/4: 开始并发抓取每位用户的详细 Profile 信息...")
    enriched = enrich_with_user_details(merged, GITHUB_TOKEN)
    _log("所有用户信息抓取完成。\n")

    # 6) 导出 CSV
    output_csv = f"contributors_{repo_name.replace('/', '_')}.csv"
    _log("="*50)
    _log("所有数据处理完毕，正在导出到 CSV 文件...")
    write_csv(enriched, output_csv)
    _log(f"✅ 成功！数据已保存到文件: {output_csv}")
    _log(f"共处理并导出了 {len(enriched)} 位贡献者的信息。")
    _log("="*50)

if __name__ == "__main__":
    main()
