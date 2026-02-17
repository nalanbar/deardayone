#!/usr/bin/env python3
"""DearDayOne - Export reMarkable handwritten journal pages to Day One."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Default paths
REMARKABLE_DATA_DIR = Path.home() / "Library/Containers/com.remarkable.desktop/Data/Library/Application Support/remarkable/desktop"
DAYONE_DB = Path.home() / "Library/Group Containers/5U8NS4GX82.dayoneapp2/Data/Documents/DayOne.sqlite"
CONFIG_DIR = Path.home() / ".config/deardayone"
CONFIG_FILE = CONFIG_DIR / "config.json"
RMC_BIN = Path.home() / ".local/bin/rmc"
INKSCAPE_BIN = "/opt/homebrew/bin/inkscape"
DAYONE_BIN = "/usr/local/bin/dayone"


def load_config():
    """Load config or return None if not found."""
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_config(config):
    """Save config, creating directory if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def discover_notebooks(data_dir):
    """Scan reMarkable data folder and return list of handwritten notebooks.

    Returns list of dicts: {guid, name, parent, created, modified, page_count}
    """
    notebooks = []
    data_path = Path(data_dir)

    # Find all .metadata files
    for meta_file in data_path.glob("*.metadata"):
        guid = meta_file.stem
        try:
            with open(meta_file) as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # Must be a document, not a folder
        if meta.get("type") != "DocumentType":
            continue

        # Skip trashed items
        if meta.get("parent") == "trash":
            continue

        # Check .content file to distinguish notebooks from PDFs
        content_file = data_path / f"{guid}.content"
        if not content_file.exists():
            continue

        try:
            with open(content_file) as f:
                content = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # Only keep notebooks — skip PDFs and EPUBs
        file_type = content.get("fileType", "")
        if file_type != "notebook":
            continue

        # Count pages
        pages = []
        c_pages = content.get("cPages", {})
        if isinstance(c_pages, dict):
            pages = c_pages.get("pages", [])
        page_count = len(pages)

        # Count how many actually have .rm files
        notebook_dir = data_path / guid
        rm_count = 0
        if notebook_dir.is_dir():
            for page in pages:
                page_id = page.get("id", "")
                rm_file = notebook_dir / f"{page_id}.rm"
                if rm_file.exists():
                    rm_count += 1

        notebooks.append({
            "guid": guid,
            "name": meta.get("visibleName", "(unnamed)"),
            "parent": meta.get("parent", ""),
            "created": meta.get("createdTime", "0"),
            "modified": meta.get("lastModified", "0"),
            "page_count": page_count,
            "rm_count": rm_count,
        })

    # Sort alphabetically by name
    notebooks.sort(key=lambda n: n["name"].lower())
    return notebooks


def get_folder_name(data_dir, parent_guid):
    """Resolve a folder GUID to its visible name."""
    if not parent_guid or parent_guid in ("", "trash"):
        return ""
    meta_file = Path(data_dir) / f"{parent_guid}.metadata"
    if not meta_file.exists():
        return ""
    try:
        with open(meta_file) as f:
            meta = json.load(f)
        return meta.get("visibleName", "")
    except (json.JSONDecodeError, OSError):
        return ""


def get_page_list(data_dir, guid):
    """Get ordered list of page info for a notebook.

    Returns list of dicts: {id, modified_ms} in display order.
    The modified_ms comes from the 'modifed' (sic) field in the content file,
    which is epoch milliseconds.
    """
    content_file = Path(data_dir) / f"{guid}.content"
    with open(content_file) as f:
        content = json.load(f)

    c_pages = content.get("cPages", {})
    if isinstance(c_pages, dict):
        pages = c_pages.get("pages", [])
    else:
        pages = []

    result = []
    for p in pages:
        if "id" not in p:
            continue
        # The field is misspelled "modifed" in reMarkable's format
        modified_ms = p.get("modifed", p.get("modified", "0"))
        result.append({
            "id": p["id"],
            "modified_ms": int(modified_ms) if modified_ms else 0,
        })
    return result


def convert_rm_to_png(rm_path, output_path):
    """Convert a .rm file to PNG using rmc (for SVG) and Inkscape (for PNG).

    Pipeline: .rm → SVG (via rmc) → PNG (via Inkscape)
    """
    svg_path = str(output_path).rsplit(".", 1)[0] + ".svg"

    # Step 1: .rm → SVG
    result = subprocess.run(
        [str(RMC_BIN), "-t", "svg", str(rm_path), "-o", svg_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        lines = stderr.splitlines()
        error_line = lines[-1] if lines else "unknown error"
        raise RuntimeError(f"rmc conversion failed: {error_line}")

    # Step 2: SVG → PNG
    result = subprocess.run(
        [INKSCAPE_BIN, "--export-type=png",
         f"--export-filename={output_path}", svg_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"inkscape conversion failed: {result.stderr.strip()}")

    # Clean up intermediate SVG
    try:
        os.remove(svg_path)
    except OSError:
        pass

    return output_path


def create_dayone_entry(journal, date_str, tags, attachment_path, body_text):
    """Create a Day One entry via the dayone CLI.

    Note: dayone's --attachments flag consumes all following arguments until
    it hits '--' or another option, so we place it last and add '--' before
    the 'new' command.
    """
    # Build command with careful argument ordering.
    # dayone's --tags and --attachments are greedy (consume all following args
    # until another option or '--'), so we put simple options first, then tags,
    # then attachments last, and terminate with '--' before 'new'.
    cmd = [DAYONE_BIN]
    if journal:
        cmd.extend(["--journal", journal])
    if date_str:
        cmd.extend(["--date", date_str])
    if tags:
        cmd.extend(["--tags"] + list(tags))
    if attachment_path:
        cmd.extend(["--attachments", str(attachment_path)])
    cmd.append("--")
    cmd.extend(["new", body_text])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"dayone failed: {result.stderr.strip()}")
    return result.stdout.strip()


def list_dayone_journals():
    """Read journal names from the Day One SQLite database.

    Returns a sorted list of unique journal name strings.
    """
    if not DAYONE_DB.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{DAYONE_DB}?mode=ro", uri=True)
        cursor = conn.execute("SELECT ZNAME FROM ZJOURNAL ORDER BY ZNAME")
        journals = sorted(set(row[0] for row in cursor if row[0]))
        conn.close()
        return journals
    except sqlite3.Error:
        return []


def run_setup(data_dir):
    """Interactive setup: pick a notebook and Day One journal."""
    print("Scanning reMarkable notebooks...\n")
    notebooks = discover_notebooks(data_dir)

    if not notebooks:
        print("No handwritten notebooks found in reMarkable data.")
        print(f"  Looked in: {data_dir}")
        sys.exit(1)

    print(f"Found {len(notebooks)} handwritten notebook(s):\n")
    for i, nb in enumerate(notebooks, 1):
        folder = get_folder_name(data_dir, nb["parent"])
        location = f"  [{folder}]" if folder else ""
        print(f"  {i:3d}. {nb['name']}{location}  ({nb['rm_count']}/{nb['page_count']} pages with content)")

    print()
    while True:
        try:
            choice = input("Select notebook number: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(notebooks):
                break
            print(f"  Please enter a number between 1 and {len(notebooks)}")
        except ValueError:
            print("  Please enter a valid number")
        except (EOFError, KeyboardInterrupt):
            print("\nSetup cancelled.")
            sys.exit(0)

    selected = notebooks[idx]
    print(f"\nSelected: {selected['name']}")

    # Pick Day One journal
    journals = list_dayone_journals()

    if journals:
        print(f"\nDay One journals:\n")
        for i, j in enumerate(journals, 1):
            print(f"  {i:3d}. {j}")

        print()
        while True:
            try:
                choice = input("Select journal number: ").strip()
                jdx = int(choice) - 1
                if 0 <= jdx < len(journals):
                    break
                print(f"  Please enter a number between 1 and {len(journals)}")
            except ValueError:
                print("  Please enter a valid number")
            except (EOFError, KeyboardInterrupt):
                print("\nSetup cancelled.")
                sys.exit(0)

        journal = journals[jdx]
    else:
        print("\nCould not read Day One journals from database.")
        default_journal = "Journal"
        try:
            journal = input(f"Day One journal name [{default_journal}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSetup cancelled.")
            sys.exit(0)
        if not journal:
            journal = default_journal

    # Load existing config to preserve exported_pages if same notebook
    existing = load_config()
    exported_pages = []
    if existing and existing.get("remarkable_notebook_guid") == selected["guid"]:
        exported_pages = existing.get("exported_pages", [])
        print(f"  (Preserving {len(exported_pages)} previously exported page records)")

    config = {
        "remarkable_notebook_guid": selected["guid"],
        "remarkable_notebook_name": selected["name"],
        "dayone_journal": journal,
        "exported_pages": exported_pages,
    }
    save_config(config)

    print(f"\nSetup complete!")
    print(f"  Notebook: {selected['name']}")
    print(f"  Journal:  {journal}")
    print(f"  Config:   {CONFIG_FILE}")
    print(f"\nRun `deardayone` to export pages, or `deardayone --dry-run` to preview.")


def run_export(data_dir, dry_run=False):
    """Export notebook pages to Day One."""
    config = load_config()
    if not config:
        print("No configuration found. Run `deardayone --setup` first.")
        sys.exit(1)

    guid = config["remarkable_notebook_guid"]
    notebook_name = config["remarkable_notebook_name"]
    journal = config["dayone_journal"]
    exported = set(config.get("exported_pages", []))

    # Verify notebook still exists
    meta_file = Path(data_dir) / f"{guid}.metadata"
    if not meta_file.exists():
        print(f"Error: Notebook '{notebook_name}' ({guid}) not found in reMarkable data.")
        print("  It may have been deleted or the data hasn't synced.")
        print("  Run `deardayone --setup` to select a different notebook.")
        sys.exit(1)

    # Get page list (returns list of {id, modified_ms} dicts)
    page_infos = get_page_list(data_dir, guid)
    notebook_dir = Path(data_dir) / guid

    if not page_infos:
        print(f"Notebook '{notebook_name}' has no pages.")
        return

    # Determine what needs exporting
    to_export = []
    for i, page_info in enumerate(page_infos):
        page_id = page_info["id"]
        if page_id in exported:
            continue
        rm_file = notebook_dir / f"{page_id}.rm"
        if not rm_file.exists():
            continue
        # Use the page's modification timestamp from content metadata
        if page_info["modified_ms"] > 0:
            page_date = datetime.fromtimestamp(page_info["modified_ms"] / 1000)
        else:
            # Fallback to file mtime
            page_date = datetime.fromtimestamp(rm_file.stat().st_mtime)
        to_export.append((i, page_id, rm_file, page_date))

    already_exported = len(exported)

    if not to_export:
        print(f"Nothing to export. All {len(page_infos)} pages of '{notebook_name}' are already exported.")
        return

    action = "Would export" if dry_run else "Exporting"
    print(f"{action} {len(to_export)} page(s) from '{notebook_name}' to Day One journal '{journal}'")
    if already_exported:
        print(f"  ({already_exported} page(s) already exported, skipping)")
    print()

    if dry_run:
        for i, page_id, rm_file, page_date in to_export:
            date_str = page_date.strftime("%Y-%m-%d %H:%M:%S")
            print(f"  Page {i + 1}: {page_id[:8]}... ({date_str})")
        print(f"\nRun without --dry-run to export.")
        return

    # Export each page
    exported_count = 0
    error_count = 0

    with tempfile.TemporaryDirectory(prefix="deardayone_") as tmp_dir:
        for i, page_id, rm_file, page_date in to_export:
            page_num = i + 1
            date_str = page_date.strftime("%Y-%m-%d %H:%M:%S")
            png_path = Path(tmp_dir) / f"{page_id}.png"

            print(f"  Page {page_num}/{len(page_infos)}: ", end="", flush=True)

            try:
                # Convert .rm to PNG
                convert_rm_to_png(rm_file, png_path)

                # Create Day One entry
                body = f"Page {page_num} of {notebook_name}"
                tags = ["reMarkable", notebook_name]
                create_dayone_entry(journal, date_str, tags, png_path, body)

                # Track as exported (save immediately for crash safety)
                exported.add(page_id)
                config["exported_pages"] = list(exported)
                save_config(config)

                exported_count += 1
                print(f"OK ({date_str})")

            except RuntimeError as e:
                error_count += 1
                print(f"FAILED - {e}")
            except Exception as e:
                error_count += 1
                print(f"FAILED - {type(e).__name__}: {e}")

    print()
    print(f"Done! {exported_count} page(s) exported, {error_count} error(s).")
    if error_count:
        print("  Re-run to retry failed pages.")


def main():
    parser = argparse.ArgumentParser(
        prog="deardayone",
        description="Export reMarkable handwritten journal pages to Day One.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Interactive setup to pick a reMarkable notebook and Day One journal",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be exported without actually doing it",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REMARKABLE_DATA_DIR,
        help=f"reMarkable desktop data directory (default: {REMARKABLE_DATA_DIR})",
    )

    args = parser.parse_args()

    # Verify data dir exists
    if not args.data_dir.is_dir():
        print(f"Error: reMarkable data directory not found: {args.data_dir}")
        print("  Make sure the reMarkable desktop app is installed and has synced.")
        sys.exit(1)

    if args.setup:
        run_setup(args.data_dir)
    else:
        run_export(args.data_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
