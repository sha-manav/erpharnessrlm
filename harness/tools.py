"""Tool schemas exposed to the model (PLAN.md P2.7).

Which tools a config exposes is the experiment: `B_bash` gets bash and finish, `C_full`
gets the kernel, bash, paging and finish. The schemas live here so the two configs differ
only by a list in `configs.yaml`, never by prompt wording.

Descriptions are deliberately short. They are re-sent with every request for the whole
trajectory, so a paragraph here costs more than it does anywhere else in the harness.
"""

from __future__ import annotations

PYTHON = {
    "type": "function",
    "function": {
        "name": "python",
        "description": (
            "Run Python in a persistent kernel inside the Odoo container. Variables and "
            "imports survive between calls. The harness library (erp, check, plan, ...) is "
            "already loaded. Print what you want to see; nothing is returned implicitly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute."},
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before the kernel is restarted (default 120).",
                },
            },
            "required": ["code"],
        },
    },
}

BASH = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Run a shell command in the Odoo container. Each call is a fresh shell: no "
            "directory or variable survives between calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Command to run."},
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before the command is killed (default 120).",
                },
            },
            "required": ["cmd"],
        },
    },
}

SHOW = {
    "type": "function",
    "function": {
        "name": "show",
        "description": (
            "Read one page of an output that was truncated. Handles look like 'h3' and are "
            "named in the truncation notice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "Handle from a truncation notice."},
                "page": {"type": "integer", "description": "1-based page number (default 1)."},
            },
            "required": ["handle"],
        },
    },
}

FINISH = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": (
            "End the task. Runs the end-state checks first and refuses while a hard check "
            "fails, returning what is wrong so you can fix it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "What you changed and why, in a few sentences.",
                },
            },
            "required": ["summary"],
        },
    },
}

ALL = {"python": PYTHON, "bash": BASH, "show": SHOW, "finish": FINISH}


def schemas_for(names: list[str]) -> list[dict]:
    """The tool schemas for a config's tool list, in the order given."""
    unknown = [name for name in names if name not in ALL]
    if unknown:
        raise ValueError(f"unknown tool(s) {unknown}; have {sorted(ALL)}")
    return [ALL[name] for name in names]
