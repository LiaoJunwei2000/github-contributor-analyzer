import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "contributors.db")

_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_database_url() -> str:
    url = ""
    try:
        import streamlit as st
        url = st.secrets.get("DATABASE_URL", "")
    except Exception:
        pass
    if not url:
        url = os.getenv("DATABASE_URL", "")
    return url


def _use_postgres() -> bool:
    return bool(_get_database_url())


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── PostgreSQL ────────────────────────────────────────────────
def _get_pg_pool():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pg_pool_lock:
        if _pg_pool is None:
            import psycopg2.pool
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                1, 10,
                _get_database_url(),
                sslmode="require",
            )
    return _pg_pool


@contextmanager
def _pg_cursor():
    import psycopg2.extras
    pool = _get_pg_pool()
    conn = pool.getconn()
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        pool.putconn(conn)


# ── SQLite ────────────────────────────────────────────────────
@contextmanager
def _sqlite_cursor():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _get_cursor():
    return _pg_cursor() if _use_postgres() else _sqlite_cursor()


# ── Schema ────────────────────────────────────────────────────
def init_db():
    if _use_postgres():
        _init_postgres()
    else:
        _init_sqlite()
    _migrate_hf_tables()


def _migrate_hf_tables():
    """为已有数据库的 hf_contributors / hf_org_members 表追加新列（幂等）。"""
    new_cols = [
        ("linkedin_url",     "TEXT"),
        ("scholar_url",      "TEXT"),
        ("affiliation_type", "TEXT"),
        ("employer",         "TEXT"),
        ("num_discussions",  "INTEGER DEFAULT 0"),
        ("num_papers",       "INTEGER DEFAULT 0"),
        ("num_upvotes",      "INTEGER DEFAULT 0"),
        ("num_likes",        "INTEGER DEFAULT 0"),
        ("twitter_url",      "TEXT"),
        ("github_url",       "TEXT"),
        ("bluesky_url",      "TEXT"),
    ]
    tables = ["hf_contributors", "hf_org_members"]
    with _get_cursor() as cur:
        for table in tables:
            for col, col_type in new_cols:
                try:
                    if _use_postgres():
                        cur.execute(
                            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
                        )
                    else:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                except Exception:
                    pass  # 列已存在时 SQLite 会报错，忽略即可


def _init_postgres():
    with _pg_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hf_orgs (
                id           SERIAL PRIMARY KEY,
                name         TEXT UNIQUE NOT NULL,
                fullname     TEXT,
                avatar_url   TEXT,
                is_verified  INTEGER DEFAULT 0,
                num_members  INTEGER DEFAULT 0,
                num_models   INTEGER DEFAULT 0,
                num_datasets INTEGER DEFAULT 0,
                num_spaces   INTEGER DEFAULT 0,
                num_papers   INTEGER DEFAULT 0,
                num_followers INTEGER DEFAULT 0,
                scraped_at   TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hf_org_members (
                id               SERIAL PRIMARY KEY,
                org_name         TEXT NOT NULL,
                username         TEXT NOT NULL,
                fullname         TEXT,
                member_type      TEXT,
                is_pro           INTEGER DEFAULT 0,
                avatar_url       TEXT,
                bio              TEXT,
                location         TEXT,
                website          TEXT,
                num_followers    INTEGER DEFAULT 0,
                num_following    INTEGER DEFAULT 0,
                num_models       INTEGER DEFAULT 0,
                num_datasets     INTEGER DEFAULT 0,
                num_spaces       INTEGER DEFAULT 0,
                orgs             TEXT,
                profile_url      TEXT,
                account_created  TEXT,
                scraped_at       TEXT,
                linkedin_url     TEXT,
                scholar_url      TEXT,
                affiliation_type TEXT,
                employer         TEXT,
                num_discussions  INTEGER DEFAULT 0,
                num_papers       INTEGER DEFAULT 0,
                num_upvotes      INTEGER DEFAULT 0,
                num_likes        INTEGER DEFAULT 0,
                twitter_url      TEXT,
                github_url       TEXT,
                bluesky_url      TEXT,
                UNIQUE(org_name, username)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hf_repos (
                id            SERIAL PRIMARY KEY,
                full_name     TEXT UNIQUE NOT NULL,
                hf_type       TEXT NOT NULL,
                description   TEXT,
                author        TEXT,
                likes         INTEGER DEFAULT 0,
                downloads     INTEGER DEFAULT 0,
                pipeline_tag  TEXT,
                library_name  TEXT,
                tags          TEXT,
                license       TEXT,
                gated         TEXT,
                created_at    TEXT,
                last_modified TEXT,
                sha           TEXT,
                scraped_at    TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hf_contributors (
                id               SERIAL PRIMARY KEY,
                repo_full_name   TEXT NOT NULL,
                hf_type          TEXT NOT NULL,
                rank             INTEGER,
                username         TEXT NOT NULL,
                fullname         TEXT,
                bio              TEXT,
                location         TEXT,
                website          TEXT,
                avatar_url       TEXT,
                is_pro           INTEGER DEFAULT 0,
                num_followers    INTEGER DEFAULT 0,
                num_following    INTEGER DEFAULT 0,
                num_models       INTEGER DEFAULT 0,
                num_datasets     INTEGER DEFAULT 0,
                num_spaces       INTEGER DEFAULT 0,
                orgs             TEXT,
                total_commits    INTEGER DEFAULT 0,
                first_commit_at  TEXT,
                last_commit_at   TEXT,
                profile_url      TEXT,
                account_created  TEXT,
                scraped_at       TEXT,
                linkedin_url     TEXT,
                scholar_url      TEXT,
                affiliation_type TEXT,
                employer         TEXT,
                num_discussions  INTEGER DEFAULT 0,
                num_papers       INTEGER DEFAULT 0,
                num_upvotes      INTEGER DEFAULT 0,
                num_likes        INTEGER DEFAULT 0,
                twitter_url      TEXT,
                github_url       TEXT,
                bluesky_url      TEXT,
                UNIQUE(repo_full_name, username)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id    SERIAL PRIMARY KEY,
                name  TEXT UNIQUE NOT NULL,
                color TEXT DEFAULT '#6B6B6B'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS repo_tags (
                repo_full_name TEXT NOT NULL,
                tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (repo_full_name, tag_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS repos (
                id          SERIAL PRIMARY KEY,
                full_name   TEXT UNIQUE NOT NULL,
                description TEXT,
                stars       INTEGER,
                forks       INTEGER,
                watchers    INTEGER,
                language    TEXT,
                scraped_at  TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contributors (
                id                              SERIAL PRIMARY KEY,
                repo_full_name                  TEXT NOT NULL,
                rank                            INTEGER,
                login                           TEXT NOT NULL,
                user_id                         INTEGER,
                name                            TEXT,
                company                         TEXT,
                location                        TEXT,
                email                           TEXT,
                blog                            TEXT,
                twitter_username                TEXT,
                hireable                        INTEGER,
                bio                             TEXT,
                public_repos                    INTEGER,
                public_gists                    INTEGER,
                followers                       INTEGER,
                following                       INTEGER,
                total_commits                   INTEGER,
                total_additions                 INTEGER,
                total_deletions                 INTEGER,
                net_lines                       INTEGER,
                total_changes                   INTEGER,
                avg_changes_per_commit          REAL,
                addition_deletion_ratio         REAL,
                contributions_on_default_branch INTEGER,
                profile_url                     TEXT,
                avatar_url                      TEXT,
                account_created                 TEXT,
                last_updated                    TEXT,
                scraped_at                      TEXT,
                UNIQUE(repo_full_name, login)
            )
        """)


def _init_sqlite():
    with _sqlite_cursor() as cur:
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS hf_orgs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT UNIQUE NOT NULL,
                fullname      TEXT,
                avatar_url    TEXT,
                is_verified   INTEGER DEFAULT 0,
                num_members   INTEGER DEFAULT 0,
                num_models    INTEGER DEFAULT 0,
                num_datasets  INTEGER DEFAULT 0,
                num_spaces    INTEGER DEFAULT 0,
                num_papers    INTEGER DEFAULT 0,
                num_followers INTEGER DEFAULT 0,
                scraped_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS hf_org_members (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                org_name         TEXT NOT NULL,
                username         TEXT NOT NULL,
                fullname         TEXT,
                member_type      TEXT,
                is_pro           INTEGER DEFAULT 0,
                avatar_url       TEXT,
                bio              TEXT,
                location         TEXT,
                website          TEXT,
                num_followers    INTEGER DEFAULT 0,
                num_following    INTEGER DEFAULT 0,
                num_models       INTEGER DEFAULT 0,
                num_datasets     INTEGER DEFAULT 0,
                num_spaces       INTEGER DEFAULT 0,
                orgs             TEXT,
                profile_url      TEXT,
                account_created  TEXT,
                scraped_at       TEXT,
                linkedin_url     TEXT,
                scholar_url      TEXT,
                affiliation_type TEXT,
                employer         TEXT,
                num_discussions  INTEGER DEFAULT 0,
                num_papers       INTEGER DEFAULT 0,
                num_upvotes      INTEGER DEFAULT 0,
                num_likes        INTEGER DEFAULT 0,
                twitter_url      TEXT,
                github_url       TEXT,
                bluesky_url      TEXT,
                UNIQUE(org_name, username)
            );
            CREATE TABLE IF NOT EXISTS hf_repos (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name     TEXT UNIQUE NOT NULL,
                hf_type       TEXT NOT NULL,
                description   TEXT,
                author        TEXT,
                likes         INTEGER DEFAULT 0,
                downloads     INTEGER DEFAULT 0,
                pipeline_tag  TEXT,
                library_name  TEXT,
                tags          TEXT,
                license       TEXT,
                gated         TEXT,
                created_at    TEXT,
                last_modified TEXT,
                sha           TEXT,
                scraped_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS hf_contributors (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full_name   TEXT NOT NULL,
                hf_type          TEXT NOT NULL,
                rank             INTEGER,
                username         TEXT NOT NULL,
                fullname         TEXT,
                bio              TEXT,
                location         TEXT,
                website          TEXT,
                avatar_url       TEXT,
                is_pro           INTEGER DEFAULT 0,
                num_followers    INTEGER DEFAULT 0,
                num_following    INTEGER DEFAULT 0,
                num_models       INTEGER DEFAULT 0,
                num_datasets     INTEGER DEFAULT 0,
                num_spaces       INTEGER DEFAULT 0,
                orgs             TEXT,
                total_commits    INTEGER DEFAULT 0,
                first_commit_at  TEXT,
                last_commit_at   TEXT,
                profile_url      TEXT,
                account_created  TEXT,
                scraped_at       TEXT,
                linkedin_url     TEXT,
                scholar_url      TEXT,
                affiliation_type TEXT,
                employer         TEXT,
                num_discussions  INTEGER DEFAULT 0,
                num_papers       INTEGER DEFAULT 0,
                num_upvotes      INTEGER DEFAULT 0,
                num_likes        INTEGER DEFAULT 0,
                twitter_url      TEXT,
                github_url       TEXT,
                bluesky_url      TEXT,
                UNIQUE(repo_full_name, username)
            );
            CREATE TABLE IF NOT EXISTS tags (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT UNIQUE NOT NULL,
                color TEXT DEFAULT '#6B6B6B'
            );
            CREATE TABLE IF NOT EXISTS repo_tags (
                repo_full_name TEXT NOT NULL,
                tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (repo_full_name, tag_id)
            );
            CREATE TABLE IF NOT EXISTS repos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name   TEXT UNIQUE NOT NULL,
                description TEXT,
                stars       INTEGER,
                forks       INTEGER,
                watchers    INTEGER,
                language    TEXT,
                scraped_at  TEXT
            );
            CREATE TABLE IF NOT EXISTS contributors (
                id                              INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full_name                  TEXT NOT NULL,
                rank                            INTEGER,
                login                           TEXT NOT NULL,
                user_id                         INTEGER,
                name                            TEXT,
                company                         TEXT,
                location                        TEXT,
                email                           TEXT,
                blog                            TEXT,
                twitter_username                TEXT,
                hireable                        INTEGER,
                bio                             TEXT,
                public_repos                    INTEGER,
                public_gists                    INTEGER,
                followers                       INTEGER,
                following                       INTEGER,
                total_commits                   INTEGER,
                total_additions                 INTEGER,
                total_deletions                 INTEGER,
                net_lines                       INTEGER,
                total_changes                   INTEGER,
                avg_changes_per_commit          REAL,
                addition_deletion_ratio         REAL,
                contributions_on_default_branch INTEGER,
                profile_url                     TEXT,
                avatar_url                      TEXT,
                account_created                 TEXT,
                last_updated                    TEXT,
                scraped_at                      TEXT,
                UNIQUE(repo_full_name, login)
            );
        """)


# ── CRUD ──────────────────────────────────────────────────────
def _ph():
    """SQL placeholder: %s for postgres, ? for sqlite."""
    return "%s" if _use_postgres() else "?"


def save_repo(details: Dict[str, Any]):
    p = _ph()
    with _get_cursor() as cur:
        if _use_postgres():
            cur.execute(f"""
                INSERT INTO repos (full_name, description, stars, forks, watchers, language, scraped_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p})
                ON CONFLICT (full_name) DO UPDATE SET
                    description = EXCLUDED.description,
                    stars       = EXCLUDED.stars,
                    forks       = EXCLUDED.forks,
                    watchers    = EXCLUDED.watchers,
                    language    = EXCLUDED.language,
                    scraped_at  = EXCLUDED.scraped_at
            """, (
                details.get("full_name"), details.get("description"),
                details.get("stargazers_count"), details.get("forks_count"),
                details.get("subscribers_count"), details.get("language"), _now(),
            ))
        else:
            cur.execute(f"""
                INSERT OR REPLACE INTO repos
                    (full_name, description, stars, forks, watchers, language, scraped_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p})
            """, (
                details.get("full_name"), details.get("description"),
                details.get("stargazers_count"), details.get("forks_count"),
                details.get("subscribers_count"), details.get("language"), _now(),
            ))


def save_contributors(repo_full_name: str, contributors: List[Dict[str, Any]]):
    if not contributors:
        return
    p = _ph()
    placeholders = ",".join([p] * 29)
    now = _now()
    all_vals = [
        (
            repo_full_name,
            c.get("rank"), c.get("login"), c.get("user_id"),
            c.get("name"), c.get("company"), c.get("location"),
            c.get("email"), c.get("blog"), c.get("twitter_username"),
            1 if c.get("hireable") else 0,
            c.get("bio"), c.get("public_repos"), c.get("public_gists"),
            c.get("followers"), c.get("following"),
            c.get("total_commits"), c.get("total_additions"), c.get("total_deletions"),
            c.get("net_lines"), c.get("total_changes"),
            c.get("avg_changes_per_commit"), c.get("addition_deletion_ratio"),
            c.get("contributions_on_default_branch"),
            c.get("profile_url"), c.get("avatar_url"),
            c.get("account_created"), c.get("last_updated"), now,
        )
        for c in contributors
    ]
    with _get_cursor() as cur:
        if _use_postgres():
            import psycopg2.extras
            psycopg2.extras.execute_batch(cur, f"""
                INSERT INTO contributors (
                    repo_full_name, rank, login, user_id, name, company, location,
                    email, blog, twitter_username, hireable, bio,
                    public_repos, public_gists, followers, following,
                    total_commits, total_additions, total_deletions,
                    net_lines, total_changes, avg_changes_per_commit,
                    addition_deletion_ratio, contributions_on_default_branch,
                    profile_url, avatar_url, account_created, last_updated, scraped_at
                ) VALUES ({placeholders})
                ON CONFLICT (repo_full_name, login) DO UPDATE SET
                    rank=EXCLUDED.rank, user_id=EXCLUDED.user_id, name=EXCLUDED.name,
                    company=EXCLUDED.company, location=EXCLUDED.location, email=EXCLUDED.email,
                    blog=EXCLUDED.blog, twitter_username=EXCLUDED.twitter_username,
                    hireable=EXCLUDED.hireable, bio=EXCLUDED.bio,
                    public_repos=EXCLUDED.public_repos, public_gists=EXCLUDED.public_gists,
                    followers=EXCLUDED.followers, following=EXCLUDED.following,
                    total_commits=EXCLUDED.total_commits, total_additions=EXCLUDED.total_additions,
                    total_deletions=EXCLUDED.total_deletions, net_lines=EXCLUDED.net_lines,
                    total_changes=EXCLUDED.total_changes,
                    avg_changes_per_commit=EXCLUDED.avg_changes_per_commit,
                    addition_deletion_ratio=EXCLUDED.addition_deletion_ratio,
                    contributions_on_default_branch=EXCLUDED.contributions_on_default_branch,
                    profile_url=EXCLUDED.profile_url, avatar_url=EXCLUDED.avatar_url,
                    account_created=EXCLUDED.account_created, last_updated=EXCLUDED.last_updated,
                    scraped_at=EXCLUDED.scraped_at
            """, all_vals, page_size=200)
        else:
            cur.executemany(f"""
                INSERT OR REPLACE INTO contributors (
                    repo_full_name, rank, login, user_id, name, company, location,
                    email, blog, twitter_username, hireable, bio,
                    public_repos, public_gists, followers, following,
                    total_commits, total_additions, total_deletions,
                    net_lines, total_changes, avg_changes_per_commit,
                    addition_deletion_ratio, contributions_on_default_branch,
                    profile_url, avatar_url, account_created, last_updated, scraped_at
                ) VALUES ({placeholders})
            """, all_vals)


def get_complete_profiles(repo_full_name: str) -> Dict[str, Dict]:
    """返回该仓库已有完整 Profile 的贡献者（followers 不为空），用于续传跳过。"""
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM contributors WHERE repo_full_name = {p} AND followers IS NOT NULL",
            (repo_full_name,),
        )
        return {row["login"]: dict(row) for row in cur.fetchall()}


def list_repos() -> List[Dict]:
    with _get_cursor() as cur:
        cur.execute("SELECT * FROM repos ORDER BY scraped_at DESC")
        return [dict(r) for r in cur.fetchall()]


def get_contributors(repo_full_name: str) -> List[Dict]:
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM contributors WHERE repo_full_name = {p} ORDER BY rank ASC",
            (repo_full_name,),
        )
        return [dict(r) for r in cur.fetchall()]


def delete_repo(repo_full_name: str):
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(f"DELETE FROM contributors WHERE repo_full_name = {p}", (repo_full_name,))
        cur.execute(f"DELETE FROM repo_tags WHERE repo_full_name = {p}", (repo_full_name,))
        cur.execute(f"DELETE FROM repos WHERE full_name = {p}", (repo_full_name,))


# ── Tag CRUD ───────────────────────────────────────────────
def create_tag(name: str, color: str = "#6B6B6B") -> Dict:
    p = _ph()
    with _get_cursor() as cur:
        if _use_postgres():
            cur.execute(
                f"INSERT INTO tags (name, color) VALUES ({p},{p}) ON CONFLICT (name) DO NOTHING RETURNING *",
                (name, color),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute(f"SELECT * FROM tags WHERE name = {p}", (name,))
            return dict(cur.fetchone())
        else:
            cur.execute(
                f"INSERT OR IGNORE INTO tags (name, color) VALUES ({p},{p})",
                (name, color),
            )
            cur.execute(f"SELECT * FROM tags WHERE name = {p}", (name,))
            return dict(cur.fetchone())


def list_tags() -> List[Dict]:
    with _get_cursor() as cur:
        cur.execute("SELECT * FROM tags ORDER BY name ASC")
        return [dict(r) for r in cur.fetchall()]


def update_tag(tag_id: int, name: str = None, color: str = None):
    p = _ph()
    updates = []
    vals = []
    if name is not None:
        updates.append(f"name = {p}")
        vals.append(name)
    if color is not None:
        updates.append(f"color = {p}")
        vals.append(color)
    if not updates:
        return
    vals.append(tag_id)
    with _get_cursor() as cur:
        cur.execute(f"UPDATE tags SET {', '.join(updates)} WHERE id = {p}", vals)


def delete_tag(tag_id: int):
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(f"DELETE FROM repo_tags WHERE tag_id = {p}", (tag_id,))
        cur.execute(f"DELETE FROM tags WHERE id = {p}", (tag_id,))


def get_repo_tags(repo_full_name: str) -> List[Dict]:
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT t.* FROM tags t JOIN repo_tags rt ON t.id = rt.tag_id WHERE rt.repo_full_name = {p} ORDER BY t.name",
            (repo_full_name,),
        )
        return [dict(r) for r in cur.fetchall()]


def add_repo_tag(repo_full_name: str, tag_id: int):
    p = _ph()
    with _get_cursor() as cur:
        if _use_postgres():
            cur.execute(
                f"INSERT INTO repo_tags (repo_full_name, tag_id) VALUES ({p},{p}) ON CONFLICT DO NOTHING",
                (repo_full_name, tag_id),
            )
        else:
            cur.execute(
                f"INSERT OR IGNORE INTO repo_tags (repo_full_name, tag_id) VALUES ({p},{p})",
                (repo_full_name, tag_id),
            )


def remove_repo_tag(repo_full_name: str, tag_id: int):
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"DELETE FROM repo_tags WHERE repo_full_name = {p} AND tag_id = {p}",
            (repo_full_name, tag_id),
        )


def get_all_repo_tags() -> Dict[str, List[Dict]]:
    """返回所有仓库的标签映射 {repo_full_name: [tag_dict, ...]}，一次查询。"""
    with _get_cursor() as cur:
        cur.execute("""
            SELECT rt.repo_full_name, t.id, t.name, t.color
            FROM tags t JOIN repo_tags rt ON t.id = rt.tag_id
            ORDER BY rt.repo_full_name, t.name
        """)
        result: Dict[str, List[Dict]] = {}
        for row in cur.fetchall():
            r = dict(row)
            rname = r.pop("repo_full_name")
            result.setdefault(rname, []).append(r)
        return result


def get_repos_by_tags(tag_ids: List[int]) -> List[str]:
    """返回拥有所有指定标签之一的仓库名称列表（OR 逻辑）。"""
    if not tag_ids:
        return []
    p = _ph()
    placeholders = ",".join([p] * len(tag_ids))
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT repo_full_name FROM repo_tags WHERE tag_id IN ({placeholders})",
            tag_ids,
        )
        return [r["repo_full_name"] for r in cur.fetchall()]


# ── HF CRUD ────────────────────────────────────────────────────

def save_hf_repo(details: Dict[str, Any]):
    """Upsert 一条 HF 项目记录。"""
    import json
    p = _ph()
    tags_json = json.dumps(details.get("tags") or [], ensure_ascii=False)
    vals = (
        details.get("full_name"),
        details.get("hf_type"),
        details.get("description"),
        details.get("author"),
        details.get("likes", 0),
        details.get("downloads", 0),
        details.get("pipeline_tag"),
        details.get("library_name"),
        tags_json,
        details.get("license"),
        str(details.get("gated", "false")),
        details.get("created_at"),
        details.get("last_modified"),
        details.get("sha"),
        _now(),
    )
    with _get_cursor() as cur:
        if _use_postgres():
            cur.execute(f"""
                INSERT INTO hf_repos
                    (full_name, hf_type, description, author, likes, downloads,
                     pipeline_tag, library_name, tags, license, gated,
                     created_at, last_modified, sha, scraped_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
                ON CONFLICT (full_name) DO UPDATE SET
                    hf_type=EXCLUDED.hf_type, description=EXCLUDED.description,
                    author=EXCLUDED.author, likes=EXCLUDED.likes,
                    downloads=EXCLUDED.downloads, pipeline_tag=EXCLUDED.pipeline_tag,
                    library_name=EXCLUDED.library_name, tags=EXCLUDED.tags,
                    license=EXCLUDED.license, gated=EXCLUDED.gated,
                    created_at=EXCLUDED.created_at, last_modified=EXCLUDED.last_modified,
                    sha=EXCLUDED.sha, scraped_at=EXCLUDED.scraped_at
            """, vals)
        else:
            cur.execute(f"""
                INSERT OR REPLACE INTO hf_repos
                    (full_name, hf_type, description, author, likes, downloads,
                     pipeline_tag, library_name, tags, license, gated,
                     created_at, last_modified, sha, scraped_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
            """, vals)


def save_hf_contributors(repo_full_name: str, hf_type: str, contributors: List[Dict[str, Any]]):
    """批量 Upsert HF 贡献者记录。"""
    if not contributors:
        return
    import json
    p = _ph()
    placeholders = ",".join([p] * 33)
    now = _now()
    all_vals = [
        (
            repo_full_name, hf_type,
            c.get("rank"), c.get("username"), c.get("fullname"),
            c.get("bio"), c.get("location"), c.get("website"), c.get("avatar_url"),
            1 if c.get("is_pro") else 0,
            c.get("num_followers", 0), c.get("num_following", 0),
            c.get("num_models", 0), c.get("num_datasets", 0), c.get("num_spaces", 0),
            json.dumps(c.get("orgs") or [], ensure_ascii=False),
            c.get("total_commits", 0), c.get("first_commit_at"), c.get("last_commit_at"),
            c.get("profile_url"), c.get("account_created"), now,
            c.get("linkedin_url"), c.get("scholar_url"),
            c.get("affiliation_type"), c.get("employer"),
            c.get("num_discussions", 0) or 0, c.get("num_papers", 0) or 0,
            c.get("num_upvotes", 0) or 0, c.get("num_likes", 0) or 0,
            c.get("twitter_url"), c.get("github_url"), c.get("bluesky_url"),
        )
        for c in contributors
    ]
    with _get_cursor() as cur:
        if _use_postgres():
            import psycopg2.extras
            psycopg2.extras.execute_batch(cur, f"""
                INSERT INTO hf_contributors (
                    repo_full_name, hf_type, rank, username, fullname,
                    bio, location, website, avatar_url, is_pro,
                    num_followers, num_following, num_models, num_datasets, num_spaces,
                    orgs, total_commits, first_commit_at, last_commit_at,
                    profile_url, account_created, scraped_at,
                    linkedin_url, scholar_url, affiliation_type, employer,
                    num_discussions, num_papers, num_upvotes, num_likes,
                    twitter_url, github_url, bluesky_url
                ) VALUES ({placeholders})
                ON CONFLICT (repo_full_name, username) DO UPDATE SET
                    hf_type=EXCLUDED.hf_type, rank=EXCLUDED.rank,
                    fullname=EXCLUDED.fullname, bio=EXCLUDED.bio,
                    location=EXCLUDED.location, website=EXCLUDED.website,
                    avatar_url=EXCLUDED.avatar_url, is_pro=EXCLUDED.is_pro,
                    num_followers=EXCLUDED.num_followers, num_following=EXCLUDED.num_following,
                    num_models=EXCLUDED.num_models, num_datasets=EXCLUDED.num_datasets,
                    num_spaces=EXCLUDED.num_spaces, orgs=EXCLUDED.orgs,
                    total_commits=EXCLUDED.total_commits,
                    first_commit_at=EXCLUDED.first_commit_at,
                    last_commit_at=EXCLUDED.last_commit_at,
                    profile_url=EXCLUDED.profile_url,
                    account_created=EXCLUDED.account_created,
                    scraped_at=EXCLUDED.scraped_at,
                    linkedin_url=EXCLUDED.linkedin_url,
                    scholar_url=EXCLUDED.scholar_url,
                    affiliation_type=EXCLUDED.affiliation_type,
                    employer=EXCLUDED.employer,
                    num_discussions=EXCLUDED.num_discussions,
                    num_papers=EXCLUDED.num_papers,
                    num_upvotes=EXCLUDED.num_upvotes,
                    num_likes=EXCLUDED.num_likes,
                    twitter_url=EXCLUDED.twitter_url,
                    github_url=EXCLUDED.github_url,
                    bluesky_url=EXCLUDED.bluesky_url
            """, all_vals, page_size=200)
        else:
            cur.executemany(f"""
                INSERT OR REPLACE INTO hf_contributors (
                    repo_full_name, hf_type, rank, username, fullname,
                    bio, location, website, avatar_url, is_pro,
                    num_followers, num_following, num_models, num_datasets, num_spaces,
                    orgs, total_commits, first_commit_at, last_commit_at,
                    profile_url, account_created, scraped_at,
                    linkedin_url, scholar_url, affiliation_type, employer,
                    num_discussions, num_papers, num_upvotes, num_likes,
                    twitter_url, github_url, bluesky_url
                ) VALUES ({placeholders})
            """, all_vals)


def get_hf_contributors(repo_full_name: str) -> List[Dict]:
    """返回该 HF 项目的贡献者列表，按 rank 升序。"""
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM hf_contributors WHERE repo_full_name = {p} ORDER BY rank ASC",
            (repo_full_name,),
        )
        return [dict(r) for r in cur.fetchall()]


def list_hf_repos() -> List[Dict]:
    """返回所有 HF 项目，按采集时间倒序。"""
    with _get_cursor() as cur:
        cur.execute("SELECT * FROM hf_repos ORDER BY scraped_at DESC")
        return [dict(r) for r in cur.fetchall()]


def delete_hf_repo(repo_full_name: str):
    """删除 HF 项目及其所有贡献者记录。"""
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(f"DELETE FROM hf_contributors WHERE repo_full_name = {p}", (repo_full_name,))
        cur.execute(f"DELETE FROM hf_repos WHERE full_name = {p}", (repo_full_name,))


def get_hf_complete_profiles(repo_full_name: str) -> Dict[str, Dict]:
    """返回已有完整 Profile 的贡献者（num_followers 不为 null），用于续传跳过。"""
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM hf_contributors WHERE repo_full_name = {p} AND num_followers IS NOT NULL",
            (repo_full_name,),
        )
        return {row["username"]: dict(row) for row in cur.fetchall()}


# ── HF Org CRUD ──────────────────────────────────────────────────

def save_hf_org(overview: Dict[str, Any]):
    p = _ph()
    vals = (
        overview.get("name"),
        overview.get("fullname"),
        overview.get("avatar_url"),
        1 if overview.get("is_verified") else 0,
        overview.get("num_members", 0),
        overview.get("num_models", 0),
        overview.get("num_datasets", 0),
        overview.get("num_spaces", 0),
        overview.get("num_papers", 0),
        overview.get("num_followers", 0),
        _now(),
    )
    with _get_cursor() as cur:
        if _use_postgres():
            cur.execute(f"""
                INSERT INTO hf_orgs
                    (name, fullname, avatar_url, is_verified, num_members,
                     num_models, num_datasets, num_spaces, num_papers, num_followers, scraped_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
                ON CONFLICT (name) DO UPDATE SET
                    fullname=EXCLUDED.fullname, avatar_url=EXCLUDED.avatar_url,
                    is_verified=EXCLUDED.is_verified, num_members=EXCLUDED.num_members,
                    num_models=EXCLUDED.num_models, num_datasets=EXCLUDED.num_datasets,
                    num_spaces=EXCLUDED.num_spaces, num_papers=EXCLUDED.num_papers,
                    num_followers=EXCLUDED.num_followers, scraped_at=EXCLUDED.scraped_at
            """, vals)
        else:
            cur.execute(f"""
                INSERT OR REPLACE INTO hf_orgs
                    (name, fullname, avatar_url, is_verified, num_members,
                     num_models, num_datasets, num_spaces, num_papers, num_followers, scraped_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
            """, vals)


def save_hf_org_members(org_name: str, members: List[Dict[str, Any]]):
    if not members:
        return
    import json
    p = _ph()
    placeholders = ",".join([p] * 29)
    now = _now()
    all_vals = [
        (
            org_name,
            m.get("username"),
            m.get("fullname"),
            m.get("member_type"),
            1 if m.get("is_pro") else 0,
            m.get("avatar_url"),
            m.get("bio"),
            m.get("location"),
            m.get("website"),
            m.get("num_followers", 0),
            m.get("num_following", 0),
            m.get("num_models", 0),
            m.get("num_datasets", 0),
            m.get("num_spaces", 0),
            json.dumps(m.get("orgs") or [], ensure_ascii=False),
            m.get("profile_url"),
            m.get("account_created"),
            now,
            m.get("linkedin_url"),
            m.get("scholar_url"),
            m.get("affiliation_type"),
            m.get("employer"),
            m.get("num_discussions", 0) or 0,
            m.get("num_papers", 0) or 0,
            m.get("num_upvotes", 0) or 0,
            m.get("num_likes", 0) or 0,
            m.get("twitter_url"),
            m.get("github_url"),
            m.get("bluesky_url"),
        )
        for m in members
    ]
    with _get_cursor() as cur:
        if _use_postgres():
            import psycopg2.extras
            psycopg2.extras.execute_batch(cur, f"""
                INSERT INTO hf_org_members
                    (org_name, username, fullname, member_type, is_pro, avatar_url,
                     bio, location, website, num_followers, num_following,
                     num_models, num_datasets, num_spaces, orgs,
                     profile_url, account_created, scraped_at,
                     linkedin_url, scholar_url, affiliation_type, employer,
                     num_discussions, num_papers, num_upvotes, num_likes,
                     twitter_url, github_url, bluesky_url)
                VALUES ({placeholders})
                ON CONFLICT (org_name, username) DO UPDATE SET
                    fullname=EXCLUDED.fullname, member_type=EXCLUDED.member_type,
                    is_pro=EXCLUDED.is_pro, avatar_url=EXCLUDED.avatar_url,
                    bio=EXCLUDED.bio, location=EXCLUDED.location, website=EXCLUDED.website,
                    num_followers=EXCLUDED.num_followers, num_following=EXCLUDED.num_following,
                    num_models=EXCLUDED.num_models, num_datasets=EXCLUDED.num_datasets,
                    num_spaces=EXCLUDED.num_spaces, orgs=EXCLUDED.orgs,
                    profile_url=EXCLUDED.profile_url, account_created=EXCLUDED.account_created,
                    scraped_at=EXCLUDED.scraped_at,
                    linkedin_url=EXCLUDED.linkedin_url, scholar_url=EXCLUDED.scholar_url,
                    affiliation_type=EXCLUDED.affiliation_type, employer=EXCLUDED.employer,
                    num_discussions=EXCLUDED.num_discussions, num_papers=EXCLUDED.num_papers,
                    num_upvotes=EXCLUDED.num_upvotes, num_likes=EXCLUDED.num_likes,
                    twitter_url=EXCLUDED.twitter_url, github_url=EXCLUDED.github_url,
                    bluesky_url=EXCLUDED.bluesky_url
            """, all_vals, page_size=200)
        else:
            cur.executemany(f"""
                INSERT OR REPLACE INTO hf_org_members
                    (org_name, username, fullname, member_type, is_pro, avatar_url,
                     bio, location, website, num_followers, num_following,
                     num_models, num_datasets, num_spaces, orgs,
                     profile_url, account_created, scraped_at,
                     linkedin_url, scholar_url, affiliation_type, employer,
                     num_discussions, num_papers, num_upvotes, num_likes,
                     twitter_url, github_url, bluesky_url)
                VALUES ({placeholders})
            """, all_vals)


def get_hf_org_members(org_name: str) -> List[Dict]:
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM hf_org_members WHERE org_name = {p} ORDER BY num_followers DESC",
            (org_name,),
        )
        return [dict(r) for r in cur.fetchall()]


def list_hf_orgs() -> List[Dict]:
    with _get_cursor() as cur:
        cur.execute("SELECT * FROM hf_orgs ORDER BY scraped_at DESC")
        return [dict(r) for r in cur.fetchall()]


def delete_hf_org(org_name: str):
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(f"DELETE FROM hf_org_members WHERE org_name = {p}", (org_name,))
        cur.execute(f"DELETE FROM hf_orgs WHERE name = {p}", (org_name,))


def get_hf_org_complete_profiles(org_name: str) -> Dict[str, Dict]:
    """返回已有完整 Profile 的成员，用于续传跳过。"""
    p = _ph()
    with _get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM hf_org_members WHERE org_name = {p} AND num_followers IS NOT NULL",
            (org_name,),
        )
        return {row["username"]: dict(row) for row in cur.fetchall()}
