# tiny_harness/skills/files.py
from mid_harness.tools.files import (
    read_file,
    write_file,
    list_directory,
    find_files,
    delete_file,
    create_directory,
    move_file,
    edit_file,
)
from mid_harness._tools import ToolDef


FILES_PROMPT_SECTION = """
## File Operations

You have full filesystem access within the workspace. All paths are relative to the workspace root unless absolute.

### Decision Matrix — Which Tool When?

| You want to... | Use | Notes |
|---|---|---|
| Create a brand-new file | `write_file` | Full content, safe — nothing to overwrite |
| Rewrite an entire file | `write_file` | Read first to confirm; accept the full-replace cost |
| Change a few lines/strings in existing file | `edit_file` | **DEFAULT for modifications.** Surgical. Only sends old/new diff. |
| Replace all occurrences of a pattern | `edit_file` with `replace_all=true` | Use for renames, global find-and-replace |
| See what's in a file | `read_file` | Use offset/limit for large files |
| Find where a file is | `find_files` | Glob patterns, discover before reading |
| Understand directory structure | `list_directory` | Before making changes in a new area |
| Remove a file | `delete_file` | **IRREVERSIBLE.** Prefer `move_file` to archive. |
| Rename / relocate a file | `move_file` | Source → destination, overwrites destination |

### CRITICAL: write_file vs edit_file

```
NEW FILE or FULL REWRITE → write_file(path, content)
    "I need to create a new config file"          → write_file
    "This file is completely wrong, rewrite it"   → write_file (read first!)

MODIFY EXISTING FILE → edit_file(path, old_string, new_string)
    "Change the timeout from 30 to 60"            → edit_file
    "Add a new parameter to this function"        → edit_file
    "Fix the typo in the error message"           → edit_file
    "Rename this variable everywhere"             → edit_file(replace_all=true)
```

**NEVER use write_file to make a small change to an existing file.** It requires sending the ENTIRE file content, which is wasteful and risks losing content. `edit_file` only needs the exact old_string and new_string.

### Standard Workflow (MANDATORY)

```
1. DISCOVER — list_directory or find_files to locate the target
2. READ — read_file to see the current content
3. EDIT — edit_file (for modifications) or write_file (for new files)
4. VERIFY — read_file again to confirm the change is correct
```

**Verification is NOT optional.** Every write or edit MUST be followed by a read_file to confirm the result matches your intent. If you skip verification and the file is wrong, you will not know until the user reports it.

### edit_file Usage Guide

edit_file replaces exact text matches. It has three behaviors depending on how many times old_string appears:

**Single match** → Replaces the one occurrence, reports success.
**Multiple matches** → Returns ALL locations with context. Add surrounding lines to old_string to make it unique, or use replace_all=true.
**No match** → Returns the most similar lines in the file with similarity scores. Re-read the file, copy the exact text (including whitespace), and retry.

Pattern for editing: read_file → copy exact text → edit_file → read_file to verify.

Common edit_file mistakes:
- Forgetting to read_file first → old_string doesn't match → error
- Not including enough context → multiple matches → error
- Copy/paste losing whitespace → use the exact text from read_file output

Recovery: If edit_file says "not found", run read_file again and copy the exact text. If it says "found N times", add surrounding lines to old_string.

### Do NOT

- **Use write_file for small changes to existing files** — use edit_file. This is the #1 mistake.
- **Skip read_file before edit_file** — you will get "not found" errors.
- **Skip read_file after write/edit** — unverified changes are broken changes.
- Guess file paths. Discover with find_files or list_directory first.
- Read entire large files (>1000 lines). Use offset/limit to read sections.
- Delete files unless explicitly asked or absolutely necessary.
- Write to locations outside the project structure unless requested.
"""


def register(agent) -> None:
    agent.tools.register_from_def(
        ToolDef(
            name="read_file",
            description="Read file contents from the filesystem. Returns text content. For large files, use offset and limit to read specific sections. Binary files return a size/type description instead of raw content.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-indexed). Default: 1.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read. Omit to read the entire file.",
                    },
                },
                "required": ["path"],
            },
            risk_level="read_only",
        ),
        read_file,
    )
    agent.tools.register_from_def(
        ToolDef(
            name="write_file",
            description="Create a new file or overwrite an existing one. Auto-creates parent directories. WARNING: Overwrites existing files without confirmation. Verify with read_file if unsure about current contents.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to create or overwrite.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
            risk_level="mutation",
        ),
        write_file,
    )
    agent.tools.register_from_def(
        ToolDef(
            name="list_directory",
            description="List files and subdirectories in a directory. Shows names, types (file/dir), and sizes. Use to understand project structure before making changes.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list. Default: current directory.",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter (e.g., '*.py').",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "List recursively if true. Default: false.",
                    },
                },
            },
            risk_level="read_only",
        ),
        list_directory,
    )
    agent.tools.register_from_def(
        ToolDef(
            name="find_files",
            description="Find files matching a glob pattern. Supports recursive wildcards like '**/*.py'. Use to discover file locations before reading them.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g., '**/*.py', 'src/**/*test*').",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in. Default: current directory.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results. Default: 200.",
                    },
                },
                "required": ["pattern"],
            },
            risk_level="read_only",
        ),
        find_files,
    )
    agent.tools.register_from_def(
        ToolDef(
            name="delete_file",
            description="Permanently delete a file. IRREVERSIBLE — cannot be undone. Prefer move_file to archive. Always confirm what you're deleting by reading or listing first.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to delete permanently.",
                    }
                },
                "required": ["path"],
            },
            risk_level="destructive",
        ),
        delete_file,
    )
    agent.tools.register_from_def(
        ToolDef(
            name="create_directory",
            description="Create a directory and any needed parent directories. Safe to call on existing directories (no error).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the directory to create.",
                    }
                },
                "required": ["path"],
            },
            risk_level="mutation",
        ),
        create_directory,
    )
    agent.tools.register_from_def(
        ToolDef(
            name="move_file",
            description="Move or rename a file or directory. Overwrites the destination if it already exists. Safer alternative to delete_file for removing files.",
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Current path of the file or directory.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "New path for the file or directory.",
                    },
                },
                "required": ["source", "destination"],
            },
            risk_level="mutation",
        ),
        move_file,
    )
    agent.tools.register_from_def(
        ToolDef(
            name="edit_file",
            description="Replace text in a file by exact string matching. When old_string is found exactly once, performs the replacement. When not found, returns best-matching lines with similarity scores. When found multiple times, returns all occurrences with surrounding context so you can add more context to make the match unique. Use replace_all=true to replace all occurrences.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to replace. Must match character-for-character including whitespace. If not found, use read_file first to verify content.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences if true. Default: false (replace single occurrence only).",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            risk_level="mutation",
        ),
        edit_file,
    )
    agent._prompt.append(FILES_PROMPT_SECTION)
