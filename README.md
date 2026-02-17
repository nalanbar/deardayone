# DearDayOne

Export handwritten journal pages from a [reMarkable](https://remarkable.com/) tablet into [Day One](https://dayoneapp.com/).

Each page of your reMarkable notebook becomes its own Day One entry, dated to when you last edited that page, with the handwriting attached as a PNG image.

> This project was entirely vibe-coded with [Claude Code](https://claude.ai/claude-code).

## How it works

DearDayOne reads the reMarkable desktop app's local sync data, converts each notebook page from reMarkable's `.rm` drawing format into a PNG image, and creates a Day One journal entry for it via the `dayone` CLI.

The conversion pipeline for each page:

```
.rm file → SVG (via rmc) → PNG (via Inkscape) → Day One entry (via dayone CLI)
```

Each entry gets:
- **Date**: the page's last-modified timestamp from reMarkable metadata
- **Tags**: `reMarkable` and the notebook name
- **Body**: "Page N of Notebook Name"
- **Attachment**: a PNG rendering of the handwritten page

DearDayOne tracks which pages have already been exported, so you can run it repeatedly and it will only export new or unprocessed pages.

## Prerequisites

- **macOS** (paths are hardcoded for macOS app locations)
- **Python 3** (standard library only, no pip installs needed)
- **[reMarkable desktop app](https://remarkable.com/account/desktop)** installed and synced
- **[rmc](https://github.com/ricklupton/rmc)** (reMarkable converter) — install with `uv tool install rmc`
- **[Inkscape](https://inkscape.org/)** — install with `brew install inkscape`
- **[Day One CLI](https://dayoneapp.com/guides/tips-and-tutorials/command-line-interface-cli/)** — install from Day One's settings

## Usage

### First run: set up the notebook-to-journal mapping

```
python3 deardayone.py --setup
```

This will:
1. Scan your reMarkable data and list all handwritten notebooks (filtering out PDFs, EPUBs, and trashed items)
2. Let you pick which notebook to export from
3. List your Day One journals (read directly from the Day One database) and let you pick which one to export into
4. Save the configuration to `~/.config/deardayone/config.json`

### Preview what would be exported

```
python3 deardayone.py --dry-run
```

Shows each page that would be exported along with its date, without actually creating any entries.

### Export

```
python3 deardayone.py
```

Exports all un-exported pages. Each page is saved to the config as it's exported, so if the process is interrupted, you can just run it again and it picks up where it left off.

### Re-running setup

```
python3 deardayone.py --setup
```

Run setup again anytime to switch to a different notebook or journal. If you select the same notebook, your export history is preserved.

## Resetting exported pages

The export tracker lives in `~/.config/deardayone/config.json` in the `exported_pages` array. To re-export everything:

```bash
# Option 1: delete the config and start fresh
rm ~/.config/deardayone/config.json
python3 deardayone.py --setup

# Option 2: edit the config and clear just the export history
# Open ~/.config/deardayone/config.json and set "exported_pages" to []
```

Note that resetting the tracker will cause duplicate entries in Day One — DearDayOne doesn't delete previously created entries. You'd want to delete the old entries in Day One first.

## Known issues

- **Highlighter strokes**: Pages using reMarkable's highlighter tool (pen color ID 9) may fail to convert due to an [upstream bug in rmc](https://github.com/ricklupton/rmc). These pages are skipped with an error message and can be retried after rmc is updated.
- **Day One CLI sandbox bug**: The `dayone` CLI can't copy attachment files into its own PendingMedia folder due to macOS sandbox restrictions. DearDayOne works around this by reading the Day One SQLite database to find the attachment UUID and copying the file there itself.
- **Single notebook**: Only one notebook can be configured at a time. Run `--setup` to switch.

## File layout

```
~/.config/deardayone/config.json    # your notebook/journal config + export history
~/Library/Containers/com.remarkable.desktop/...  # reMarkable desktop sync data (read-only)
~/Library/Group Containers/5U8NS4GX82.dayoneapp2/...  # Day One database + PendingMedia
```
