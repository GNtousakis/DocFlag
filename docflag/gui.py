"""NiceGUI front end for DocFlag: add Word files, edit the shared word list,
and download copies with every listed word highlighted.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import shutil
import socket
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from nicegui import events, run, ui

from . import wordlist
from .flagger import HIGHLIGHT_COLOURS, FlagResult, flag_document
from .legacy_convert import convert_doc, is_legacy_doc

ACCENT = "#3457D5"
DEFAULT_PORT = 8086
SESSION_PREFIX = "docflag_session_"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@dataclass
class DocEntry:
    display_name: str
    path: str


@dataclass
class Outcome:
    doc: DocEntry
    output_name: str = ""
    output_path: str = ""
    result: FlagResult | None = None
    error: str = ""


def lan_ip() -> str:
    """This machine's address on the local network (no packet is sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def sweep_stale_sessions() -> None:
    """Remove upload folders left behind by a previous run."""
    for path in Path(tempfile.gettempdir()).glob(f"{SESSION_PREFIX}*"):
        shutil.rmtree(path, ignore_errors=True)


def _unique_names(names: list[str]) -> list[str]:
    """Output names for a zip: 'a.docx' twice becomes 'a.docx', 'a (2).docx'."""
    seen: dict[str, int] = {}
    unique = []
    for name in names:
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            stem, ext = os.path.splitext(name)
            name = f"{stem} ({seen[key]}){ext}"
        unique.append(name)
    return unique


def _report_csv(outcomes: list[Outcome]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["File", "Word", "Matches"])
    for outcome in outcomes:
        if outcome.result is None:
            writer.writerow([outcome.doc.display_name, f"ERROR: {outcome.error}", ""])
        elif not outcome.result.counts:
            writer.writerow([outcome.doc.display_name, "(no matches)", 0])
        else:
            for word, count in sorted(
                outcome.result.counts.items(), key=lambda item: -item[1]
            ):
                writer.writerow([outcome.doc.display_name, word, count])
    # BOM so Excel opens non-ASCII words correctly.
    return buffer.getvalue().encode("utf-8-sig")


def create_app(port: int = DEFAULT_PORT) -> None:
    lan_url = f"http://{lan_ip()}:{port}"

    @ui.page("/")
    def index() -> None:
        ui.colors(primary=ACCENT)
        session_dir = tempfile.mkdtemp(prefix=SESSION_PREFIX)
        ui.context.client.on_delete(
            lambda: shutil.rmtree(session_dir, ignore_errors=True)
        )
        documents: list[DocEntry] = []
        upload_counter = {"n": 0}

        # -- documents ------------------------------------------------------

        def remove_at(i: int) -> None:
            documents.pop(i)
            doc_list.refresh()

        def clear_all() -> None:
            documents.clear()
            doc_list.refresh()

        async def handle_upload(e: events.UploadEventArguments) -> None:
            # Never trust a client-supplied name as a path.
            name = os.path.basename(e.file.name.replace("\\", "/"))
            if name.startswith("~$"):
                ui.notify(
                    f"Skipped '{name}': looks like a Word lock file "
                    "(the real file may be open in Word right now)",
                    type="warning",
                )
                return
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".docx", ".doc"):
                ui.notify(f"Skipped '{name}': not a .docx or .doc file", type="warning")
                return
            upload_counter["n"] += 1
            dest_path = os.path.join(session_dir, f"in_{upload_counter['n']}{ext}")
            data = await e.file.read()
            with open(dest_path, "wb") as f:
                f.write(data)

            if is_legacy_doc(dest_path):
                ui.notify(f"Converting '{name}' from .doc to .docx...", type="info")
                try:
                    # Word/LibreOffice take a few seconds: keep the UI responsive.
                    dest_path = await run.io_bound(convert_doc, dest_path)
                except Exception as exc:  # noqa: BLE001
                    ui.notify(
                        f"Couldn't add '{name}': {exc}", type="negative",
                        multi_line=True, timeout=15000, close_button=True,
                    )
                    upload.reset()
                    return
            documents.append(DocEntry(display_name=name, path=dest_path))
            doc_list.refresh()
            ui.notify(f"Added '{name}'", type="positive")
            upload.reset()

        # -- word list ------------------------------------------------------

        async def confirm(question: str) -> bool:
            with ui.dialog() as dialog, ui.card():
                ui.label(question)
                with ui.row().classes("w-full justify-end"):
                    ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
                    ui.button("Yes", on_click=lambda: dialog.submit(True)).props(
                        "unelevated"
                    ).mark("confirm-yes")
            return bool(await dialog)

        def add_words(text: str, source) -> None:
            new = wordlist.parse_lines(text)
            if not new:
                ui.notify("Type a word or phrase first.", type="warning")
                return
            before = len(wordlist.load())
            added = len(wordlist.add(new)) - before
            source.value = ""
            word_chips.refresh()
            ui.notify(
                f"Added {added} word(s)" if added else "Already in the list",
                type="positive" if added else "info",
            )

        def remove_word(word: str) -> None:
            wordlist.remove(word)
            word_chips.refresh()

        async def handle_import(e: events.UploadEventArguments) -> None:
            text = wordlist.decode_text(await e.file.read())
            before = len(wordlist.load())
            added = len(wordlist.add(wordlist.parse_lines(text))) - before
            word_chips.refresh()
            import_upload.reset()
            ui.notify(f"Imported {added} new word(s)", type="positive")

        def export_words() -> None:
            ui.download("\n".join(wordlist.load()).encode("utf-8"), "docflag_words.txt")

        async def reset_words() -> None:
            if await confirm("Replace the shared word list with the defaults?"):
                wordlist.reset()
                word_chips.refresh()

        async def remove_all_words() -> None:
            if await confirm("Remove every word from the shared list?"):
                wordlist.save([])
                word_chips.refresh()

        # -- layout ---------------------------------------------------------

        ui.add_head_html(f"<style>a:link, a:visited {{ color: {ACCENT}; }}</style>")

        with ui.header().classes("items-center justify-between px-6"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("flag", size="28px")
                ui.label("DocFlag").classes("text-xl font-bold")
            ui.label(f"Open from other PCs on this network: {lan_url}").classes(
                "text-sm opacity-80"
            )

        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-6"):

            with ui.card().classes("w-full"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label("1. Add documents").classes("text-lg font-semibold")
                    ui.button("Clear all", icon="clear_all", on_click=clear_all).props(
                        "flat dense color=negative"
                    )
                upload = ui.upload(
                    label="Drop .docx or .doc files here or click to browse",
                    multiple=True,
                    auto_upload=True,
                    max_file_size=MAX_UPLOAD_BYTES,
                    on_upload=handle_upload,
                    on_rejected=lambda: ui.notify(
                        "File rejected: the limit is 50 MB per document.",
                        type="negative",
                    ),
                ).props("accept=.docx,.doc").classes("w-full").mark("doc-upload")

                @ui.refreshable
                def doc_list() -> None:
                    if not documents:
                        ui.label("No documents added yet.").classes(
                            "text-gray-400 italic py-4"
                        )
                        return
                    for i, doc in enumerate(documents):
                        with ui.row().classes(
                            "items-center w-full gap-2 py-1 border-b border-gray-200"
                        ):
                            ui.icon("description", color="primary")
                            ui.label(doc.display_name).classes("flex-grow truncate")
                            ui.button(
                                icon="delete", on_click=lambda i=i: remove_at(i)
                            ).props("flat dense round color=negative").mark(f"remove-{i}")

                doc_list()

            with ui.card().classes("w-full"):
                ui.label("2. Words to flag").classes("text-lg font-semibold")
                ui.label(
                    "Whole words and phrases; upper/lower case and accents are "
                    "ignored. End a word with * to match any ending (contract* "
                    "finds contracts, contractual). This list is shared: changes "
                    "are saved and apply for everyone."
                ).classes("text-sm text-gray-500")

                @ui.refreshable
                def word_chips() -> None:
                    words = wordlist.load()
                    if not words:
                        ui.label("The list is empty.").classes(
                            "text-gray-400 italic py-2"
                        )
                        return
                    with ui.row().classes("gap-1"):
                        for word in words:
                            ui.chip(
                                word,
                                removable=True,
                                on_value_change=lambda _, w=word: remove_word(w),
                            ).props("outline")

                word_chips()

                with ui.row().classes("items-center w-full gap-2"):
                    new_word = ui.input("Add a word or phrase").classes(
                        "flex-grow"
                    ).mark("new-word")
                    new_word.on(
                        "keydown.enter", lambda: add_words(new_word.value, new_word)
                    )
                    ui.button(
                        "Add", icon="add",
                        on_click=lambda: add_words(new_word.value, new_word),
                    ).props("unelevated").mark("add-word")

                with ui.expansion("Paste a list, import, export, reset").classes(
                    "w-full"
                ):
                    bulk = ui.textarea("One word or phrase per line").classes(
                        "w-full"
                    ).mark("bulk-words")
                    ui.button(
                        "Add all", icon="playlist_add",
                        on_click=lambda: add_words(bulk.value, bulk),
                    ).props("flat").mark("bulk-add")
                    import_upload = ui.upload(
                        label="Import a .txt file (one word per line)",
                        auto_upload=True,
                        on_upload=handle_import,
                    ).props("accept=.txt flat bordered").classes("w-full").mark(
                        "import-words"
                    )
                    with ui.row().classes("gap-2"):
                        ui.button(
                            "Export list", icon="download", on_click=export_words
                        ).props("flat").mark("export-words")
                        ui.button(
                            "Reset to defaults", icon="restart_alt", on_click=reset_words
                        ).props("flat").mark("reset-words")
                        ui.button(
                            "Remove all", icon="delete_sweep", on_click=remove_all_words
                        ).props("flat color=negative").mark("remove-all-words")

            with ui.card().classes("w-full"):
                ui.label("3. Flag").classes("text-lg font-semibold")
                colour = ui.select(
                    HIGHLIGHT_COLOURS, value="yellow", label="Highlight colour"
                ).classes("w-48").mark("colour")
                ui.label(
                    "Your original files are not changed: you get highlighted "
                    ".docx copies (a .zip with a summary if there are several)."
                ).classes("text-sm text-gray-500")
                flag_button = ui.button("Flag and download", icon="flag").props(
                    "unelevated"
                ).mark("flag-button")

            results = ui.column().classes("w-full gap-4")

        # -- flag action ----------------------------------------------------

        def render_outcome(outcome: Outcome) -> None:
            if outcome.result is None:
                with ui.card().classes("w-full border-l-4 border-red-500"):
                    ui.label(outcome.doc.display_name).classes("font-semibold")
                    ui.label(f"Could not be processed: {outcome.error}").classes(
                        "text-sm text-red-600"
                    )
                return
            total = outcome.result.total
            border = "border-green-500" if total else "border-gray-300"
            with ui.card().classes(f"w-full border-l-4 {border}"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(outcome.doc.display_name).classes("font-semibold truncate")
                    ui.label(
                        f"{total} match(es)" if total else "No matches"
                    ).classes("text-sm")
                with ui.row().classes("gap-1"):
                    for word, count in sorted(
                        outcome.result.counts.items(), key=lambda item: -item[1]
                    ):
                        ui.badge(f"{word} × {count}").props("outline")

        async def run_flag() -> None:
            results.clear()
            if not documents:
                ui.notify("Add at least one Word file first.", type="warning")
                return
            words = wordlist.load()
            if not words:
                ui.notify("The word list is empty: add a word first.", type="warning")
                return

            flag_button.props("loading")
            flag_button.set_enabled(False)
            try:
                names = _unique_names([
                    f"{os.path.splitext(d.display_name)[0]}_flagged.docx"
                    for d in documents
                ])
                outcomes = []
                for i, (doc, name) in enumerate(zip(list(documents), names)):
                    outcome = Outcome(
                        doc=doc,
                        output_name=name,
                        output_path=os.path.join(session_dir, f"out_{i}.docx"),
                    )
                    try:
                        # Off the event loop so other users aren't blocked.
                        outcome.result = await run.io_bound(
                            flag_document, doc.path, outcome.output_path,
                            words, colour.value,
                        )
                    except Exception as exc:  # noqa: BLE001
                        outcome.error = str(exc) or type(exc).__name__
                    outcomes.append(outcome)

                done = [o for o in outcomes if o.result is not None]
                with results:
                    for outcome in outcomes:
                        render_outcome(outcome)
                if not done:
                    ui.notify("No document could be processed.", type="negative")
                    return

                if len(outcomes) == 1:
                    filename, media_type = done[0].output_name, DOCX_MEDIA_TYPE
                    data = Path(done[0].output_path).read_bytes()
                else:
                    filename, media_type = "docflag_results.zip", "application/zip"
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for outcome in done:
                            zf.write(outcome.output_path, outcome.output_name)
                        zf.writestr("report.csv", _report_csv(outcomes))
                    data = buffer.getvalue()

                ui.download(data, filename, media_type)
                with results:
                    ui.button(
                        "Download again", icon="download",
                        on_click=lambda: ui.download(data, filename, media_type),
                    ).props("flat")
                ui.notify(f"'{filename}' is ready — check your downloads.", type="positive")
            finally:
                flag_button.props(remove="loading")
                flag_button.set_enabled(True)

        flag_button.on_click(run_flag)


def main() -> None:
    parser = argparse.ArgumentParser(description="DocFlag web interface")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to listen on (default: all, for LAN access)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true",
                        help="don't open a browser window on start")
    args, _ = parser.parse_known_args()

    sweep_stale_sessions()
    create_app(args.port)
    print(f"DocFlag: other PCs on this network can open http://{lan_ip()}:{args.port}")
    print("Close this window to stop DocFlag.")
    ui.run(host=args.host, port=args.port, reload=False, title="DocFlag",
           show=not args.no_browser)


if __name__ in {"__main__", "__mp_main__"}:
    main()
