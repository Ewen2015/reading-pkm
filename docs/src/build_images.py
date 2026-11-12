#!/usr/bin/env python3
"""Rebuild the README images in docs/assets/ from their sources.

  profile.png    <- docs/assets/profile-raw.png   (Chrome screenshot of the 小红书 profile,
                                             left sidebar cropped away)
  case-post.png  <- ~/Documents/rednote/published/creative-act-2026-10-06/
  covers.png     <- 01-封面.png of six published posts

Run:  python3 docs/src/build_images.py

profile-raw.png is a local capture (needs a logged-in browser) and is gitignored,
so the profile step only runs on machines that have it.
"""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "docs" / "assets"
PUB = Path.home() / "Documents" / "rednote" / "published"
PAPER = (243, 238, 229)


def fit(im: Image.Image, width: int) -> Image.Image:
    h = round(im.height * width / im.width)
    return im.resize((width, h), Image.LANCZOS)


def strip(paths, card_w, gap, pad, out):
    cards = [fit(Image.open(p).convert("RGB"), card_w) for p in paths]
    h = max(c.height for c in cards)
    W = pad * 2 + card_w * len(cards) + gap * (len(cards) - 1)
    H = pad * 2 + h
    canvas = Image.new("RGB", (W, H), PAPER)
    x = pad
    for c in cards:
        canvas.paste(c, (x, pad))
        x += card_w + gap
    canvas.save(ASSETS / out, optimize=True)
    print(f"{out}: {canvas.width}x{canvas.height}  "
          f"{len(cards)} cards from {[Path(p).parent.name for p in paths]}")


# --- profile: drop the site's left navigation rail and menu ---------------
raw = Image.open(ASSETS / "profile-raw.png")
prof = fit(raw.crop((444, 0, raw.width, 1700)), 1800)
prof.save(ASSETS / "profile.png", optimize=True)
print(f"profile.png: {prof.width}x{prof.height}")

# --- one post, all five cards ---------------------------------------------
post = PUB / "creative-act-2026-10-06"
strip(sorted(post.glob("*.png")), card_w=420, gap=26, pad=34, out="case-post.png")

# --- six covers from six different books ----------------------------------
books = ["naval-2026-10-07", "pale-blue-dot-2026-10-08",
         "creative-act-2026-10-06", "socratic-apology-2026-10-19",
         "road-to-serfdom-2026-10-20", "historical-regimes-2026-10-15"]
strip([PUB / b / "01-封面.png" for b in books], card_w=350, gap=22, pad=34,
      out="covers.png")
