#!/usr/bin/env python3
"""
Convert the UBC Rocket MkDocs site (UBC-Rocket/docs, folder `docs/`) into a
folder tree that Wiki.js 3.x can import through its Git storage module.

    python scripts/convert_mkdocs.py --src ../docs/docs --out ../wiki-content

What it does
------------
* Paths are slugified to what Wiki.js 3 accepts (lowercase a-z, 0-9, hyphens) so every page and asset has a
  clean URL. `a/b/index.md` becomes `a/b.md` (Wiki.js has no index pages; a page
  can share a name with a folder). The site root `index.md` becomes `home.md`.
* Adds the YAML front matter Wiki.js expects (title, published, editor...).
  Title comes from: existing front matter -> first `# H1` -> `.pages` title
  (for folder landing pages) -> the file name. The leading H1 is removed because
  Wiki.js already prints the title above the page.
* `!!! type "Title"` / `??? type` admonitions -> Wiki.js 3 callouts
  (`> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]`, `> [!CAUTION]`).
* Inline math `$x$` -> `$`x`$` (Wiki.js 3 needs both the dollars and backticks).
* Relative links to pages, images and files are rewritten to absolute Wiki.js
  paths, so they work no matter which page renders them.
* `![embed](...)` / `![iframe](...)` (the MkDocs iframe hook) -> a plain image
  for images, a `::block-pdf` PDF viewer for PDFs, otherwise an <iframe> plus
  a fallback link.
* `$$ ... $$` display math -> `::block-katex` content blocks.
* Every PDF without a page of the same name gets a small wrapper page that
  shows it in a PDF viewer block, matching what the MkDocs `pdf_pages` hook did. Use --no-pdf-pages
  to skip this.
* `\\set{...}` (MathJax braket) -> `\\{...\\}` so the math renders in KaTeX.
* Files in content-overrides/ (next to this script's parent folder) replace the
  converted page at the same output path, e.g. a Wiki.js-specific contributing
  guide.

Output goes under <out>/en/ (the language folder Wiki.js 3 Git storage uses).

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
SKIP_FILE_RE = re.compile(r"^(empty \(for git to track\)\.txt|~\$.*|\.~lock\..*#)$", re.IGNORECASE)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}

CALLOUT = {  # MkDocs admonition type -> Wiki.js 3 callout ([!NOTE] etc.)
    "note": "NOTE", "info": "NOTE", "abstract": "NOTE", "summary": "NOTE",
    "tldr": "NOTE", "todo": "NOTE", "seealso": "NOTE", "quote": "NOTE",
    "tip": "TIP", "hint": "TIP", "success": "TIP", "check": "TIP",
    "done": "TIP", "example": "TIP",
    "important": "IMPORTANT",
    "warning": "WARNING", "attention": "WARNING", "question": "WARNING",
    "help": "WARNING", "faq": "WARNING",
    "caution": "CAUTION", "danger": "CAUTION", "error": "CAUTION",
    "failure": "CAUTION", "fail": "CAUTION", "missing": "CAUTION", "bug": "CAUTION",
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
    """Folder / page name for Wiki.js 3: only a-z, 0-9 and hyphens."""
    s = part.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "untitled"


def slug_file(name: str) -> str:
    """Asset file name: slugged stem + lowercase extension."""
    p = PurePosixPath(name)
    ext = re.sub(r"[^a-z0-9]", "", p.suffix.lower())
    return slug(p.stem if p.suffix else name) + (f".{ext}" if ext else "")


def slug_path(rel: PurePosixPath, is_file: bool = False) -> PurePosixPath:
    parts = [slug(p) for p in rel.parts]
    if is_file:
        parts[-1] = slug_file(rel.parts[-1])
    return PurePosixPath(*parts)


def page_out_path(rel_md: PurePosixPath) -> PurePosixPath:
    """docs-relative .md path -> output .md path (index handling + slugs)."""
    if rel_md.name.lower() == "index.md":
        parent = rel_md.parent
        if str(parent) in ("", "."):
            return PurePosixPath("home.md")
        folders = slug_path(parent)
        return folders.parent / (folders.name + ".md")
    folders = slug_path(rel_md.parent) if str(rel_md.parent) not in ("", ".") else PurePosixPath()
    return folders / (slug(rel_md.stem) + ".md")


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


INLINE_MATH_RE = re.compile(r"(?<![\\$`])\$(?![\s$])((?:\\.|[^\\$\n])+?)(?<!\s)\$(?![\d$`])")
CODE_SPAN_RE = re.compile(r"(`+)(?:.+?)\1")


def katex_block(lines: list[str], pad: str) -> list[str]:
    """Wiki.js 3 KaTeX content block (the syntax its editor generates)."""
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    body = [ln[len(pad):] if ln.startswith(pad) else ln.lstrip() for ln in lines]
    return ["", f"{pad}::block-katex", f"{pad}```latex"] + [f"{pad}{b}".rstrip() for b in body] + \
        [f"{pad}```", f"{pad}::", ""]


def convert_math(body: str) -> tuple[str, int, int]:
    """Math for Wiki.js 3:
      * $x$            -> $`x`$             (inline; needs dollars AND backticks)
      * $$ ... $$      -> ::block-katex      (display; one-line or multi-line)
    Code fences and inline code are left alone.
    Returns (body, inline_count, display_count)."""
    out: list[str] = []
    in_fence, n_inline, n_display = False, 0, 0
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue
        if stripped.startswith("$$"):
            pad = line[:len(line) - len(line.lstrip())]
            rest = stripped[2:]
            if rest.rstrip().endswith("$$") and rest.strip() != "":
                # $$ ... $$ on one line
                out.extend(katex_block([pad + rest.rstrip()[:-2].strip()], pad))
                n_display += 1
                i += 1
                continue
            # multi-line: collect until a line ending in $$
            math = [pad + rest] if rest.strip() else []
            i += 1
            closed = False
            while i < len(lines):
                ln = lines[i]
                if ln.rstrip().endswith("$$"):
                    before = ln.rstrip()[:-2]
                    if before.strip():
                        math.append(before)
                    closed = True
                    i += 1
                    break
                math.append(ln)
                i += 1
            if closed:
                out.extend(katex_block(math, pad))
                n_display += 1
            else:  # unterminated - leave as it was
                out.append(line)
                out.extend(math)
            continue
        # inline math, protecting code spans
        spans: list[str] = []

        def keep(m):
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"
        tmp = CODE_SPAN_RE.sub(keep, line)
        tmp, k = INLINE_MATH_RE.subn(lambda m: "$`" + m.group(1) + "`$", tmp)
        n_inline += k
        out.append(re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], tmp))
        i += 1
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, n_inline, n_display


def pdf_block(url: str) -> str:
    """Wiki.js 3 PDF Viewer content block, plus a plain link as a fallback."""
    return f'::block-pdf{{src="{url}"}}\n::\n\n[Open / download the PDF]({url})'


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
    def __init__(self, src: Path, out: Path, overrides: Path | None, pdf_pages: bool,
                 locale: str = "en"):
        self.locale = locale
        self.src, self.out, self.overrides, self.pdf_pages = src, out, overrides, pdf_pages
        self.page_map: dict[PurePosixPath, PurePosixPath] = {}   # src rel -> out rel (.md)
        self.asset_map: dict[PurePosixPath, PurePosixPath] = {}  # src rel -> out rel
        self.report: dict[str, list[str]] = {k: [] for k in (
            "broken_links", "tabs", "html", "collisions", "overrides", "pdf_pages", "big_assets",
            "display_math", "empty")}
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
                out = slug_path(rel, is_file=True)
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
                if ext == ".pdf":
                    return "\n" + pdf_block(final) + "\n"
                self.report["html"].append(f"{page_src}: embed of {final} kept as <iframe>")
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
                ctype = CALLOUT.get(kind, "NOTE")
                title = m.group("title")
                if title is None and kind not in ("note", "tip", "important", "warning", "caution"):
                    title = kind.capitalize()  # keep e.g. "Example" visible
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
                out.append(f"{pad}> [!{ctype}]" + (f" {title}" if title else ""))
                inner = self.convert_body(page_src, "\n".join(block)).splitlines() if block else []
                out.extend(f"{pad}> {b}".rstrip() for b in inner)
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

    def section_index(self, out_rel: PurePosixPath) -> str:
        """Body for an empty folder landing page: links to the pages under it."""
        folder = out_rel.with_suffix("")
        kids = sorted((self.titles.get(p, pretty(p.stem)), p) for p in self.page_map.values()
                      if p.parent == folder)
        if not kids:
            return "*This page doesn't have any content yet.*\n"
        lines = ["Pages in this section:", ""]
        lines += [f"- [{t}]({wiki_url(p, True)})" for t, p in kids]
        return "\n".join(lines) + "\n"

    def front_matter(self, title: str, description: str = "") -> str:
        return (
            "---\n"
            f"title: {yaml_str(title)}\n"
            f"description: {yaml_str(description)}\n"
            "published: true\n"
            f"date: '{self.now}'\n"
            "tags: []\n"
            "editor: markdown\n"
            f"dateCreated: '{self.now}'\n"
            "---\n\n"
        )

    # ---- run
    def run(self):
        if self.out.exists() and any(self.out.iterdir()):
            sys.exit(f"Output folder {self.out} is not empty. Pick a new folder or empty it first.")
        # Wiki.js 3 Git storage keeps each language in its own folder (en/home.md)
        self.out = self.out / self.locale
        self.out.mkdir(parents=True, exist_ok=True)
        self.plan()
        overridden = set()
        if self.overrides and self.overrides.is_dir():
            overridden = {PurePosixPath(f.relative_to(self.overrides).as_posix())
                          for f in self.overrides.rglob("*.md")}

        self.titles = {}
        for rel, out_rel in self.page_map.items():
            src_path = self.src / Path(*rel.parts)
            meta, body = split_front_matter(
                src_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n"))
            self.titles[out_rel] = self.title_for(src_path, rel, meta, body)[0]

        for rel, out_rel in self.page_map.items():
            if out_rel in overridden:
                continue  # replaced by a hand-written page below
            src_path = self.src / Path(*rel.parts)
            text = src_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            meta, body = split_front_matter(text)
            title, body = self.title_for(src_path, rel, meta, body)
            body = replace_set_macro(self.convert_body(rel, body))
            body, n_inline, n_display = convert_math(body)
            if n_display:
                self.report["display_math"].append(f"{out_rel} ({n_display} blocks)")
            body = body.rstrip() + "\n"
            if not body.strip():
                body = self.section_index(out_rel)
                self.report["empty"].append(str(out_rel))
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
                body = pdf_block(url) + "\n"
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
            ("tabs", "Content tabs (=== \"...\") - convert by hand to a :::block-tabs block"),
            ("empty", "Empty pages - filled with a list of their section's pages (Wiki.js 3 rejects empty pages)"),
            ("display_math", "Display math converted to KaTeX blocks - spot-check these pages"),
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
    ap.add_argument("--locale", default="en", help="Wiki.js language folder (default: en)")
    ap.add_argument("--no-pdf-pages", action="store_true", help="don't generate PDF wrapper pages")
    a = ap.parse_args()
    if not (a.src / "index.md").exists():
        sys.exit(f"{a.src} doesn't look like the MkDocs docs/ folder (no index.md).")
    Converter(a.src.resolve(), a.out.resolve(), a.overrides, not a.no_pdf_pages, a.locale).run()


if __name__ == "__main__":
    main()
