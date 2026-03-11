"""
后台任务管理器。

模块级 _jobs 字典在整个 Streamlit 进程生命周期内持久存在，
即使用户切换页面或关闭浏览器，后台线程仍在运行并持续更新状态。
"""
import threading
import uuid
from typing import Dict, Any, Optional

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def create_job(repo: str, job_type: str = "proj") -> str:
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        _jobs[job_id] = {
            "repo": repo,
            "job_type": job_type,   # "proj" | "org"
            "status": "running",    # running | complete | error
            "phase": "starting",    # starting | repo_info | contributors | stats | merging | enriching | saving
            "done": 0,
            "total": 0,
            "contrib_count": 0,
            "skip_count": 0,
            "rl_status": "normal",
            "rl_remaining": 5000,
            "rl_wait_s": 0,
            "rl_wait_until": 0.0,
            "details": None,
            "error": None,
        }
    return job_id


def update_job(job_id: str, **kwargs):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def get_job(job_id: str) -> Optional[Dict]:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def get_active_job_for_repo(repo: str) -> Optional[str]:
    """返回当前正在运行的同仓库任务 ID（若有），用于断线重连。"""
    with _lock:
        for jid, j in _jobs.items():
            if j["repo"] == repo and j["status"] == "running":
                return jid
    return None


def list_running_jobs(job_type: Optional[str] = None) -> list:
    """
    返回所有运行中任务的 job_id 列表。
    job_type 为 None 时返回全部，否则按 job_type 过滤（"proj" | "org"）。
    用于页面刷新后自动恢复进度显示。
    """
    with _lock:
        return [
            jid for jid, j in _jobs.items()
            if j["status"] == "running"
            and (job_type is None or j.get("job_type") == job_type)
        ]


def finish_job(job_id: str, error: str = None):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "error" if error else "complete"
            _jobs[job_id]["phase"] = "error" if error else "complete"
            _jobs[job_id]["error"] = error


def cleanup_job(job_id: str):
    with _lock:
        _jobs.pop(job_id, None)
