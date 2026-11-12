# tiny_harness/tools/files.py
import os
import glob
import shutil
from difflib import SequenceMatcher


def read_file(args: dict) -> str:
    path = args["path"]
    offset = max(1, args.get("offset", 1))
    limit = args.get("limit")
    if limit is not None and limit <= 0:
        return f"Error: limit must be positive, got {limit}."
    if not os.path.exists(path):
        return f"Error: File '{path}' not found."
    if os.path.isdir(path):
        return f"Error: '{path}' is a directory."
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    total = len(lines)
    if total == 0:
        return f"[{path}] Lines 0-0 of 0 (empty)\n"
    if offset > total:
        return f"Error: offset {offset} exceeds file length {total}."
    selected = lines[offset - 1 : (offset - 1 + limit) if limit else None]
    result = "".join(selected)
    end = offset + len(selected) - 1
    header = f"[{path}] Lines {offset}-{end} of {total}\n"
    output = header + result
    if len(output) > 50000:
        output = output[:50000] + "\n\n[... truncated at 50,000 characters]"
    return output


def write_file(args: dict) -> str:
    path = args["path"]
    content = args["content"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existed = os.path.exists(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    size = len(content.encode("utf-8"))
    action = "Updated" if existed else "Created"
    lines = content.count("\n") + 1
    return f"{action} '{path}' ({lines} lines, {_format_size(size)})"


def list_directory(args: dict) -> str:
    path = args.get("path", ".")
    pattern = args.get("pattern")
    recursive = args.get("recursive", False)
    if not os.path.isdir(path):
        return f"Error: '{path}' is not a directory."
    entries = []
    if recursive:
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                full = os.path.join(root, name)
                if not pattern or _glob_match(name, pattern):
                    entries.append(_format_entry(full, os.path.relpath(full, path)))
    else:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if not pattern or _glob_match(name, pattern):
                entries.append(_format_entry(full, name))
    if not entries:
        return f"Directory '{path}' is empty."
    return f"[{path}] {len(entries)} items:\n" + "\n".join(entries)


def find_files(args: dict) -> str:
    pattern = args["pattern"]
    path = args.get("path", ".")
    max_results = args.get("max_results", 200)
    matches = []

    import os as _os

    base = _os.path.realpath(path)
    search_path = _os.path.join(base, pattern)
    if _os.path.isabs(pattern) or ".." in pattern.split(_os.sep):
        resolved = (
            _os.path.realpath(search_path)
            if _os.path.exists(search_path)
            else _os.path.realpath(base)
        )
        if not resolved.startswith(base + _os.sep) and resolved != base:
            return f"Error: pattern '{pattern}' escapes workspace."

    for i, match in enumerate(glob.glob(search_path, recursive=True)):
        if i >= max_results:
            break
        matches.append(os.path.relpath(match, path))
    if not matches:
        return f"No files matching '{pattern}' found in '{path}'."
    return f"Found {len(matches)} files matching '{pattern}':\n" + "\n".join(
        f"  {m}" for m in matches
    )


def delete_file(args: dict) -> str:
    path = args["path"]
    if not os.path.exists(path):
        return f"Error: File '{path}' not found."
    if os.path.isdir(path):
        return f"Error: '{path}' is a directory. Use a directory removal tool instead."
    os.remove(path)
    return f"Deleted '{path}'."


def create_directory(args: dict) -> str:
    path = args["path"]
    existed = os.path.isdir(path)
    os.makedirs(path, exist_ok=True)
    action = "Already exists" if existed else "Created"
    return f"{action} directory '{path}'."


def move_file(args: dict) -> str:
    src = args["source"]
    dst = args["destination"]
    if not os.path.exists(src):
        return f"Error: Source '{src}' not found."
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.move(src, dst)
    return f"Moved '{src}' -> '{dst}'."


def edit_file(args: dict) -> str:
    path = args["path"]
    old_string = args["old_string"]
    new_string = args["new_string"]
    replace_all = args.get("replace_all", False)

    if old_string == new_string:
        return "Error: old_string and new_string are identical — nothing to change."
    if not old_string:
        return "Error: old_string must not be empty."
    if not os.path.exists(path):
        return f"Error: File '{path}' not found."
    if os.path.isdir(path):
        return f"Error: '{path}' is a directory."

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    count = content.count(old_string)

    if count == 0:
        return _edit_no_match(path, content, old_string)
    if count > 1 and not replace_all:
        return _edit_multiple_matches(content, old_string, count)
    if count > 1 and replace_all:
        count_before = count
    else:
        count_before = count

    new_content = content.replace(old_string, new_string)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)

    if replace_all:
        return (
            f"Replaced all {count_before} occurrences of old_string in '{path}'\n"
            f"Preview — old:\n{_truncate(old_string)}\n"
            f"Preview — new:\n{_truncate(new_string)}"
        )
    lines_changed = new_string.count("\n") + 1
    return (
        f"Replaced 1 occurrence in '{path}' ({lines_changed} line(s) changed)\n"
        f"Preview — old:\n{_truncate(old_string)}\n"
        f"Preview — new:\n{_truncate(new_string)}"
    )


def _edit_no_match(path: str, content: str, old_string: str) -> str:
    first_line = old_string.split("\n")[0].strip()
    if not first_line:
        return (
            f"Error: old_string not found in '{path}'.\n"
            f"Tip: old_string must match exactly — check whitespace (tabs vs spaces) "
            f"and trailing characters. Use read_file to view '{path}' first."
        )

    lines = content.split("\n")
    candidates: list[tuple[int, str, float]] = []
    for i, line in enumerate(lines):
        ratio = SequenceMatcher(None, first_line, line.strip()).ratio()
        if ratio > 0.4:
            candidates.append((i + 1, line, ratio))
    candidates.sort(key=lambda x: -x[2])

    if not candidates:
        return (
            f"Error: old_string not found in '{path}'. "
            f"No similar lines detected.\n"
            f"Tip: Use read_file to check the file content and try again."
        )

    block = [f"Error: old_string not found in '{path}'."]
    block.append(f"Best-matching lines for '{_truncate(first_line, 40)}':\n")
    for line_num, matched, ratio in candidates[:5]:
        block.append(f"  Line {line_num} (similarity {ratio:.0%}): {matched}")
    block.append(
        "\nTip: old_string must match character-for-character. "
        "Use read_file to see exact content, or add surrounding context "
        "to make the match unique."
    )
    return "\n".join(block)


def _edit_multiple_matches(
    content: str, old_string: str, count: int
) -> str:
    lines = content.split("\n")
    old_first_line = old_string.split("\n")[0]
    occurrences: list[int] = []
    for i, line in enumerate(lines):
        if old_first_line in line or old_first_line.strip() in line:
            occurrences.append(i + 1)

    block = [
        f"Error: old_string found {count} times in file. "
        f"Add more surrounding context to make the match unique, "
        f"or use replace_all=true to replace all occurrences.\n"
    ]
    block.append(f"Preview — old:\n{_truncate(old_string, 60)}\n")
    if occurrences:
        block.append("Locations where the first line appears (showing up to 10):")
        for line_num in occurrences[:10]:
            start = max(0, line_num - 2)
            end = min(len(lines), line_num + 3)
            ctx = "\n".join(
                f"    {i + 1}: {lines[i]}" for i in range(start, end)
            )
            block.append(f"\n  Around line {line_num}:\n{ctx}")
    if len(occurrences) > 10:
        block.append(f"\n  ... and {len(occurrences) - 10} more locations.")
    block.append(
        "\nFix: Pick the unique context around your target occurrence "
        "and include it in old_string."
    )
    return "\n".join(block)


def _truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_entry(full_path: str, name: str) -> str:
    is_dir = os.path.isdir(full_path)
    prefix = "D" if is_dir else "F"
    size = "" if is_dir else _format_size(os.path.getsize(full_path))
    return f"  [{prefix}] {name}{'  ' + size if size else ''}"


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}B"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def _glob_match(name: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatch(name, pattern)
