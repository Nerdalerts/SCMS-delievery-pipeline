import os
import re
import sys

# Maximum file size allowed in the repository (5 MB)
MAX_FILE_BYTES = 5 * 1024 * 1024

# Ignored directories that are not committed to git
IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", "data"}

# Patterns to catch accidental leaks of secrets and identity
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
DAPI_REGEX = re.compile(r"\bd" + r"api[a-zA-Z0-9]{16,}\b")
WORKSPACE_URL_REGEX = re.compile(r"https://" + r"[^\s\"'`<>]*databricks[^\s\"'`<>]*", re.IGNORECASE)


def is_binary(path):
    with open(path, "rb") as f:
        chunk = f.read(1024)
        return b"\x00" in chunk


def check_file(path):
    violations = []
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        violations.append(f"File size {size} bytes exceeds 5 MB limit")

    if is_binary(path):
        return violations

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line_number, line in enumerate(f, start=1):
            if os.path.basename(path) == "check_repo.py":
                continue
            if EMAIL_REGEX.search(line):
                violations.append(f"Line {line_number}: matched email address pattern")
            if DAPI_REGEX.search(line):
                violations.append(f"Line {line_number}: matched Databricks token pattern")
            if WORKSPACE_URL_REGEX.search(line):
                violations.append(f"Line {line_number}: matched Databricks workspace URL")

    return violations


def scan_repo(root_dir):
    all_violations = {}
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            issues = check_file(file_path)
            if issues:
                all_violations[file_path] = issues
    return all_violations


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    violations = scan_repo(root)

    if violations:
        print("Repository check failed with the following issues:")
        for path, issues in violations.items():
            rel_path = os.path.relpath(path, root)
            for issue in issues:
                print(f"  {rel_path}: {issue}")
        sys.exit(1)

    print("Repository check passed: no secrets, PII, or oversized files found.")
    sys.exit(0)


if __name__ == "__main__":
    main()
