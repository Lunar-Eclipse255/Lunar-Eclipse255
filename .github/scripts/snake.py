"""Generate a contribution-grid snake that grows each time it eats a day with commits.

Reads the contribution calendar from the GitHub GraphQL API and writes an animated
SVG. The snake sweeps the grid column by column (down one week, up the next), so it
never crosses itself no matter how long it gets.

Env vars:
  GITHUB_TOKEN  token for the GraphQL API (the workflow's built-in token works)
  GH_USER       username whose contributions to draw
  OUT           output path (default dist/github-snake-dark.svg)
  MAX_LENGTH    cap on snake length, 0 = no cap (default 35)
  STEP_SECONDS  seconds per cell moved (default 0.08)
"""
import json
import os
import urllib.request

# Catppuccin Macchiato
BG_EMPTY = "#363a4f"                                   # Surface 0
LEVEL_COLORS = ["#8bd5ca", "#8aadf4", "#b7bdf8", "#c6a0f6"]  # Teal, Blue, Lavender, Mauve
SNAKE_HEAD = "#f5bde6"                                 # Pink
SNAKE_BODY = "#f5bde6"

CELL = 12
GAP = 4
PITCH = CELL + GAP
PAD = 16
ROWS = 7
START_LENGTH = 4

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


def cell_xy(col, row):
    return PAD + col * PITCH, PAD + row * PITCH


def build_path(grid, tail_room):
    """Serpentine path: enter from the left, sweep every week, exit off the right edge.

    Returns (positions, eat) where positions[i] is the pixel (x, y) of the head at step i
    and eat[i] is the (col, row) eaten at step i, or None.
    """
    cols = len(grid)
    positions, eat = [], []

    # Slide in from off-screen left along the top row.
    for k in range(START_LENGTH, 0, -1):
        positions.append(cell_xy(-k, 0))
        eat.append(None)

    for c in range(cols):
        rows = range(ROWS) if c % 2 == 0 else range(ROWS - 1, -1, -1)
        for r in rows:
            positions.append(cell_xy(c, r))
            level = grid[c][r]
            eat.append((c, r) if level else None)

    # Slide out to the right until the whole tail has left the grid.
    last_row = (ROWS - 1) if (cols - 1) % 2 == 0 else 0
    for k in range(1, tail_room + 2):
        positions.append(cell_xy(cols - 1 + k, last_row))
        eat.append(None)
    return positions, eat


def pct(step, total):
    return f"{100 * step / total:.4f}%"


def render(grid, step_s, max_len):
    eaten_days = sum(1 for col in grid for lvl in col if lvl)
    final_len = START_LENGTH + eaten_days
    if max_len:
        final_len = min(final_len, max_len)

    positions, eat = build_path(grid, final_len)
    n = len(positions)
    dur = n * step_s

    # Snake length after each step, and when each segment first appears.
    length, lengths = START_LENGTH, []
    for i in range(n):
        if eat[i]:
            length += 1
            if max_len:
                length = min(length, max_len)
        lengths.append(length)
    appear = {}
    for i in range(n):
        for k in range(lengths[i]):
            if k not in appear and i >= k:
                appear[k] = i
    seg_count = max(lengths)

    width = PAD * 2 + len(grid) * PITCH - GAP
    height = PAD * 2 + ROWS * PITCH - GAP
    css, dots, segs = [], [], []

    # Head path, shared by every segment (each one is delayed by its index).
    frames = [f"{pct(i, n)}{{transform:translate({x}px,{y}px)}}" for i, (x, y) in enumerate(positions)]
    lx, ly = positions[-1]
    frames.append(f"100%{{transform:translate({lx}px,{ly}px)}}")
    css.append("@keyframes mv{" + "".join(frames) + "}")
    css.append(f".s{{animation-duration:{dur:.2f}s;animation-iteration-count:infinite}}")

    # Dots: colored until the head reaches them, then empty until the loop restarts.
    eat_step = {cell: i for i, cell in enumerate(eat) if cell}
    for c, col in enumerate(grid):
        for r, lvl in enumerate(col):
            if lvl is None:
                continue
            x, y = cell_xy(c, r)
            if lvl == 0:
                dots.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="3" fill="{BG_EMPTY}"/>')
                continue
            color = LEVEL_COLORS[lvl - 1]
            i = eat_step[(c, r)]
            name = f"d{c}_{r}"
            css.append(
                f"@keyframes {name}{{0%,{pct(i, n)}{{fill:{color}}}"
                f"{pct(i + 0.01, n)},100%{{fill:{BG_EMPTY}}}}}"
                f".{name}{{animation:{name} {dur:.2f}s linear infinite}}"
            )
            dots.append(f'<rect class="{name}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="3" fill="{color}"/>')

    # Segments: drawn tail-first so the head sits on top.
    for k in reversed(range(seg_count)):
        a = appear[k]
        name = f"g{k}"
        css.append(
            f"@keyframes {name}{{0%,{pct(a, n)}{{opacity:0}}{pct(a + 0.01, n)},100%{{opacity:1}}}}"
            f".{name}{{animation-name:mv,{name};animation-timing-function:linear,linear;"
            f"animation-delay:{k * step_s:.2f}s,0s}}"
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
