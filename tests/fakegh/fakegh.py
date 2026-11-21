"""Fake `gh` for tests: state and call log in the JSON file named by $FAKEGH_STATE.

State keys: "user" (login), "issues" ({number: issue}), "prs" ([pr]), "fail" ({"<cmd> <sub>":
"stderr text"} makes that command exit 1), "calls" (appended: {"argv": [...], "stdin": text|null}).
Only the subcommands Atta uses are implemented; anything else exits 1.
"""

import json
import os
import sys


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(path, state):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def _opt(argv, name, default=None):
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return default


def _pick(obj, fields):
    if not fields:
        return obj
    return {k: obj.get(k) for k in fields.split(",")}


def _out(data):
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")


def _pr(state, ref):
    for pr in state["prs"]:
        if str(pr["number"]) == str(ref) or pr.get("url") == ref:
            return pr
    sys.stderr.write(f"no pull requests found for {ref}\n")
    sys.exit(1)


def main(argv):
    # The real gh (Go) reads and writes UTF-8 on every OS; so does the fake.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    path = os.environ["FAKEGH_STATE"]
    state = _load(path)
    state.setdefault("calls", [])
    state.setdefault("issues", {})
    state.setdefault("prs", [])
    stdin = sys.stdin.read() if "-" in argv and _opt(argv, "--body-file") == "-" else None
    state["calls"].append({"argv": argv, "stdin": stdin})
    _save(path, state)

    key = " ".join(argv[:2])
    if key in state.get("fail", {}):
        sys.stderr.write(state["fail"][key] + "\n")
        return 1
    fields = _opt(argv, "--json")

    if key == "api user":
        sys.stdout.write(state.get("user", "operador") + "\n")
    elif key == "issue list":
        label = _opt(argv, "--label")
        rows = [i for i in state["issues"].values()
                if i.get("state", "OPEN") == "OPEN"
                and (label is None or label in [lb["name"] for lb in i.get("labels", [])])]
        _out([_pick(i, fields) for i in sorted(rows, key=lambda i: i["number"])])
    elif key == "issue view":
        issue = state["issues"].get(str(argv[2]))
        if issue is None:
            sys.stderr.write(f"Could not resolve to an issue with the number of {argv[2]}.\n")
            return 1
        _out(_pick(issue, fields))
    elif key in ("issue comment", "pr comment"):
        state["comments"] = state.get("comments", [])
        state["comments"].append({"on": argv[0], "number": argv[2], "body": stdin})
        sys.stdout.write(f"https://github.com/o/r/issues/{argv[2]}#issuecomment-1\n")
    elif key in ("issue edit", "pr edit"):
        target = state["issues"].setdefault(str(argv[2]), {"number": int(argv[2]), "labels": []})
        labels = target.setdefault("labels", [])
        add, remove = _opt(argv, "--add-label"), _opt(argv, "--remove-label")
        if add and add not in [lb["name"] for lb in labels]:
            labels.append({"name": add})
        if remove:
            target["labels"] = [lb for lb in labels if lb["name"] != remove]
    elif key == "pr list":
        head = _opt(argv, "--head")
        rows = [p for p in state["prs"] if p.get("state", "OPEN") == "OPEN"
                and (head is None or p["headRefName"] == head)]
        _out([_pick(p, fields) for p in rows])
    elif key == "pr create":
        number = 100 + len(state["prs"]) + 1
        url = f"https://github.com/o/r/pull/{number}"
        state["prs"].append({"number": number, "url": url, "state": "OPEN",
                             "baseRefName": _opt(argv, "--base"),
                             "headRefName": _opt(argv, "--head"),
                             "isDraft": "--draft" in argv, "title": _opt(argv, "--title"),
                             "body": stdin})
        sys.stdout.write(url + "\n")
    elif key == "pr view":
        _out(_pick(_pr(state, argv[2]), fields))
    elif key == "pr ready":
        _pr(state, argv[2])["isDraft"] = False
    elif key == "pr merge":
        _pr(state, argv[2])["state"] = "MERGED"
    else:
        sys.stderr.write(f"fakegh: comando no soportado: {' '.join(argv)}\n")
        return 1
    _save(path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
