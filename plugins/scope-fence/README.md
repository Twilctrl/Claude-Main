# scope-fence

"Fix the pagination bug in the API" shouldn't come back with edits to the
build config, three unrelated components and the README. scope-fence lets you
draw a line around what a task is about and keeps Claude's edits inside it.

## Install

```
/plugin marketplace add twilctrl/claude-main
/plugin install scope-fence@claude-main
```

## Use

```
/scope-fence:fence set src/api/** tests/api/
/scope-fence:fence budget 5
```

Now an edit to `package.json` is refused, with a reason that tells Claude to
stop and ask you instead of finding another way. And when it reaches for a
sixth distinct file this session, you get a permission prompt listing the five
it already changed.

| Command | What it does |
| --- | --- |
| `/scope-fence:fence` | Show the fence, budget and mode for this project |
| `/scope-fence:fence set <glob>...` | Fence edits to these paths |
| `/scope-fence:fence add <glob>...` / `drop <glob>...` | Widen or narrow the fence |
| `/scope-fence:fence budget <n\|off>` | Ask before changing more than `n` files per session |
| `/scope-fence:fence mode deny\|ask` | Out-of-fence edits are refused (default) or put to you |
| `/scope-fence:fence on` / `off` | Toggle without losing settings |
| `/scope-fence:fence clear` | Forget everything for this project |

The fence and budget are independent: use either or both.

## Details

- Globs match the path relative to the project root. `*` crosses directories,
  and a bare directory (`tests/api/`) covers everything below it. Files outside
  the project are always outside a fence.
- Settings are stored per project in `${CLAUDE_PLUGIN_DATA}` (or
  `~/.claude/plugin-data/scope-fence/`), never in the repo — fences are
  usually about one task, not something to commit.
- The budget counts distinct files successfully changed in the current
  session. Re-editing a file already on the list is always free.

## Limits

This covers Claude's file-editing tools. A shell command (`sed -i`, `>`
redirects) isn't parsed; the denial message asks Claude not to route around
the fence, but pair it with Bash permissions if you need a hard guarantee.

Hooks fail open: a broken config means the normal permission flow.

## Requirements

`python3` on `PATH` (standard library only).
