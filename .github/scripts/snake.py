"""Generate a contribution-grid snake that hunts down your commits and grows as it eats.

Reads the contribution calendar from the GitHub GraphQL API and writes an animated SVG.
Each step the snake heads for a nearby uneaten day it can safely reach (working left to
right so it doesn't leave stragglers behind), steering around its own body, and only takes a path if it can still reach its tail afterwards
(so it never boxes itself in). It never leaves the graph.

Env vars:
  GITHUB_TOKEN  token for the GraphQL API (the workflow's built-in token works)
  GH_USER       username whose contributions to draw
  OUT           output path (default dist/github-snake-dark.svg)
  MAX_LENGTH    final snake length, 0 = one segment per day eaten (default 35)
  STEP_SECONDS  seconds per cell moved (default 0.08)
"""
import json
import os
import urllib.request
from collections import deque

# Catppuccin Macchiato
BG_EMPTY = "#554b72"  # empty / eaten days: 30% Mauve over Base, so no grey
LEVEL_COLORS = ["#8bd5ca", "#8aadf4", "#b7bdf8", "#f5bde6"]  # Teal, Blue, Lavender, Pink
SNAKE_HEAD = "#c6a0f6"  # Mauve
SNAKE_BODY = "#c6a0f6"

CELL = 12
GAP = 3
PITCH = CELL + GAP
PAD = 4
ROWS = 7
START_LENGTH = 4
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))

LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks {
          contributionDays { weekday contributionLevel }
        }
      }
    }
  }
}
"""


def fetch_grid(user, token):
    """Return grid[col][row] -> level (0-4), or None where the calendar has no day."""
    body = json.dumps({"query": QUERY, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    if "errors" in data:
        raise SystemExit(f"GraphQL error: {data['errors']}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    grid = []
    for week in weeks:
        col = [None] * ROWS
        for day in week["contributionDays"]:
            col[day["weekday"]] = LEVELS[day["contributionLevel"]]
        grid.append(col)
    return grid


def cell_xy(cell):
    c, r = cell
    return PAD + c * PITCH, PAD + r * PITCH


# ---------------------------------------------------------------- path planning


def neighbors(cell, valid):
    c, r = cell
    for dc, dr in DIRS:
        n = (c + dc, r + dr)
        if n in valid:
            yield n


def bfs(start, goal_fn, valid, blocked):
    """Shortest path from start to the nearest cell where goal_fn is true.

    Returns the path excluding start, or None if no goal is reachable.
    """
    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur != start and goal_fn(cur):
            path = []
            while cur != start:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]
        for n in neighbors(cur, valid):
            if n not in prev and n not in blocked:
                prev[n] = cur
                q.append(n)
    return None


def path_to_best_food(start, food, valid, blocked):
    """Path to the food with the best score: close by, but it won't skip days behind it.

    Pure nearest-food leaves stragglers on the left that the snake later has to trek
    back for, so food further right than the leftmost uneaten day costs extra.
    """
    prev = {start: None}
    dist = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        for n in neighbors(cur, valid):
            if n not in prev and n not in blocked:
                prev[n] = cur
                dist[n] = dist[cur] + 1
                q.append(n)
    reachable = [f for f in food if f in dist]
    if not reachable:
        return None
    left = min(f[0] for f in reachable)
    target = min(reachable, key=lambda f: (dist[f] + 0.6 * (f[0] - left), f))
    path = []
    cur = target
    while cur != start:
        path.append(cur)
        cur = prev[cur]
    return path[::-1]


def open_space(start, valid, blocked):
    """How many cells can be reached from start without crossing the body."""
    seen = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        for n in neighbors(cur, valid):
            if n not in seen and n not in blocked:
                seen.add(n)
                q.append(n)
    return len(seen)


def simulate(body, path, length_after):
    """Body (head first) after walking path, growing to length_after along the way."""
    trail = list(reversed(path)) + list(body)
    return trail[:length_after]


def can_reach_tail(body, valid):
    """True if the head can get to where the tail currently is."""
    if len(body) < 3:
        return True
    head, tail = body[0], body[-1]
    blocked = set(body[1:-1])
    return bfs(head, lambda c: c == tail, valid, blocked) is not None


def plan(grid, final_len):
    """Head positions, one per step, plus the food eaten at each step."""
    valid = {(c, r) for c, col in enumerate(grid) for r, lvl in enumerate(col) if lvl is not None}
    food = {(c, r) for c, col in enumerate(grid) for r, lvl in enumerate(col) if lvl}
    total_food = len(food)
    grow = (final_len - START_LENGTH) / total_food if total_food else 0

    def target_len(eaten):
        return START_LENGTH + int(round(eaten * grow))

    start = min(valid)  # first day of the calendar
    body = deque([start])
    heads, eat = [start], [start if start in food else None]
    food.discard(start)
    eaten = 1 if eat[0] else 0
    limit = len(valid) * 20
    hungry = 0  # steps since the last meal

    while food and len(heads) < limit:
        length = target_len(eaten)
        # The tail cell frees up as we move, unless we're still growing into it.
        blocked = set(list(body)[:-1]) if len(body) >= length else set(body)

        step = None
        path = path_to_best_food(body[0], food, valid, blocked)
        if path:
            eaten_on_path = sum(1 for c in path if c in food)
            after = simulate(body, path, target_len(eaten + eaten_on_path))
            # If it has been circling too long, go for the food even without the
            # tail check, so the snake can't chase its tail forever.
            if can_reach_tail(after, valid) or hungry > 2 * len(body) + 10:
                step = path[0]
        if step is None and len(body) >= length:
            # No safe meal right now: follow the tail to buy time.
            tail_path = bfs(body[0], lambda c: c == body[-1], valid, set(list(body)[1:-1]))
            if tail_path:
                step = tail_path[0]
        if step is None:
            # Otherwise move to the free neighbor with the most open space around it.
            free = [nb for nb in neighbors(body[0], valid) if nb not in blocked]
            if free:
                step = max(free, key=lambda nb: (open_space(nb, valid, blocked), nb))
            else:
                step = next(neighbors(body[0], valid))  # boxed in (very rare)

        body.appendleft(step)
        if step in food:
            food.discard(step)
            eaten += 1
            hungry = 0
            eat.append(step)
        else:
            hungry += 1
            eat.append(None)
        while len(body) > target_len(eaten):
            body.pop()
        heads.append(step)

    return heads, eat, target_len


# ---------------------------------------------------------------- rendering


def pct(step, total):
    return f"{100 * step / total:.4f}%"


def render(grid, step_s, max_len):
    total_food = sum(1 for col in grid for lvl in col if lvl)
    final_len = START_LENGTH + total_food
    if max_len:
        final_len = max(START_LENGTH, min(final_len, max_len))

    heads, eat, target_len = plan(grid, final_len)

    # Hold at the end: the head stays put and the body curls into it, then a short pause.
    pause_steps = int(round(1.5 / step_s))
    for _ in range(final_len + pause_steps):
        heads.append(heads[-1])
        eat.append(None)

    n = len(heads)
    dur = n * step_s

    eaten, lengths = 0, []
    for i in range(n):
        if eat[i]:
            eaten += 1
        lengths.append(target_len(eaten))
    appear = {}
    for i in range(n):
        for k in range(lengths[i]):
            if k not in appear and i >= k:
                appear[k] = i
    seg_count = max(lengths)

    width = PAD * 2 + len(grid) * PITCH - GAP
    height = PAD * 2 + ROWS * PITCH - GAP
    css, dots, segs = [], [], []

    # Head path, shared by every segment (each one is delayed by its index). step-end
    # makes the snake jump cleanly cell to cell on whole pixels, so it stays sharp.
    frames = []
    for i, cell in enumerate(heads):
        if i and cell == heads[i - 1]:
            continue
        x, y = cell_xy(cell)
        frames.append(f"{pct(i, n)}{{transform:translate({x}px,{y}px)}}")
    lx, ly = cell_xy(heads[-1])
    frames.append(f"100%{{transform:translate({lx}px,{ly}px)}}")
    css.append("@keyframes mv{" + "".join(frames) + "}")
    css.append(
        f".s{{animation-duration:{dur:.2f}s;animation-iteration-count:infinite;"
        f"animation-timing-function:step-end}}"
    )

    # Dots: colored until the head reaches them, then the empty tint until the loop restarts.
    eat_step = {cell: i for i, cell in enumerate(eat) if cell}
    for c, col in enumerate(grid):
        for r, lvl in enumerate(col):
            if lvl is None:
                continue
            x, y = cell_xy((c, r))
            if lvl == 0 or (c, r) not in eat_step:
                dots.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="3" fill="{BG_EMPTY}"/>')
                continue
            color = LEVEL_COLORS[lvl - 1]
            i = eat_step[(c, r)]
            name = f"d{c}_{r}"
            css.append(
                f"@keyframes {name}{{0%{{fill:{color}}}{pct(i, n)}{{fill:{BG_EMPTY}}}100%{{fill:{BG_EMPTY}}}}}"
                f".{name}{{animation:{name} {dur:.2f}s step-end infinite}}"
            )
            dots.append(f'<rect class="{name}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="3" fill="{color}"/>')

    # Segments: drawn tail-first so the head sits on top.
    for k in reversed(range(seg_count)):
        a = appear[k]
        name = f"g{k}"
        css.append(
            f"@keyframes {name}{{0%{{opacity:0}}{pct(a, n)}{{opacity:1}}100%{{opacity:1}}}}"
            f".{name}{{animation-name:mv,{name};animation-delay:{k * step_s:.2f}s,0s}}"
        )
        color = SNAKE_HEAD if k == 0 else SNAKE_BODY
        inset = 0 if k == 0 else 1
        size = CELL - 2 * inset
        segs.append(
            f'<rect class="s {name}" x="{inset}" y="{inset}" width="{size}" height="{size}" '
            f'rx="4" fill="{color}" opacity="0"/>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">'
        f"<style>{''.join(css)}</style>"
        + "".join(dots)
        + "".join(segs)
        + "</svg>"
    )


def main():
    user = os.environ["GH_USER"]
    token = os.environ["GITHUB_TOKEN"]
    out = os.environ.get("OUT", "dist/github-snake-dark.svg")
    max_len = int(os.environ.get("MAX_LENGTH", "35"))
    step_s = float(os.environ.get("STEP_SECONDS", "0.08"))

    grid = fetch_grid(user, token)
    svg = render(grid, step_s, max_len)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        f.write(svg)
    print(f"wrote {out} ({len(svg) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
