"""
Fetches merged PRs to external repos and top languages, updates README.md.

Usage:
  GITHUB_TOKEN=ghp_xxx python update_contributions.py --username o1x3
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

MAX_PRS = 20
SECTION_START = "<!-- OPEN_SOURCE_START -->"
SECTION_END = "<!-- OPEN_SOURCE_END -->"


def api(url):
    headers = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"API error {e.code}: {url}", file=sys.stderr)
        return [] if "search" in url else {}


def get_user_repos(username):
    repos = set()
    page = 1
    while True:
        data = api(f"{GITHUB_API}/users/{username}/repos?per_page=100&page={page}&type=all")
        if not data:
            break
        for r in data:
            repos.add(r["full_name"])
        if len(data) < 100:
            break
        page += 1
    return repos


def get_merged_prs(username):
    prs = []
    page = 1
    while len(prs) < MAX_PRS * 3:
        url = (
            f"{GITHUB_API}/search/issues?q=type:pr+author:{username}+is:merged"
            f"&sort=updated&order=desc&per_page=100&page={page}"
        )
        data = api(url)
        items = data.get("items", [])
        if not items:
            break
        prs.extend(items)
        if len(items) < 100:
            break
        page += 1
    return prs


def extract_repo(pr):
    parts = pr.get("html_url", "").split("/")
    if len(parts) >= 5:
        return f"{parts[3]}/{parts[4]}"
    return None


def get_repo_info(full_name):
    return api(f"{GITHUB_API}/repos/{full_name}")


def get_top_languages(username):
    """Fetch languages across user's own repos, weighted by bytes."""
    lang_bytes = {}
    page = 1
    while True:
        repos = api(
            f"{GITHUB_API}/users/{username}/repos?per_page=100&page={page}"
            f"&type=owner&sort=updated"
        )
        if not repos:
            break
        for repo in repos:
            if repo.get("fork"):
                continue
            langs = api(repo["languages_url"])
            for lang, count in langs.items():
                lang_bytes[lang] = lang_bytes.get(lang, 0) + count
        if len(repos) < 100:
            break
        page += 1

    total = sum(lang_bytes.values()) or 1
    ranked = sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)[:8]
    return [(lang, count / total) for lang, count in ranked]


def build_section(languages, contributions):
    lines = []

    # Language tags (>=1% only, top 6)
    if languages:
        tags = []
        for lang, pct in languages:
            if pct < 0.01:
                break
            tags.append(f"**{lang} {pct * 100:.0f}%**")
            if len(tags) >= 6:
                break
        lines.append(" · ".join(tags))
        lines.append("")

    # Contributions as flat list
    if not contributions:
        lines.append("*No external contributions yet.*")
    else:
        for c in contributions:
            repo = c["repo"]
            number = c["number"]
            url = c["url"]
            title = c["title"]
            merged = c["merged_at"]
            lines.append(f"- [{repo}#{number}]({url}) — {title} · {merged}")

    return "\n".join(lines)


def replace_section(content, start, end, new_section):
    full = f"{start}\n{new_section}\n{end}"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(content):
        return pattern.sub(full, content)
    return content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--readme", default="README.md")
    args = parser.parse_args()

    username = args.username

    # Fetch owned repos
    print(f"Fetching repos for {username}...")
    owned = get_user_repos(username)
    print(f"  {len(owned)} owned/member repos")

    # Fetch merged PRs
    print("Searching merged PRs...")
    prs = get_merged_prs(username)
    print(f"  {len(prs)} total merged PRs")

    # Filter to external repos
    repo_cache = {}
    external = []
    for pr in prs:
        repo = extract_repo(pr)
        if not repo or repo in owned:
            continue
        if repo not in repo_cache:
            repo_cache[repo] = get_repo_info(repo)
        info = repo_cache[repo]
        merged_raw = pr.get("pull_request", {}).get("merged_at", "")
        merged_at = (
            datetime.strptime(merged_raw, "%Y-%m-%dT%H:%M:%SZ").strftime("%b %Y")
            if merged_raw
            else "—"
        )
        external.append({
            "repo": repo,
            "title": pr["title"],
            "number": pr["number"],
            "url": pr["html_url"],
            "merged_at": merged_at,
            "stars": info.get("stargazers_count", 0),
            "language": info.get("language"),
        })
        if len(external) >= MAX_PRS:
            break

    # Sort by most recent first
    month_order = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    def sort_key(c):
        parts = c["merged_at"].split()
        if len(parts) == 2:
            return (int(parts[1]), month_order.get(parts[0], 0))
        return (0, 0)
    external.sort(key=sort_key, reverse=True)
    print(f"  {len(external)} external contributions")

    # Fetch top languages
    print("Fetching languages...")
    languages = get_top_languages(username)
    print(f"  {len(languages)} languages found")

    # Build combined section
    section_md = build_section(languages, external)

    # Update README
    with open(args.readme) as f:
        content = f.read()

    content = replace_section(content, SECTION_START, SECTION_END, section_md)

    # Update timestamp
    now = datetime.now(timezone.utc).strftime("%b %d, %Y")
    content = re.sub(
        r"Last updated: .+?-->",
        f"Last updated: {now} -->",
        content,
    )

    with open(args.readme, "w") as f:
        f.write(content)

    print(f"Updated {args.readme}")


if __name__ == "__main__":
    main()
