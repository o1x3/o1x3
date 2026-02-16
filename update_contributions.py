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
SECTION_START = "<!-- OSS_CONTRIBUTIONS_START -->"
SECTION_END = "<!-- OSS_CONTRIBUTIONS_END -->"
LANG_START = "<!-- TOP_LANGUAGES_START -->"
LANG_END = "<!-- TOP_LANGUAGES_END -->"

LANG_EMOJI = {
    "Python": "🐍", "JavaScript": "🟨", "TypeScript": "🔷", "Rust": "🦀",
    "Go": "🐹", "Java": "☕", "C++": "⚡", "C": "⚙️", "Ruby": "💎",
    "Swift": "🍎", "Kotlin": "🟣", "Shell": "🐚", "HTML": "🌐",
    "CSS": "🎨", "PHP": "🐘", "Dart": "🎯", "Scala": "🔴",
    "Elixir": "💧", "Lua": "🌙", "Zig": "⚡", "Vue": "💚",
}


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


def format_stars(n):
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


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


def build_lang_section(languages):
    if not languages:
        return ""

    lines = []
    for lang, pct in languages:
        emoji = LANG_EMOJI.get(lang, "📦")
        bar_width = int(pct * 20)
        bar = "█" * bar_width + "░" * (20 - bar_width)
        lines.append(f"  {emoji} {lang:<14} {bar} {pct * 100:5.1f}%")

    return "```\n" + "\n".join(lines) + "\n```"


def build_contributions_section(contributions):
    if not contributions:
        return "*No external contributions yet.*"

    total = len(contributions)
    repos = len({c["repo"] for c in contributions})
    total_stars = sum(c["stars"] for c in contributions)

    lines = []
    lines.append(
        f"**{total}** merged PRs across **{repos}** repos "
        f"({format_stars(total_stars)} combined stars)"
    )
    lines.append("")
    lines.append("| Repository | PR | Merged |")
    lines.append("|---|---|---|")

    for c in contributions:
        lang = c["language"] or ""
        emoji = LANG_EMOJI.get(lang, "")
        stars = format_stars(c["stars"])
        repo_cell = f"[{c['repo']}](https://github.com/{c['repo']}) · {emoji} {stars} ⭐"
        pr_cell = f"[{c['title']}]({c['url']})"
        lines.append(f"| {repo_cell} | {pr_cell} | {c['merged_at']} |")

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

    # Build sections
    contrib_md = build_contributions_section(external)
    lang_md = build_lang_section(languages)

    # Update README
    with open(args.readme) as f:
        content = f.read()

    content = replace_section(content, SECTION_START, SECTION_END, contrib_md)
    content = replace_section(content, LANG_START, LANG_END, lang_md)

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
