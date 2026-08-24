#!/usr/bin/env python3
"""Central Git data-security gate for repositories pushed to external remotes.

Design goals:
- fail closed on credentials, likely internal infrastructure/data, and likely PII;
- add stricter privacy rules for repositories declared public;
- scan staged blobs at pre-commit and every new commit/blob at pre-push;
- never print matched secret values;
- support a local-only literal denylist for real company table/system/field names.

Standard-library only. Intended to be shared through core.hooksPath.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

HOOK_DIR = Path(__file__).resolve().parent
POLICY_PATH = HOOK_DIR / "policy.json"
ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    line: int | None
    message: str
    commit: str | None = None


CREDENTIAL_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("SECRET_PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "发现私钥内容"),
    ("SECRET_GITHUB_TOKEN", re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"), "发现 GitHub token 形态"),
    ("SECRET_SK_TOKEN", re.compile(r"\bsk-(?:proj-|ant-|live-)?[A-Za-z0-9_-]{20,}\b"), "发现 sk-* 凭证形态"),
    ("SECRET_AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "发现 AWS access key 形态"),
    ("SECRET_GOOGLE_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{25,}\b"), "发现 Google API key 形态"),
    ("SECRET_SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "发现 Slack token 形态"),
    ("SECRET_HF_TOKEN", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "发现 Hugging Face token 形态"),
    ("SECRET_JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "发现 JWT 形态"),
)

GENERIC_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|refresh[_-]?token)"
    r"\s*[:=]\s*([\"'])([^\"'\r\n]{10,})\2"
)
PLACEHOLDER_MARKERS = (
    "example",
    "dummy",
    "fake",
    "test",
    "bad-key",
    "changeme",
    "placeholder",
    "synthetic",
    "redacted",
    "your_",
    "your-",
    "${",
    "<",
    "none",
    "null",
    "os.getenv",
    "getenv",
    "env[",
)

PRIVATE_IP = re.compile(
    r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3}\.)\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b"
)
INFRA_URI = re.compile(r"(?i)\b(?:jdbc:[a-z0-9]+|hdfs)://([^\s/:\"'<>]+)")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Strong combinations only. Generic architecture prose such as “公司内部规则” alone is not blocked.
COMPANY_CONTEXT_RULES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:公司内部|行内|我行|内网|生产(?:环境|库|集群))[^\n]{0,50}"
        r"(?:真实)?(?:表名|字段名|数据字典|DDL|接口地址|IP地址|账号|密码|Topic|主键|状态码|码表|生产表|生产字段)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:真实)?(?:表名|字段名|数据字典|DDL|接口地址|IP地址|账号|密码|Topic|主键|状态码|码表|生产表|生产字段)"
        r"[^\n]{0,50}(?:公司内部|行内|我行|内网|生产(?:环境|库|集群))",
        re.IGNORECASE,
    ),
)

WINDOWS_USER_PATH = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\r\n\t ]+")
WINDOWS_LOCAL_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:SoftWare|Projects?|Work(?:space)?|Repos?)\\[^\r\n\t ]+")
UNIX_USER_PATH = re.compile(r"(?:/home/[A-Za-z0-9._-]+/|/mnt/[a-z]/(?:Users|SoftWare|Projects?|Work(?:space)?|Repos?)/[^\s]+)", re.IGNORECASE)

CN_PHONE = re.compile(r"(?<![0-9A-Fa-f])1[3-9]\d{9}(?![0-9A-Fa-f])")
CN_ID = re.compile(r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)")
LONG_DIGITS = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
NON_CARD_NUMERIC_LABEL = re.compile(
    r"(?i)(?P<label>\b(?:timestamp|rest_id|tweet_id|status_id|event_id|message_id|task_id|user_id)\b[\"']?"
    r"|[A-Za-z_][A-Za-z0-9_]*(?:Id|_id))\s*[:=]\s*[\"']?\s*$"
)
PAYMENT_LABEL_TERMS = ("card", "pan", "account", "payment", "credit", "debit")
SOCIAL_STATUS_URL = re.compile(r"(?i)https?://(?:www\.)?(?:x\.com|twitter\.com)/[^\s\"'<>]*/status/(\d{13,19})\b")
PUBLIC_DOCUMENT_URL = re.compile(r"(?i)https?://[^\s\"'<>]*/(\d{13,19})\.(?:pdf|html?|json|xml)(?:[?#][^\s\"'<>]*)?")
COMPANY_META_SAFE = re.compile(
    r"(?:不等于|待确认|后面.{0,20}(?:再)?填|后续.{0,30}(?:确认|适配|填写|映射)"
    r"|空白模板.{0,80}才能填写|(?:样例映射|示例映射).{0,80}(?:最终需接|后续|不等于))"
)

FORBIDDEN_FILENAMES = {".env", "internal_integration.json"}


def git(*args: str, input_text: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def git_bytes(*args: str) -> bytes:
    proc = subprocess.run(["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def load_policy() -> dict:
    with POLICY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def repository_name() -> str:
    origin = git("config", "--get", "remote.origin.url", check=False).strip()
    if origin:
        tail = origin.rstrip("/").rsplit("/", 1)[-1]
        if ":" in tail and "/" not in tail:
            tail = tail.rsplit(":", 1)[-1]
        return tail[:-4] if tail.endswith(".git") else tail
    return Path(git("rev-parse", "--show-toplevel").strip()).name


def is_public_repo(policy: dict, repo: str) -> bool:
    return repo in set(policy.get("public_repositories", []))


def matches_allowlist(policy: dict, repo: str, path: str) -> bool:
    patterns: list[str] = []
    allow = policy.get("allow_paths", {})
    patterns.extend(allow.get("*", []))
    patterns.extend(allow.get(repo, []))
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pat) or normalized == pat for pat in patterns)


def line_number(text: str, start: int) -> int:
    return text.count("\n", 0, start) + 1


def masked_email(email: str) -> str:
    if "@" not in email:
        return "<EMAIL>"
    local, domain = email.split("@", 1)
    prefix = local[:1] + "***" if local else "***"
    return f"{prefix}@{domain}"


def luhn_valid(value: str) -> bool:
    digits = [int(ch) for ch in value]
    total = 0
    parity = len(digits) % 2
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def line_slice(text: str, start: int, end: int) -> tuple[str, int]:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return text[line_start:line_end], line_start


def is_known_non_card_number(text: str, match: re.Match[str]) -> bool:
    start, end = match.span()
    value = match.group(0)
    if start > 0 and text[start - 1] == "-":
        return True

    line, line_start = line_slice(text, start, end)
    before = line[: start - line_start]
    label_match = NON_CARD_NUMERIC_LABEL.search(before[-120:])
    if label_match:
        label = label_match.group("label").lower()
        if not any(term in label for term in PAYMENT_LABEL_TERMS):
            return True

    context = text[max(0, start - 220) : min(len(text), end + 220)]
    if any(url_value == value for url_value in SOCIAL_STATUS_URL.findall(context)):
        return True
    if any(url_value == value for url_value in PUBLIC_DOCUMENT_URL.findall(line)):
        return True
    return False


def is_company_meta_boundary(text: str, start: int, end: int) -> bool:
    line, line_start = line_slice(text, start, end)
    if COMPANY_META_SAFE.search(line):
        return True

    # Boundary sections intentionally describe fields/rules that are still unknown and
    # must be supplied later by an enterprise adapter. Treat that prose as governance
    # metadata, not as evidence that real internal identifiers are present.
    context_start = max(0, line_start - 500)
    context = text[context_start:end]
    return bool(
        re.search(r"(?:企业适配边界|企业映射\s*[:：]\s*待确认|以下继续保持.{0,40}待确认)", context, re.IGNORECASE | re.DOTALL)
    )


def load_local_denylist(policy: dict) -> list[str]:
    raw = policy.get("local_denylist")
    if not raw:
        return []
    path = Path(os.path.expanduser(raw))
    if not path.exists():
        return []
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            terms.append(value)
    return terms


def scan_path_policy(policy: dict, repo: str, path: str, public: bool, commit: str | None) -> list[Finding]:
    if matches_allowlist(policy, repo, path):
        return []
    normalized = path.replace("\\", "/")
    lower = normalized.lower()
    name = Path(normalized).name.lower()
    ext = Path(normalized).suffix.lower()
    findings: list[Finding] = []

    if name in FORBIDDEN_FILENAMES and name != ".env.example":
        findings.append(Finding("FILE_FORBIDDEN_CONFIG", path, None, "禁止把真实环境/内部映射配置提交到外部仓库", commit))

    if public and ext in set(policy.get("public_block_extensions", [])):
        findings.append(Finding("PUBLIC_RISKY_ATTACHMENT", path, None, "Public 仓库禁止直接提交日志、数据、压缩包、密钥或办公附件；请改为脱敏文本或先加入中央白名单", commit))

    sensitive_terms = [str(x).lower() for x in policy.get("sensitive_path_terms", [])]
    if ext in set(policy.get("external_sensitive_path_extensions", [])) and any(term in lower for term in sensitive_terms):
        findings.append(Finding("EXTERNAL_INTERNAL_ATTACHMENT", path, None, "疑似内部/生产/客户资料附件，禁止上传外部 Git 远程", commit))

    return findings


def scan_text(policy: dict, repo: str, path: str, text: str, public: bool, commit: str | None) -> list[Finding]:
    findings: list[Finding] = []

    for rule_id, regex, message in CREDENTIAL_RULES:
        for match in regex.finditer(text):
            findings.append(Finding(rule_id, path, line_number(text, match.start()), message, commit))

    for match in GENERIC_SECRET_ASSIGNMENT.finditer(text):
        value = match.group(3).lower()
        if any(marker in value for marker in PLACEHOLDER_MARKERS):
            continue
        findings.append(
            Finding(
                "SECRET_ASSIGNMENT",
                path,
                line_number(text, match.start()),
                f"{match.group(1)} 疑似被硬编码为真实值",
                commit,
            )
        )

    for match in PRIVATE_IP.finditer(text):
        findings.append(Finding("INTERNAL_PRIVATE_IP", path, line_number(text, match.start()), "发现私网 IP，禁止上传外部 Git 远程", commit))

    for match in INFRA_URI.finditer(text):
        host = match.group(1).lower()
        if host not in LOCAL_HOSTS:
            findings.append(Finding("INTERNAL_INFRA_URI", path, line_number(text, match.start()), "发现 JDBC/HDFS 连接地址，请确认不是内部基础设施", commit))

    for regex in COMPANY_CONTEXT_RULES:
        for match in regex.finditer(text):
            if is_company_meta_boundary(text, match.start(), match.end()):
                continue
            findings.append(Finding("COMPANY_INTERNAL_DETAIL", path, line_number(text, match.start()), "发现公司/行内上下文与真实表字段、生产环境或内部接口细节组合", commit))

    for term in load_local_denylist(policy):
        start = text.lower().find(term.lower())
        if start >= 0:
            findings.append(Finding("LOCAL_COMPANY_DENYLIST", path, line_number(text, start), "命中本机维护的公司敏感词表；具体命中值不显示", commit))

    for match in CN_PHONE.finditer(text):
        findings.append(Finding("PII_PHONE", path, line_number(text, match.start()), "发现疑似中国大陆手机号", commit))

    for match in CN_ID.finditer(text):
        findings.append(Finding("PII_CN_ID", path, line_number(text, match.start()), "发现疑似身份证号", commit))

    for match in LONG_DIGITS.finditer(text):
        value = match.group(0)
        if is_known_non_card_number(text, match):
            continue
        if luhn_valid(value):
            findings.append(Finding("PII_PAYMENT_CARD", path, line_number(text, match.start()), "发现通过 Luhn 校验的疑似银行卡号", commit))

    if public:
        for rule_id, regex, message in (
            ("PUBLIC_WINDOWS_USER_PATH", WINDOWS_USER_PATH, "Public 仓库发现 Windows 用户目录，可能暴露本机用户名"),
            ("PUBLIC_LOCAL_PATH", WINDOWS_LOCAL_PATH, "Public 仓库发现本机绝对路径"),
            ("PUBLIC_UNIX_USER_PATH", UNIX_USER_PATH, "Public 仓库发现 Linux/WSL 本机绝对路径"),
        ):
            for match in regex.finditer(text):
                findings.append(Finding(rule_id, path, line_number(text, match.start()), message, commit))

    return findings


def scan_blob(policy: dict, repo: str, path: str, blob: bytes, public: bool, commit: str | None) -> list[Finding]:
    findings = scan_path_policy(policy, repo, path, public, commit)
    if matches_allowlist(policy, repo, path):
        return findings
    if b"\x00" in blob[:4096]:
        return findings
    max_bytes = int(policy.get("max_text_bytes", 2_000_000))
    if len(blob) > max_bytes:
        # Large text-like artifacts are risky in public repos because they often contain raw logs/dumps.
        if public:
            findings.append(Finding("PUBLIC_LARGE_TEXT", path, None, f"Public 仓库文本文件超过 {max_bytes} bytes，需先拆分/脱敏审查", commit))
        return findings
    text = blob.decode("utf-8", errors="replace")
    return findings + scan_text(policy, repo, path, text, public, commit)


def staged_paths() -> list[str]:
    raw = git_bytes("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def commit_paths(commit: str) -> list[str]:
    raw = git_bytes("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "--diff-filter=ACMR", "-z", commit)
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def scan_pre_commit(policy: dict, repo: str, public: bool) -> list[Finding]:
    findings: list[Finding] = []
    if public:
        email = git("config", "--get", "user.email", check=False).strip()
        if email and not email.lower().endswith("users.noreply.github.com"):
            findings.append(Finding("PUBLIC_AUTHOR_EMAIL", "<git-config>", None, f"Public 仓库提交邮箱不是 GitHub noreply：{masked_email(email)}"))
    for path in staged_paths():
        try:
            blob = git_bytes("show", f":{path}")
        except RuntimeError:
            continue
        findings.extend(scan_blob(policy, repo, path, blob, public, None))
    return findings


def commits_for_push(remote_name: str, local_sha: str, remote_sha: str) -> list[str]:
    if local_sha == ZERO_SHA:
        return []
    if remote_sha and remote_sha != ZERO_SHA:
        revs = git("rev-list", f"{remote_sha}..{local_sha}").splitlines()
    else:
        args = ["rev-list", local_sha]
        if remote_name:
            args.append(f"--not")
            args.append(f"--remotes={remote_name}")
        revs = git(*args).splitlines()
    return [rev for rev in revs if rev]


def scan_commit(policy: dict, repo: str, public: bool, commit: str) -> list[Finding]:
    findings: list[Finding] = []
    if public:
        email = git("show", "-s", "--format=%ae", commit).strip()
        if email and not email.lower().endswith("users.noreply.github.com"):
            findings.append(Finding("PUBLIC_AUTHOR_EMAIL", "<commit-metadata>", None, f"Public commit 作者邮箱不是 GitHub noreply：{masked_email(email)}", commit))

    message = git("show", "-s", "--format=%B", commit)
    findings.extend(scan_text(policy, repo, "<commit-message>", message, public, commit))

    for path in commit_paths(commit):
        try:
            blob = git_bytes("show", f"{commit}:{path}")
        except RuntimeError:
            continue
        findings.extend(scan_blob(policy, repo, path, blob, public, commit))
    return findings


def scan_pre_push(policy: dict, repo: str, public: bool, remote_name: str, stdin_text: str) -> list[Finding]:
    findings: list[Finding] = []
    commits: list[str] = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        commits.extend(commits_for_push(remote_name, local_sha, remote_sha))
    # Keep deterministic order and avoid rescanning shared commits pushed to multiple refs.
    unique_commits = list(dict.fromkeys(reversed(commits)))
    for commit in unique_commits:
        findings.extend(scan_commit(policy, repo, public, commit))
    return findings


def scan_head(policy: dict, repo: str, public: bool) -> list[Finding]:
    findings: list[Finding] = []
    raw = git_bytes("ls-tree", "-r", "--name-only", "-z", "HEAD")
    paths = [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]
    for path in paths:
        try:
            blob = git_bytes("show", f"HEAD:{path}")
        except RuntimeError:
            continue
        findings.extend(scan_blob(policy, repo, path, blob, public, "HEAD"))
    return findings


def scan_range(policy: dict, repo: str, public: bool, base_sha: str, head_sha: str) -> list[Finding]:
    if not head_sha:
        raise ValueError("audit-range requires head SHA")
    if base_sha and base_sha != ZERO_SHA:
        revs = git("rev-list", "--reverse", f"{base_sha}..{head_sha}").splitlines()
    else:
        revs = git("rev-list", "--reverse", head_sha).splitlines()
    findings: list[Finding] = []
    for commit in revs:
        if commit:
            findings.extend(scan_commit(policy, repo, public, commit))
    return findings


def dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, int | None, str | None]] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (finding.rule_id, finding.path, finding.line, finding.commit)
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return result


def print_findings(repo: str, public: bool, findings: Sequence[Finding]) -> None:
    visibility = "PUBLIC(公开)" if public else "EXTERNAL-PRIVATE(外网私有)"
    print(f"\n[DATA-SECURITY] BLOCKED: {repo} [{visibility}] 检测到 {len(findings)} 个风险项", file=sys.stderr)
    for finding in findings[:80]:
        location = finding.path
        if finding.line:
            location += f":{finding.line}"
        if finding.commit and finding.commit != "HEAD":
            location += f" @ {finding.commit[:10]}"
        print(f"  - {finding.rule_id}: {location} — {finding.message}", file=sys.stderr)
    if len(findings) > 80:
        print(f"  ... 其余 {len(findings) - 80} 项省略", file=sys.stderr)
    print("\n处理方式：删除/脱敏风险内容后重新提交。真实公司表名、字段名、系统名可加入本机 company-denylist.txt；不要把该敏感词表提交进 Git。", file=sys.stderr)
    print("如确属公开资料，需要在中央 security-hooks/policy.json 做最小路径白名单并写清理由，而不是临时跳过 Hook。\n", file=sys.stderr)


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"pre-commit", "pre-push", "audit-head", "audit-range"}:
        print("usage: security_scan.py {pre-commit|pre-push|audit-head|audit-range} [args]", file=sys.stderr)
        return 2

    policy = load_policy()
    repo = repository_name()
    managed = set(policy.get("managed_repositories", []))
    if managed and repo not in managed:
        # Central hooks may be configured broadly later; unmanaged third-party repos are left untouched.
        return 0
    public = is_public_repo(policy, repo)
    mode = argv[1]

    try:
        if mode == "pre-commit":
            findings = scan_pre_commit(policy, repo, public)
        elif mode == "pre-push":
            remote_name = argv[2] if len(argv) > 2 else "origin"
            findings = scan_pre_push(policy, repo, public, remote_name, sys.stdin.read())
        elif mode == "audit-range":
            base_sha = argv[2] if len(argv) > 2 else ""
            head_sha = argv[3] if len(argv) > 3 else "HEAD"
            findings = scan_range(policy, repo, public, base_sha, head_sha)
        else:
            findings = scan_head(policy, repo, public)
    except Exception as exc:
        print(f"[DATA-SECURITY] BLOCKED: 安全扫描器执行失败，按 fail-closed(失败即阻断) 处理：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    findings = dedupe(findings)
    if findings:
        print_findings(repo, public, findings)
        return 1
    print(f"[DATA-SECURITY] PASS: {repo} {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
