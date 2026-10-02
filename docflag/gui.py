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

from nicegui import __version__ as NICEGUI_VERSION
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
# Word's highlighter colours as shown in the colour picker.
COLOUR_LABELS = {
    "yellow": "Κίτρινο", "green": "Πράσινο", "cyan": "Κυανό",
    "magenta": "Ματζέντα", "red": "Κόκκινο", "blue": "Μπλε",
    "lightGray": "Ανοιχτό γκρι", "darkYellow": "Σκούρο κίτρινο",
    "darkGreen": "Σκούρο πράσινο", "darkCyan": "Σκούρο κυανό",
    "darkMagenta": "Σκούρο ματζέντα", "darkRed": "Σκούρο κόκκινο",
    "darkBlue": "Σκούρο μπλε", "darkGray": "Σκούρο γκρι",
}
assert list(COLOUR_LABELS) == HIGHLIGHT_COLOURS

# NiceGUI loads all of its styling through CSS cascade layers (@layer), which
# browsers older than Chrome/Edge 99 and Firefox 97 silently drop: the page
# works but shows up completely unstyled. For those browsers, load the same
# stylesheets as plain <link>s, plus the handful of Tailwind utilities this
# page uses (Tailwind's own output is layered too).
LEGACY_STYLESHEETS = (
    "fonts.css", "quasar.unimportant.prod.css", "quasar.important.prod.css",
    "nicegui.css",
)
LEGACY_UTILITIES = """
.w-full { width: 100%; } .w-48 { width: 12rem; }
.max-w-3xl { max-width: 48rem; }
.mx-auto { margin-left: auto; margin-right: auto; }
.p-6 { padding: 1.5rem; }
.px-6 { padding-left: 1.5rem; padding-right: 1.5rem; }
.py-1 { padding-top: .25rem; padding-bottom: .25rem; }
.py-2 { padding-top: .5rem; padding-bottom: .5rem; }
.py-4 { padding-top: 1rem; padding-bottom: 1rem; }
.gap-1 { gap: .25rem; } .gap-2 { gap: .5rem; }
.gap-4 { gap: 1rem; } .gap-6 { gap: 1.5rem; }
.flex-grow { flex-grow: 1; }
.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.text-sm { font-size: .875rem; line-height: 1.25rem; }
.text-lg { font-size: 1.125rem; line-height: 1.75rem; }
.text-xl { font-size: 1.25rem; line-height: 1.75rem; }
.font-semibold { font-weight: 600; } .font-bold { font-weight: 700; }
.italic { font-style: italic; }
.opacity-80 { opacity: .8; }
.text-gray-400 { color: #9ca3af; } .text-gray-500 { color: #6b7280; }
.text-red-600 { color: #dc2626; }
.border-b { border-bottom: 1px solid; } .border-l-4 { border-left: 4px solid; }
.border-gray-200 { border-color: #e5e7eb; }
.border-gray-300 { border-color: #d1d5db; }
.border-red-500 { border-color: #ef4444; }
.border-green-500 { border-color: #22c55e; }
"""


def legacy_css_fallback() -> str:
    """Head HTML that restores the styling in browsers without @layer."""
    links = "".join(
        f'<link rel="stylesheet" href="/_nicegui/{NICEGUI_VERSION}/static/{name}">'
        for name in LEGACY_STYLESHEETS
    )
    html = links + f"<style>{LEGACY_UTILITIES}</style>"
    return (
        "<script>if (!window.CSSLayerBlockRule) "
        f"document.head.insertAdjacentHTML('beforeend', {html!r});</script>"
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


def _count(n: int, one: str, many: str) -> str:
    """'1 λέξη' / '3 λέξεις'."""
    return f"{n} {one if n == 1 else many}"


def _report_csv(outcomes: list[Outcome]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Αρχείο", "Λέξη", "Εμφανίσεις"])
    for outcome in outcomes:
        if outcome.result is None:
            writer.writerow([outcome.doc.display_name, f"ΣΦΑΛΜΑ: {outcome.error}", ""])
        elif not outcome.result.counts:
            writer.writerow([outcome.doc.display_name, "(καμία εμφάνιση)", 0])
        else:
            for word, count in sorted(
                outcome.result.counts.items(), key=lambda item: -item[1]
            ):
                writer.writerow([outcome.doc.display_name, word, count])
    # BOM so Excel opens non-ASCII words correctly.
    return buffer.getvalue().encode("utf-8-sig")


def create_app(port: int = DEFAULT_PORT) -> None:
    lan_url = f"http://{lan_ip()}:{port}"
    ui.add_head_html(legacy_css_fallback(), shared=True)

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
                    f"Το '{name}' παραλείφθηκε: μοιάζει με αρχείο κλειδώματος του Word "
                    "(το πραγματικό αρχείο ίσως είναι ανοιχτό στο Word αυτή τη στιγμή)",
                    type="warning",
                )
                return
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".docx", ".doc"):
                ui.notify(
                    f"Το '{name}' παραλείφθηκε: δεν είναι αρχείο .docx ή .doc",
                    type="warning",
                )
                return
            upload_counter["n"] += 1
            dest_path = os.path.join(session_dir, f"in_{upload_counter['n']}{ext}")
            data = await e.file.read()
            with open(dest_path, "wb") as f:
                f.write(data)

            if is_legacy_doc(dest_path):
                ui.notify(f"Μετατροπή του '{name}' από .doc σε .docx...", type="info")
                try:
                    # Word/LibreOffice take a few seconds: keep the UI responsive.
                    dest_path = await run.io_bound(convert_doc, dest_path)
                except Exception as exc:  # noqa: BLE001
                    ui.notify(
                        f"Το '{name}' δεν προστέθηκε: {exc}", type="negative",
                        multi_line=True, timeout=15000, close_button=True,
                    )
                    upload.reset()
                    return
            documents.append(DocEntry(display_name=name, path=dest_path))
            doc_list.refresh()
            ui.notify(f"Προστέθηκε το '{name}'", type="positive")
            upload.reset()

        # -- word list ------------------------------------------------------

        async def confirm(question: str) -> bool:
            with ui.dialog() as dialog, ui.card():
                ui.label(question)
                with ui.row().classes("w-full justify-end"):
                    ui.button("Άκυρο", on_click=lambda: dialog.submit(False)).props("flat")
                    ui.button("Ναι", on_click=lambda: dialog.submit(True)).props(
                        "unelevated"
                    ).mark("confirm-yes")
            return bool(await dialog)

        def add_words(text: str, source) -> None:
            new = wordlist.parse_lines(text)
            if not new:
                ui.notify("Γράψτε πρώτα μια λέξη ή φράση.", type="warning")
                return
            before = len(wordlist.load())
            added = len(wordlist.add(new)) - before
            source.value = ""
            word_chips.refresh()
            ui.notify(
                (
                    "Προστέθηκε 1 λέξη" if added == 1
                    else f"Προστέθηκαν {added} λέξεις" if added
                    else "Υπάρχει ήδη στη λίστα"
                ),
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
            ui.notify((
                    "Εισήχθη 1 νέα λέξη" if added == 1
                    else f"Εισήχθησαν {added} νέες λέξεις"
                ), type="positive")

        def export_words() -> None:
            ui.download("\n".join(wordlist.load()).encode("utf-8"), "docflag_words.txt")

        async def reset_words() -> None:
            if await confirm("Να αντικατασταθεί η κοινόχρηστη λίστα λέξεων με την προεπιλεγμένη;"):
                wordlist.reset()
                word_chips.refresh()

        async def remove_all_words() -> None:
            if await confirm("Να αφαιρεθούν όλες οι λέξεις από την κοινόχρηστη λίστα;"):
                wordlist.save([])
                word_chips.refresh()

        # -- layout ---------------------------------------------------------

        ui.add_head_html(f"<style>a:link, a:visited {{ color: {ACCENT}; }}</style>")

        with ui.header().classes("items-center justify-between px-6"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("flag", size="28px")
                ui.label("DocFlag").classes("text-xl font-bold")
            ui.label(f"Άνοιγμα από άλλους υπολογιστές του δικτύου: {lan_url}").classes(
                "text-sm opacity-80"
            )

        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-6"):

            with ui.card().classes("w-full"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label("1. Προσθήκη εγγράφων").classes("text-lg font-semibold")
                    ui.button("Καθαρισμός", icon="clear_all", on_click=clear_all).props(
                        "flat dense color=negative"
                    )
                upload = ui.upload(
                    label="Σύρετε εδώ αρχεία .docx ή .doc ή κάντε κλικ για αναζήτηση",
                    multiple=True,
                    auto_upload=True,
                    max_file_size=MAX_UPLOAD_BYTES,
                    on_upload=handle_upload,
                    on_rejected=lambda: ui.notify(
                        "Το αρχείο απορρίφθηκε: το όριο είναι 50 MB ανά έγγραφο.",
                        type="negative",
                    ),
                ).props("accept=.docx,.doc").classes("w-full").mark("doc-upload")

                @ui.refreshable
                def doc_list() -> None:
                    if not documents:
                        ui.label("Δεν έχουν προστεθεί έγγραφα ακόμη.").classes(
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
                ui.label("2. Λέξεις προς επισήμανση").classes("text-lg font-semibold")
                ui.label(
                    "Ολόκληρες λέξεις και φράσεις· κεφαλαία/πεζά και τόνοι "
                    "αγνοούνται. Βάλτε * στο τέλος μιας λέξης για οποιαδήποτε "
                    "κατάληξη (το εμπιστευτικ* βρίσκει εμπιστευτικά, "
                    "εμπιστευτικού). Η λίστα είναι κοινόχρηστη: οι αλλαγές "
                    "αποθηκεύονται και ισχύουν για όλους."
                ).classes("text-sm text-gray-500")

                @ui.refreshable
                def word_chips() -> None:
                    words = wordlist.load()
                    if not words:
                        ui.label("Η λίστα είναι κενή.").classes(
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
                    new_word = ui.input("Προσθήκη λέξης ή φράσης").classes(
                        "flex-grow"
                    ).mark("new-word")
                    new_word.on(
                        "keydown.enter", lambda: add_words(new_word.value, new_word)
                    )
                    ui.button(
                        "Προσθήκη", icon="add",
                        on_click=lambda: add_words(new_word.value, new_word),
                    ).props("unelevated").mark("add-word")

                with ui.expansion("Επικόλληση λίστας, εισαγωγή, εξαγωγή, επαναφορά").classes(
                    "w-full"
                ):
                    bulk = ui.textarea("Μία λέξη ή φράση ανά γραμμή").classes(
                        "w-full"
                    ).mark("bulk-words")
                    ui.button(
                        "Προσθήκη όλων", icon="playlist_add",
                        on_click=lambda: add_words(bulk.value, bulk),
                    ).props("flat").mark("bulk-add")
                    import_upload = ui.upload(
                        label="Εισαγωγή αρχείου .txt (μία λέξη ανά γραμμή)",
                        auto_upload=True,
                        on_upload=handle_import,
                    ).props("accept=.txt flat bordered").classes("w-full").mark(
                        "import-words"
                    )
                    with ui.row().classes("gap-2"):
                        ui.button(
                            "Εξαγωγή λίστας", icon="download", on_click=export_words
                        ).props("flat").mark("export-words")
                        ui.button(
                            "Επαναφορά προεπιλογών", icon="restart_alt", on_click=reset_words
                        ).props("flat").mark("reset-words")
                        ui.button(
                            "Αφαίρεση όλων", icon="delete_sweep", on_click=remove_all_words
                        ).props("flat color=negative").mark("remove-all-words")

            with ui.card().classes("w-full"):
                ui.label("3. Επισήμανση").classes("text-lg font-semibold")
                colour = ui.select(
                    COLOUR_LABELS, value="yellow", label="Χρώμα επισήμανσης"
                ).classes("w-48").mark("colour")
                ui.label(
                    "Τα αρχικά σας αρχεία δεν αλλάζουν: λαμβάνετε επισημασμένα "
                    "αντίγραφα .docx (ένα .zip με σύνοψη αν είναι περισσότερα)."
                ).classes("text-sm text-gray-500")
                flag_button = ui.button("Επισήμανση και λήψη", icon="flag").props(
                    "unelevated"
                ).mark("flag-button")

            results = ui.column().classes("w-full gap-4")

        # -- flag action ----------------------------------------------------

        def render_outcome(outcome: Outcome) -> None:
            if outcome.result is None:
                with ui.card().classes("w-full border-l-4 border-red-500"):
                    ui.label(outcome.doc.display_name).classes("font-semibold")
                    ui.label(f"Δεν ήταν δυνατή η επεξεργασία: {outcome.error}").classes(
                        "text-sm text-red-600"
                    )
                return
            total = outcome.result.total
            border = "border-green-500" if total else "border-gray-300"
            with ui.card().classes(f"w-full border-l-4 {border}"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(outcome.doc.display_name).classes("font-semibold truncate")
                    ui.label(
                        _count(total, "εμφάνιση", "εμφανίσεις") if total
                        else "Καμία εμφάνιση"
                    ).classes("text-sm")
                with ui.row().classes("gap-1"):
                    for word, count in sorted(
                        outcome.result.counts.items(), key=lambda item: -item[1]
                    ):
                        ui.badge(f"{word} × {count}").props("outline")

        async def run_flag() -> None:
            results.clear()
            if not documents:
                ui.notify("Προσθέστε πρώτα τουλάχιστον ένα αρχείο Word.", type="warning")
                return
            words = wordlist.load()
            if not words:
                ui.notify("Η λίστα λέξεων είναι κενή: προσθέστε πρώτα μια λέξη.", type="warning")
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
                    ui.notify("Δεν ήταν δυνατή η επεξεργασία κανενός εγγράφου.", type="negative")
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
                        "Λήψη ξανά", icon="download",
                        on_click=lambda: ui.download(data, filename, media_type),
                    ).props("flat")
                ui.notify(f"Το '{filename}' είναι έτοιμο — δείτε τις λήψεις σας.", type="positive")
            finally:
                flag_button.props(remove="loading")
                flag_button.set_enabled(True)

        flag_button.on_click(run_flag)


def main() -> None:
    parser = argparse.ArgumentParser(description="Διεπαφή ιστού του DocFlag")
    parser.add_argument("--host", default="0.0.0.0",
                        help="διεύθυνση ακρόασης (προεπιλογή: όλες, για πρόσβαση από το τοπικό δίκτυο)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true",
                        help="να μην ανοίγει παράθυρο περιηγητή κατά την εκκίνηση")
    args, _ = parser.parse_known_args()

    sweep_stale_sessions()
    create_app(args.port)
    print(f"DocFlag: οι άλλοι υπολογιστές του δικτύου μπορούν να ανοίξουν το "
          f"http://{lan_ip()}:{args.port}")
    print("Κλείστε αυτό το παράθυρο για να σταματήσει το DocFlag.")
    ui.run(host=args.host, port=args.port, reload=False, title="DocFlag", language="el",
           show=not args.no_browser)


if __name__ in {"__main__", "__mp_main__"}:
    main()
