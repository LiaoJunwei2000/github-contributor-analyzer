import sqlite3
import os
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "contributors.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS repos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name   TEXT UNIQUE NOT NULL,
                description TEXT,
                stars       INTEGER,
                forks       INTEGER,
                watchers    INTEGER,
                language    TEXT,
                scraped_at  TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS contributors (
                id                            INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full_name                TEXT NOT NULL,
                rank                          INTEGER,
                login                         TEXT NOT NULL,
                user_id                       INTEGER,
                name                          TEXT,
                company                       TEXT,
                location                      TEXT,
                email                         TEXT,
                blog                          TEXT,
                twitter_username              TEXT,
                hireable                      INTEGER,
                bio                           TEXT,
                public_repos                  INTEGER,
                public_gists                  INTEGER,
                followers                     INTEGER,
                following                     INTEGER,
                total_commits                 INTEGER,
                total_additions               INTEGER,
                total_deletions               INTEGER,
                net_lines                     INTEGER,
                total_changes                 INTEGER,
                avg_changes_per_commit        REAL,
                addition_deletion_ratio       REAL,
                contributions_on_default_branch INTEGER,
                profile_url                   TEXT,
                avatar_url                    TEXT,
                account_created               TEXT,
                last_updated                  TEXT,
                scraped_at                    TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(repo_full_name, login)
            );
        """)


def save_repo(details: Dict[str, Any]):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO repos
                (full_name, description, stars, forks, watchers, language, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (
            details.get("full_name"),
            details.get("description"),
            details.get("stargazers_count"),
            details.get("forks_count"),
            details.get("subscribers_count"),
            details.get("language"),
        ))


def save_contributors(repo_full_name: str, contributors: List[Dict[str, Any]]):
    with get_conn() as conn:
        for c in contributors:
            conn.execute("""
                INSERT OR REPLACE INTO contributors (
                    repo_full_name, rank, login, user_id, name, company, location,
                    email, blog, twitter_username, hireable, bio,
                    public_repos, public_gists, followers, following,
                    total_commits, total_additions, total_deletions,
                    net_lines, total_changes, avg_changes_per_commit,
                    addition_deletion_ratio, contributions_on_default_branch,
                    profile_url, avatar_url, account_created, last_updated,
                    scraped_at
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    datetime('now', 'localtime')
                )
            """, (
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
                c.get("account_created"), c.get("last_updated"),
            ))


def list_repos() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM repos ORDER BY scraped_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_contributors(repo_full_name: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM contributors WHERE repo_full_name = ? ORDER BY rank ASC",
            (repo_full_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_repo(repo_full_name: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM contributors WHERE repo_full_name = ?", (repo_full_name,))
        conn.execute("DELETE FROM repos WHERE full_name = ?", (repo_full_name,))
