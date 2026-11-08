---
title: 'Contributing'
description: 'How to add and edit pages on the UBC Rocket wiki'
published: true
date: 2026-10-15T00:00:00.000Z
tags: 
editor: markdown
dateCreated: 2026-10-15T00:00:00.000Z
---

Anyone on the team can edit this wiki in the browser. No Git or local setup needed.

## Getting access

1. Click **Log in** (top right) and sign in with your GitHub or Google account.
2. Ask a lead to add you to the **Editors** group. Until then you can read but not edit.

## Where pages go

Pages follow the same layout as before:

```
season-project / team / project / page
e.g.  /2026-27-tvr/firmware/esc/working-with-dshot
```

Use lowercase and hyphens in paths. A folder's landing page is a page with the
same name as the folder (e.g. `/2026-27-tvr/firmware` is the landing page for
everything under `/2026-27-tvr/firmware/...`).

## Creating a page

1. Go to the folder you want, click **New Page** (the page icon with a +).
2. Type the full path, e.g. `2026-27-tvr/controls/guides/kalman-filter`.
3. Choose the **Markdown** editor.
4. Set a clear title and write. Click **Create** when done.

## Editing a page

Click the pencil icon on any page, edit, then **Save**. Every save is kept in
the page **History**, so nothing is ever lost, and it is also committed to the
`UBC-Rocket/docs` GitHub repo automatically.

## Images and PDFs

Upload files through the editor's insert/asset button into the same folder as
your page, then insert them. To show a PDF inline, use **Insert Block → PDF Viewer**.

## Callout boxes

```markdown
> [!WARNING] Heads up
> Firefox has trouble talking to the ESC over Web Serial.
```

Types: `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]`.
The text after the type is an optional title.

## Math

Inline math needs dollar signs **and** backticks: `` $`E = mc^2`$ ``.
For a full equation on its own line, use **Insert Block → KaTeX** in the editor.

## Edit approvals

Some sections may require a lead to approve edits before they go live. If your
change doesn't show up right away, it is waiting for review.

## Editing from Git instead

You can still edit Markdown in the GitHub repo. Wiki.js pulls changes on its
next sync (every few minutes). Keep the front matter block at the top of each
file intact.
