import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "contributors.db")


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
@contextmanager
def _pg_cursor():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(
        _get_database_url(),
        cursor_factory=psycopg2.extras.RealDictCursor,
        sslmode="require",
    )
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


def _init_postgres():
    with _pg_cursor() as cur:
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
    p = _ph()
    placeholders = ",".join([p] * 29)
    with _get_cursor() as cur:
        for c in contributors:
            vals = (
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
                c.get("account_created"), c.get("last_updated"), _now(),
            )
            if _use_postgres():
                cur.execute(f"""
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
                """, vals)
            else:
                cur.execute(f"""
                    INSERT OR REPLACE INTO contributors (
                        repo_full_name, rank, login, user_id, name, company, location,
                        email, blog, twitter_username, hireable, bio,
                        public_repos, public_gists, followers, following,
                        total_commits, total_additions, total_deletions,
                        net_lines, total_changes, avg_changes_per_commit,
                        addition_deletion_ratio, contributions_on_default_branch,
                        profile_url, avatar_url, account_created, last_updated, scraped_at
                    ) VALUES ({placeholders})
                """, vals)


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
        cur.execute(f"DELETE FROM repos WHERE full_name = {p}", (repo_full_name,))
