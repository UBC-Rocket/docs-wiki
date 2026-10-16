#!/usr/bin/env python3
"""
Convert the UBC Rocket MkDocs site (UBC-Rocket/docs, folder `docs/`) into a
folder tree that Wiki.js 2.x can import through its Git storage module.

    python scripts/convert_mkdocs.py --src ../docs/docs --out ../wiki-content

What it does
------------
* Paths are slugified (lowercase, spaces -> "-") so every page and asset has a
  clean URL. `a/b/index.md` becomes `a/b.md` (Wiki.js has no index pages; a page
  can share a name with a folder). The site root `index.md` becomes `home.md`.
* Adds the YAML front matter Wiki.js expects (title, published, editor...).
  Title comes from: existing front matter -> first `# H1` -> `.pages` title
  (for folder landing pages) -> the file name. The leading H1 is removed because
  Wiki.js already prints the title above the page.
* `!!! type "Title"` / `??? type` admonitions -> Wiki.js callouts
  (blockquote + `{.is-info}` / `{.is-success}` / `{.is-warning}` / `{.is-danger}`).
* Relative links to pages, images and files are rewritten to absolute Wiki.js
  paths, so they work no matter which page renders them.
* `![embed](...)` / `![iframe](...)` (the MkDocs iframe hook) -> a plain image
  for image files, otherwise an <iframe> plus a fallback link.
  (Enable iframes in Admin -> Rendering -> HTML -> Security.)
* Every PDF without a page of the same name gets a small wrapper page that
  embeds it, matching what the MkDocs `pdf_pages` hook did. Use --no-pdf-pages
  to skip this.
* `\\set{...}` (MathJax braket) -> `\\{...\\}` so the math renders in KaTeX.
* Files in content-overrides/ (next to this script's parent folder) replace the
  converted page at the same output path, e.g. a Wiki.js-specific contributing
  guide.

Things it cannot carry over are listed in the report printed at the end.
Standard library only; Python 3.9+.
"""

from __future__ import annotations

import argparse
import datetime as dt
import posixpath
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

SKIP_DIRS = {"javascripts", "stylesheets", ".git"}
SKIP_FILE_NAMES = {".pages", ".DS_Store", "Thumbs.db"}
SKIP_FILE_RE = re.compile(r"^empty \(for git to track\)\.txt$", re.IGNORECASE)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}

CALLOUT = {
    "note": "is-info", "info": "is-info", "abstract": "is-info",
    "summary": "is-info", "tldr": "is-info", "todo": "is-info", "seealso": "is-info",
    "tip": "is-success", "hint": "is-success", "important": "is-success",
    "success": "is-success", "check": "is-success", "done": "is-success",
    "example": "is-success",
    "warning": "is-warning", "caution": "is-warning", "attention": "is-warning",
    "question": "is-warning", "help": "is-warning", "faq": "is-warning",
    "danger": "is-danger", "error": "is-danger", "failure": "is-danger",
    "fail": "is-danger", "missing": "is-danger", "bug": "is-danger",
}

ADMON_RE = re.compile(r'^(?P<indent>\s*)(?:!!!|\?\?\?\+?)\s+(?P<type>[\w-]+)(?:\s+"(?P<title>[^"]*)")?\s*$')
TABS_RE = re.compile(r'^\s*===\+?\s+"')
FENCE_RE = re.compile(r"^\s*(```|~~~)")
# ![alt](<target with spaces> "title")  or  [text](target "title")
LINK_RE = re.compile(
    r'(?P<bang>!?)\[(?P<text>(?:[^\[\]]|\[[^\]]*\])*)\]'
    r'\((?P<target><[^>]+>|[^)\s]+)(?P<title>\s+"[^"]*")?\)'
)
H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$")


# --------------------------------------------------------------------------- paths
def slug(part: str) -> str:
    s = part.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9._\-]", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "untitled"


def slug_path(rel: PurePosixPath) -> PurePosixPath:
    return PurePosixPath(*[slug(p) for p in rel.parts])


def page_out_path(rel_md: PurePosixPath) -> PurePosixPath:
    """docs-relative .md path -> output .md path (index handling + slugs)."""
    if rel_md.name.lower() == "index.md":
        parent = rel_md.parent
        if str(parent) in ("", "."):
            return PurePosixPath("home.md")
        return slug_path(parent).with_suffix(".md")
    s = slug_path(rel_md)
    return s.with_name(s.stem + ".md")


def wiki_url(out_rel: PurePosixPath, is_page: bool) -> str:
    p = str(out_rel)
    if is_page:
        p = p[:-3] if p.endswith(".md") else p
    return "/" + quote(p, safe="/._-")


# --------------------------------------------------------------------------- helpers
def read_pages_title(folder: Path) -> str | None:
    f = folder / ".pages"
    if not f.exists():
        return None
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^\s*title\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def pretty(name: str) -> str:
    s = re.sub(r"[-_]+", " ", Path(name).stem)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:1].upper() + s[1:] if s else "Untitled"


def split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = {}
            ok = True
            for ln in lines[1:i]:
                if not ln.strip():
                    continue
                m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", ln.rstrip())
                if not m:
                    ok = False
                    break
                meta[m.group(1)] = m.group(2).strip().strip("'\"")
            if ok and meta:
                return meta, "".join(lines[i + 1:])
            return {}, text  # a thematic break, not front matter
    return {}, text


def yaml_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def replace_set_macro(s: str) -> str:
    """\\set{...} -> \\{...\\}, with brace matching (KaTeX has no \\set)."""
    out, i, key = [], 0, "\\set{"
    while True:
        j = s.find(key, i)
        if j < 0:
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:j])
        depth, k = 1, j + len(key)
        while k < len(s) and depth:
            depth += {"{": 1, "}": -1}.get(s[k], 0)
            k += 1
        inner = s[j + len(key):k - 1]
        out.append("\\{" + inner + "\\}")
        i = k


# --------------------------------------------------------------------------- converter
class Converter:
    def __init__(self, src: Path, out: Path, overrides: Path | None, pdf_pages: bool):
        self.src, self.out, self.overrides, self.pdf_pages = src, out, overrides, pdf_pages
        self.page_map: dict[PurePosixPath, PurePosixPath] = {}   # src rel -> out rel (.md)
        self.asset_map: dict[PurePosixPath, PurePosixPath] = {}  # src rel -> out rel
        self.report: dict[str, list[str]] = {k: [] for k in (
            "broken_links", "tabs", "html", "collisions", "overrides", "pdf_pages", "big_assets")}
        self.now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # ---- discovery
    def walk(self):
        for p in sorted(self.src.rglob("*")):
            rel = PurePosixPath(p.relative_to(self.src).as_posix())
            if any(part in SKIP_DIRS for part in rel.parts) or p.is_dir():
                continue
            if p.name in SKIP_FILE_NAMES or SKIP_FILE_RE.match(p.name):
                continue
            yield p, rel

    def plan(self):
        taken: dict[PurePosixPath, PurePosixPath] = {}
        for p, rel in self.walk():
            if p.suffix.lower() == ".md":
                out = page_out_path(rel)
                target = self.page_map
            else:
                out = slug_path(rel)
                target = self.asset_map
            if out in taken:
                self.report["collisions"].append(f"{rel}  and  {taken[out]}  ->  {out}")
                out = out.with_name(out.stem + "-2" + out.suffix)
            taken[out] = rel
            target[rel] = out
        # A page "a/b.md" and an asset "a/b.<ext>" are fine; a page and folder share names fine.

    # ---- link rewriting
    def resolve(self, page_src: PurePosixPath, target: str) -> str | None:
        """Return absolute Wiki.js URL for a relative target, or None if not ours."""
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/", "mailto:")):
            return None
        path, _, frag = target.partition("#")
        path = unquote(path)
        if not path:
            return None
        base = page_src.parent
        joined = PurePosixPath(posixpath.normpath(str(base / path)))
        frag = ("#" + frag) if frag else ""
        if joined in self.page_map:
            return wiki_url(self.page_map[joined], True) + frag
        if joined in self.asset_map:
            return wiki_url(self.asset_map[joined], False) + frag
        # "folder/" style links -> folder/index.md
        idx = joined / "index.md"
        if idx in self.page_map:
            return wiki_url(self.page_map[idx], True) + frag
        if joined.with_suffix(".md") in self.page_map:
            return wiki_url(self.page_map[joined.with_suffix(".md")], True) + frag
        return ""  # ours but missing

    def rewrite_links(self, page_src: PurePosixPath, line: str) -> str:
        def sub(m: re.Match) -> str:
            raw = m.group("target")
            target = raw[1:-1] if raw.startswith("<") else raw
            url = self.resolve(page_src, target)
            text, bang, title = m.group("text"), m.group("bang"), m.group("title") or ""
            if url == "":
                self.report["broken_links"].append(f"{page_src}: {target}")
                return m.group(0)
            final = url if url is not None else target
            if bang and text.strip().lower() in ("embed", "iframe"):
                ext = PurePosixPath(final.split("#")[0]).suffix.lower()
                if ext in IMAGE_EXT:
                    return f"![{PurePosixPath(unquote(final)).stem}]({final})"
                return (f'<iframe src="{final}" width="100%" height="800" loading="lazy" '
                        f'style="border:1px solid #ccc"></iframe>\n\n[Open in a new tab]({final})')
            if url is None:
                return m.group(0)
            return f"{bang}[{text}]({final}{title})"
        return LINK_RE.sub(sub, line)

    # ---- body conversion
    def convert_body(self, page_src: PurePosixPath, body: str) -> str:
        lines = body.splitlines()
        out: list[str] = []
        in_fence = False
        i = 0
        while i < len(lines):
            line = lines[i]
            if FENCE_RE.match(line):
                in_fence = not in_fence
                out.append(line)
                i += 1
                continue
            if in_fence:
                out.append(line)
                i += 1
                continue

            m = ADMON_RE.match(line)
            if m:
                base = len(m.group("indent"))
                kind = m.group("type").lower()
                cls = CALLOUT.get(kind, "is-info")
                title = m.group("title")
                if title is None:
                    title = kind.capitalize()
                block: list[str] = []
                i += 1
                while i < len(lines):
                    ln = lines[i]
                    if ln.strip() == "":
                        block.append("")
                        i += 1
                        continue
                    lead = len(ln) - len(ln.lstrip(" "))
                    if lead >= base + 4 or ln.startswith("\t"):
                        block.append(ln[base + 4:] if not ln.startswith("\t") else ln[1:])
                        i += 1
                    else:
                        break
                while block and block[-1] == "":
                    block.pop()
                pad = " " * base
                if title:
                    out.append(f"{pad}> **{title}**")
                    if block:
                        out.append(f"{pad}>")
                inner = self.convert_body(page_src, "\n".join(block)).splitlines() if block else []
                out.extend(f"{pad}> {b}".rstrip() for b in inner)
                out.append(f"{pad}{{.{cls}}}")
                out.append("")
                continue

            if TABS_RE.match(line):
                self.report["tabs"].append(f"{page_src}: {line.strip()}")
            if re.search(r"<(div|details|span|iframe)\b", line, re.I):
                self.report["html"].append(f"{page_src}: {line.strip()[:80]}")

            out.append(self.rewrite_links(page_src, line))
            i += 1
        return "\n".join(out)

    def title_for(self, src_path: Path, rel: PurePosixPath, meta: dict, body: str) -> tuple[str, str]:
        if meta.get("title"):
            return meta["title"], body
        lines = body.lstrip("\n").splitlines()
        if lines:
            m = H1_RE.match(lines[0])
            if m:
                rest = "\n".join(lines[1:]).lstrip("\n")
                return m.group(1).strip(), rest
        if rel.name.lower() == "index.md":
            t = read_pages_title(src_path.parent)
            if t:
                return t, body
            if str(rel.parent) in ("", "."):
                return "Home", body
            return pretty(rel.parent.name), body
        return pretty(rel.name), body

    def front_matter(self, title: str, description: str = "") -> str:
        return (
            "---\n"
            f"title: {yaml_str(title)}\n"
            f"description: {yaml_str(description)}\n"
            "published: true\n"
            f"date: {self.now}\n"
            "tags: \n"
            "editor: markdown\n"
            f"dateCreated: {self.now}\n"
            "---\n\n"
        )

    # ---- run
    def run(self):
        if self.out.exists() and any(self.out.iterdir()):
            sys.exit(f"Output folder {self.out} is not empty. Pick a new folder or empty it first.")
        self.out.mkdir(parents=True, exist_ok=True)
        self.plan()
        overridden = set()
        if self.overrides and self.overrides.is_dir():
            overridden = {PurePosixPath(f.relative_to(self.overrides).as_posix())
                          for f in self.overrides.rglob("*.md")}

        for rel, out_rel in self.page_map.items():
            if out_rel in overridden:
                continue  # replaced by a hand-written page below
            src_path = self.src / Path(*rel.parts)
            text = src_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            meta, body = split_front_matter(text)
            title, body = self.title_for(src_path, rel, meta, body)
            body = replace_set_macro(self.convert_body(rel, body)).rstrip() + "\n"
            dest = self.out / Path(*out_rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(self.front_matter(title, meta.get("description", "")) + body,
                            encoding="utf-8", newline="\n")

        for rel, out_rel in self.asset_map.items():
            src_path = self.src / Path(*rel.parts)
            dest = self.out / Path(*out_rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest)
            if src_path.stat().st_size > 5 * 1024 * 1024:
                self.report["big_assets"].append(f"{out_rel} ({src_path.stat().st_size / 1e6:.1f} MB)")

        if self.pdf_pages:
            for rel, out_rel in self.asset_map.items():
                if out_rel.suffix.lower() != ".pdf":
                    continue
                page = out_rel.with_suffix(".md")
                if (self.out / Path(*page.parts)).exists():
                    continue
                url = wiki_url(out_rel, False)
                body = (f'<iframe src="{url}" width="100%" height="800" loading="lazy" '
                        f'style="border:1px solid #ccc"></iframe>\n\n[Open / download the PDF]({url})\n')
                (self.out / Path(*page.parts)).write_text(
                    self.front_matter(pretty(rel.name), "PDF") + body, encoding="utf-8", newline="\n")
                self.report["pdf_pages"].append(str(page))

        if self.overrides and self.overrides.is_dir():
            for f in sorted(self.overrides.rglob("*")):
                if f.is_file() and f.name not in ("README.md",):
                    rel = f.relative_to(self.overrides)
                    dest = self.out / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    self.report["overrides"].append(rel.as_posix())

        self.print_report()

    def print_report(self):
        r = self.report
        print(f"\nConverted {len(self.page_map)} pages and {len(self.asset_map)} assets into {self.out}")
        print(f"  PDF wrapper pages generated: {len(r['pdf_pages'])}")
        print(f"  Overrides applied:           {', '.join(r['overrides']) or 'none'}")
        sections = [
            ("collisions", "Path collisions after slugifying (renamed with -2)"),
            ("broken_links", "Relative links whose target doesn't exist (left unchanged)"),
            ("tabs", "Content tabs (=== \"...\") - convert by hand to a {.tabset} heading"),
            ("html", "Raw HTML - check it renders (Wiki.js sanitizes HTML)"),
            ("big_assets", "Assets over 5 MB - fine via Git import, but raise Admin > General > Uploads limit to re-upload in the editor"),
        ]
        for key, label in sections:
            if r[key]:
                print(f"\n{label}:")
                for item in r[key]:
                    print(f"  - {item}")
        print("\nNot carried over: .pages ordering (Wiki.js sorts the tree alphabetically),"
              " MkDocs hooks for author/date headers (Wiki.js shows its own history).")


def main():
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", required=True, type=Path, help="MkDocs docs/ folder")
    ap.add_argument("--out", required=True, type=Path, help="empty output folder")
    ap.add_argument("--overrides", type=Path, default=here / "content-overrides",
                    help="folder of hand-written pages that replace converted ones")
    ap.add_argument("--no-pdf-pages", action="store_true", help="don't generate PDF wrapper pages")
    a = ap.parse_args()
    if not (a.src / "index.md").exists():
        sys.exit(f"{a.src} doesn't look like the MkDocs docs/ folder (no index.md).")
    Converter(a.src.resolve(), a.out.resolve(), a.overrides, not a.no_pdf_pages).run()


if __name__ == "__main__":
    main()
