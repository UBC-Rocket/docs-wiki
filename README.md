# UBC Rocket Wiki (Wiki.js on Oracle Cloud)

Everything needed to run UBC Rocket's docs on Wiki.js at **docs.ubcrocket.com**:
the server stack, setup and backup scripts, and a converter that turns the
current MkDocs site (`UBC-Rocket/docs`) into Wiki.js pages.

```
docs-wiki/
├── docker-compose.yml       Wiki.js 2 + PostgreSQL + Caddy (auto HTTPS)
├── Caddyfile                reverse proxy for $WIKI_DOMAIN
├── .env.example             copy to .env on the server
├── scripts/
│   ├── setup-server.sh      one-time Oracle/Ubuntu setup (Docker, firewall, swap, backups)
│   ├── backup.sh            nightly pg_dump, keeps 14 days
│   └── convert_mkdocs.py    MkDocs -> Wiki.js converter (Python 3.9+, no dependencies)
└── content-overrides/
    └── contributing.md      Wiki.js version of the contributing guide (replaces the MkDocs one)
```

## How it fits together

- **This repo** holds infrastructure only. It's what gets cloned onto the server.
- **Page content** lives in `UBC-Rocket/docs` on a branch called **`wiki`**.
  Wiki.js syncs to it both ways: edits in the browser become commits, and
  commits pushed to the branch show up in the wiki. `main` keeps the MkDocs
  site running untouched until cutover.
- Wiki.js reads the **root** of the branch, so the `wiki` branch contains only
  the converted pages and files (no README, no MkDocs config).

## 1. Convert the content (on your computer)

From `Desktop/Github` (Git Bash on Windows):

```bash
git clone https://github.com/UBC-Rocket/docs.git      # skip if you already have it
python docs-wiki/scripts/convert_mkdocs.py --src docs/docs --out wiki-content
```

It prints a report of anything it couldn't convert. Then publish the result as
the `wiki` branch:

```bash
cd wiki-content
git init -b wiki
git add .
git commit -m "Convert MkDocs content to Wiki.js"
git remote add origin https://github.com/UBC-Rocket/docs.git
git push -u origin wiki
```

What the converter does:

| MkDocs | Wiki.js |
|---|---|
| `a/b/index.md` | `a/b.md` (a page next to its folder is that folder's landing page) |
| `docs/index.md` | `home.md` (the wiki home page) |
| Mixed-case paths with spaces | lowercase-with-hyphens, links updated to match |
| No front matter | Wiki.js front matter; title from the first `# H1` or `.pages` |
| `!!! warning "Title"` | `> **Title** ... {.is-warning}` callout |
| Relative links and images | absolute wiki paths |
| `![embed](file.pdf)` | `<iframe>` plus a fallback link (images stay images) |
| Every PDF (old `pdf_pages` hook) | a wrapper page that embeds it (`--no-pdf-pages` to skip) |
| `\set{...}` in math | `\{...\}` (KaTeX has no `\set`) |

Not carried over: `.pages` ordering (Wiki.js sorts alphabetically) and the
author/date header hook (Wiki.js has its own page history).

## 2. Create the server (Oracle Cloud)

1. **Instance:** VM.Standard.A1.Flex, Ubuntu 24.04 (aarch64), 1–2 OCPU, 6–12 GB.
   If A1 says "Out of capacity", VM.Standard.E2.1.Micro works too. The setup
   script adds swap, and Postgres is already tuned for 1 GB.
   Only use shapes tagged **Always Free-eligible**.
2. **Reserved IP:** Instance → Attached VNICs → VNIC → IP addresses → ⋮ Edit →
   Reserved public IP → create `wiki-ip`.
3. **Security list:** Networking → your VCN → subnet → Default Security List →
   add ingress rules for TCP 80 and TCP 443 from `0.0.0.0/0`.
4. **DNS:** at whoever hosts DNS for `ubcrocket.com`, add
   `A  docs  → <reserved IP>`. If it's on Cloudflare, use DNS only (grey cloud).

## 3. Install

SSH in (`ssh -i <key> ubuntu@<reserved IP>`), then:

```bash
git clone https://github.com/UBC-Rocket/docs-wiki.git   # or wherever this repo lives
cd docs-wiki
sudo bash scripts/setup-server.sh
exit            # log back in so docker works without sudo
```

Log back in, then:

```bash
cd docs-wiki
nano .env                         # confirm WIKI_DOMAIN
getent hosts docs.ubcrocket.com   # must print the reserved IP before continuing
docker compose up -d
docker compose logs -f wiki       # wait for "HTTP Server: [ RUNNING ]"
```

Open https://docs.ubcrocket.com and create the admin account. Set
**Site URL** to `https://docs.ubcrocket.com`.

## 4. Configure Wiki.js

**Connect Git storage** (Admin → Storage → Git):

1. Make a deploy key on the server. Wiki.js 2 is most reliable with PEM keys:
   ```bash
   ssh-keygen -t rsa -b 4096 -m PEM -N "" -f ~/wiki_deploy
   cat ~/wiki_deploy.pub
   ```
2. On GitHub: `UBC-Rocket/docs` → Settings → Deploy keys → Add. Paste the
   public key and tick **Allow write access**.
3. In Wiki.js, fill in the following and **Apply**:
   - Authentication Type: `ssh`
   - Repository URI: `git@github.com:UBC-Rocket/docs.git`
   - Branch: `wiki`
   - SSH Private Key Mode: `contents`, then paste `cat ~/wiki_deploy`
   - Default Author Name / Email: `UBC Rocket Wiki` / a team address
   - Sync Direction: `Bi-directional`, Schedule: every 5 minutes
4. Scroll to **Actions → Import Everything** and run it. Then check the page tree.

**Rendering** (Admin → Rendering):

- Markdown → Math: make sure **KaTeX** is on (for the controls guides).
- HTML → Security: turn on **Allow iframes** (PDF embeds), then use
  **Rebuild All Pages** under Admin → Utilities → Content.

**Uploads** (Admin → General → Uploads): raise Max Upload Size to about 25 MB.
Some of the reference PDFs are larger than the 5 MB default.

**Access** (Admin → Authentication, Groups):

- Add GitHub or Google login and disable open self-registration, or limit it
  to approved email domains.
- **Guests** group: read pages and assets (keeps the docs public).
- Create an **Editors** group with write pages, upload assets and comment
  permissions, then add team members to it.

## 5. Cutover

When the team is happy with the wiki:

1. On `UBC-Rocket/docs`, set the default branch to `wiki`.
2. Replace the GitHub Pages site with a redirect to https://docs.ubcrocket.com,
   or disable Pages.
3. Delete or archive the MkDocs workflow on `main`.
4. Ask whoever runs ubcrocket.com to add a **Docs** link to the main site.

## Day-to-day

| Task | Command (in `docs-wiki/` on the server) |
|---|---|
| Status / logs | `docker compose ps`, `docker compose logs -f wiki` |
| Update Wiki.js | `docker compose pull && docker compose up -d` |
| Manual backup | `./scripts/backup.sh` |
| Restore DB | `gunzip -c backups/wiki-<stamp>.sql.gz \| docker compose exec -T db psql -U wikijs -d wiki` |
| OS updates | `sudo apt update && sudo apt upgrade -y` (reboot if asked) |

**What lives where:** page content is in Git, so you can rebuild from GitHub.
Users, groups, permissions, comments and settings live only in Postgres, so
copy `backups/` off the server occasionally.

**Oracle caveats:**
- Oracle may stop Always Free instances that stay nearly idle for 7 days. If
  you get a warning email, start the instance again from the console.
- Keep a budget alert on the account, and only create resources tagged
  Always Free-eligible.
- Before you graduate, give at least one other lead access to the Oracle
  account, the DNS and this repo.
