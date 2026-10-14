"""The "Σύγκριση" tab: DocDiff's comparison in the browser. Each side takes
screenshots (pasted with Ctrl+V or uploaded), read with the Windows OCR of
the PC that runs DocFlag, or a Word file, read exactly; then the two texts
are compared word by word (table rows cell by cell). Image mode compares
two screenshots as pictures instead.
"""
from __future__ import annotations

import asyncio
import html
import itertools
import os
from dataclasses import dataclass

from nicegui import events, run, ui
from PIL import Image

from .compare import service
from .compare.diffing import ADDED, CHANGED, EQUAL, REMOVED, DiffResult, Segment
from .compare.imagediff import ImageDiffResult, render_overlay
from .compare.regions import append_text, remove_region_text
from . import pdftext
from .doctext import extract_text
from .legacy_convert import convert_doc, is_legacy_doc

MODE_TEXT = "text"
MODE_IMAGE = "image"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")
WORD_EXTENSIONS = (".docx", ".doc")
PDF_EXTENSION = ".pdf"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
THUMB_SIZE = (160, 110)
PREVIEW_SIZE = (1600, 1600)
SENSITIVITY_LABELS = {0: "Χαμηλή", 1: "Κανονική", 2: "Υψηλή"}

# DocDiff's light palette (ui/theme.py).
STYLE = """
<style>
.diff-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.diff-added { background: #d6ebff; color: #1d2d3d; }
.diff-removed { background: #e7e7ea; color: #5d5d60; text-decoration: line-through; }
.diff-changed { background: #fff0a0; color: #6b5400; }
.diff-pane { max-height: 70vh; overflow-y: auto; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
.swatch-regions { border: 2px solid #dc2626; }
.paste-zone { border: 2px dashed #c4c7cf; border-radius: 6px; padding: .75rem;
              cursor: pointer; outline: none; text-align: center; }
.paste-zone:focus { border-color: #3457D5; background: #f3f5fd; }
.similarity { font-size: 2rem; font-weight: 700; line-height: 1; }
.preview { max-height: 360px; object-fit: contain; }
.side-col { flex: 1 1 380px; min-width: 0; }
.banner { background: #eef6ff; border: 1px solid #b5d9fd; color: #2c455d;
          padding: .75rem; border-radius: 6px; }
</style>
<script>
// Proportional scroll sync between the two result columns (as in DocDiff).
window.docflagSyncScroll = (src) => {
  if (window.docflagSyncing === src) return;
  const dst = document.getElementById(src.dataset.partner);
  if (!dst) return;
  const span = src.scrollHeight - src.clientHeight;
  const frac = span > 0 ? src.scrollTop / span : 0;
  window.docflagSyncing = dst;
  dst.scrollTop = frac * (dst.scrollHeight - dst.clientHeight);
  requestAnimationFrame(() => { window.docflagSyncing = null; });
};
</script>
"""


def paste_handler(upload: ui.upload) -> str:
    """JS for a paste event: hand a pasted screenshot to `upload`, which
    sends it like a picked file. clipboardData works over plain http, unlike
    navigator.clipboard, so this also works from other PCs on the network."""
    return f"""(e) => {{
        const files = [...(e.clipboardData ? e.clipboardData.files : [])]
            .filter(f => f.type.startsWith('image/'));
        if (!files.length) return;
        e.preventDefault();
        getElement({upload.id}).$refs.qRef.addFiles(files);
    }}"""


def segments_html(segments: list[Segment]) -> str:
    parts = []
    for seg in segments:
        text = html.escape(seg.text)
        parts.append(text if seg.kind == EQUAL else f'<span class="diff-{seg.kind}">{text}</span>')
    return "".join(parts)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def ocr_text(image: Image.Image) -> str:
    return service.ocr_image(image).text


def word_count(text: str) -> str:
    words = len(text.split())
    return f"{words} {'λέξη' if words == 1 else 'λέξεις'} · {len(text)} χαρακτήρες"


@dataclass
class Region:
    rid: int
    thumb: Image.Image
    text: str | None = None  # None while it is being read


class Side:
    """One document (A or B): its screenshots, its text and its image."""

    def __init__(self, key: str, title: str, kicker: str, session_dir: str, mode) -> None:
        self.key = key
        self.title = title
        self.session_dir = session_dir
        self.mode = mode
        self.regions: list[Region] = []
        self.image: Image.Image | None = None
        self._ids = itertools.count(1)
        self._files = itertools.count(1)
        # Screenshots are read one at a time, in the order they arrive.
        self._ocr_lock = asyncio.Lock()

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.column().classes("gap-0"):
                    ui.label(kicker).classes("text-sm text-gray-500")
                    ui.label(title).classes("text-lg font-semibold")
                ui.button("Καθαρισμός", icon="clear_all", on_click=self.clear).props(
                    "flat dense color=negative"
                ).mark(f"clear-{key}")

            self.upload = ui.upload(
                label="Σύρετε εδώ στιγμιότυπα, αρχεία Word ή PDF ή κάντε κλικ για αναζήτηση",
                multiple=True,
                auto_upload=True,
                max_file_size=MAX_UPLOAD_BYTES,
                on_upload=self.handle_upload,
                on_rejected=lambda: ui.notify(
                    "Το αρχείο απορρίφθηκε: το όριο είναι 50 MB.", type="negative"
                ),
            ).props(
                f"accept={','.join(IMAGE_EXTENSIONS + WORD_EXTENSIONS + (PDF_EXTENSION,))} flat bordered"
            ).classes("w-full").mark(f"upload-{key}")
            ui.label(
                "Ή πατήστε Win+Shift+S, επιλέξτε περιοχή της οθόνης, κάντε κλικ εδώ και πατήστε Ctrl+V"
            ).classes("paste-zone w-full text-sm text-gray-500").props("tabindex=0").on(
                "paste", js_handler=paste_handler(self.upload)
            ).mark(f"paste-{key}")

            with ui.column().classes("w-full gap-2").bind_visibility_from(
                mode, "value", value=MODE_TEXT
            ):
                self.thumbs = ui.row().classes("gap-2")
                self.textarea = ui.textarea(
                    placeholder="Το κείμενο που θα αναγνωριστεί θα εμφανιστεί εδώ. "
                    "Μπορείτε να το διορθώσετε πριν από τη σύγκριση.",
                    on_change=self._update_counts,
                ).props("outlined rows=12").classes("w-full").mark(f"text-{key}")
                self.counts = ui.label(word_count("")).classes("text-sm text-gray-500")

            with ui.column().classes("w-full items-center").bind_visibility_from(
                mode, "value", value=MODE_IMAGE
            ):
                self.preview = ui.image().classes("w-full preview")
                self.preview.set_visibility(False)
                self.no_image = ui.label(
                    "Η εικόνα που θα προσθέσετε θα εμφανιστεί εδώ."
                ).classes("text-gray-400 italic py-4")

            self.status = ui.label("").classes("text-sm text-gray-500")

    # -- state --------------------------------------------------------------

    @property
    def text(self) -> str:
        return self.textarea.value or ""

    def set_text(self, text: str) -> None:
        self.textarea.value = text
        self._update_counts()

    def _update_counts(self) -> None:
        self.counts.text = word_count(self.text)

    def set_status(self, message: str, level: str = "info") -> None:
        self.status.text = message
        self.status.classes(
            replace="text-sm " + {"info": "text-gray-500", "warn": "text-amber-700",
                                  "error": "text-red-600"}[level]
        )

    def clear(self) -> None:
        if self.mode.value == MODE_IMAGE:
            self.image = None
            self.preview.set_visibility(False)
            self.no_image.set_visibility(True)
        else:
            self.regions.clear()
            self._render_thumbs()
            self.set_text("")
        self.set_status("")

    # -- input --------------------------------------------------------------

    async def handle_upload(self, e: events.UploadEventArguments) -> None:
        # Never trust a client-supplied name as a path.
        name = os.path.basename((e.file.name or "").replace("\\", "/")) or "image.png"
        ext = os.path.splitext(name)[1].lower()
        data = await e.file.read()
        self.upload.reset()
        if ext in WORD_EXTENSIONS and not name.startswith("~$"):
            await self.add_word(name, ext, data)
        elif ext == PDF_EXTENSION:
            await self.add_pdf(name, data)
        elif ext in IMAGE_EXTENSIONS or e.file.content_type.startswith("image/"):
            await self.add_image(name, data)
        else:
            ui.notify(
                f"Το '{name}' παραλείφθηκε: δεν είναι εικόνα ή αρχείο .docx/.doc/.pdf",
                type="warning",
            )

    async def add_word(self, name: str, ext: str, data: bytes) -> None:
        if self.mode.value == MODE_IMAGE:
            ui.notify("Στη σύγκριση εικόνων προσθέστε εικόνες, όχι αρχεία Word.", type="warning")
            return
        path = self._save(data, ext)
        self.set_status(f"Ανάγνωση του '{name}'…")
        try:
            if is_legacy_doc(path):
                path = await run.io_bound(convert_doc, path)
            text = await run.io_bound(extract_text, path)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Το '{name}' δεν διαβάστηκε: {exc}", "error")
            return
        self._replace_text(name, text, f"Κείμενο από το '{name}'.")

    async def add_pdf(self, name: str, data: bytes) -> None:
        path = self._save(data, PDF_EXTENSION)
        self.set_status(f"Ανάγνωση του '{name}'…")
        try:
            if self.mode.value == MODE_IMAGE:
                image, pages = await run.io_bound(pdftext.render_first_page, path)
            else:
                result = await run.io_bound(pdftext.extract_text, path, ocr_text)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Το '{name}' δεν διαβάστηκε: {exc}", "error")
            return
        if self.mode.value == MODE_IMAGE:
            self._show_image(image)
            if pages > 1:
                self.set_status(
                    f"Η πρώτη σελίδα του '{name}' ({_count(pages, 'σελίδα', 'σελίδες')}).", "warn"
                )
            return
        details = _count(result.pages, "σελίδα", "σελίδες")
        if result.ocr_pages:
            details += f", {result.ocr_pages} με OCR"
        self._replace_text(name, result.text, f"Κείμενο από το '{name}' ({details}).")

    def _save(self, data: bytes, ext: str) -> str:
        path = os.path.join(self.session_dir, f"compare_{self.key}_{next(self._files)}{ext}")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _replace_text(self, name: str, text: str, done: str) -> None:
        """A Word or PDF file is the whole document: it replaces what was there."""
        self.regions.clear()
        self._render_thumbs()
        self.set_text(text)
        if text.strip():
            self.set_status(done)
        else:
            self.set_status(f"Το '{name}' δεν περιέχει κείμενο.", "warn")

    def _show_image(self, image: Image.Image) -> None:
        """One picture per side: a new one replaces the old."""
        self.image = image
        preview = image.copy()
        preview.thumbnail(PREVIEW_SIZE)
        self.preview.set_source(preview)
        self.preview.set_visibility(True)
        self.no_image.set_visibility(False)
        self.set_status(f"{image.width}×{image.height} px")

    async def add_image(self, name: str, data: bytes) -> None:
        try:
            image = await run.io_bound(service.load_image, data)
        except ValueError as exc:
            ui.notify(f"Το '{name}' παραλείφθηκε: {exc}", type="warning")
            return
        if self.mode.value == MODE_IMAGE:
            self._show_image(image)
            return

        thumb = image.copy()
        thumb.thumbnail(THUMB_SIZE)
        region = Region(next(self._ids), thumb)
        self.regions.append(region)
        self._render_thumbs()
        self.set_status("Ανάγνωση κειμένου…")
        async with self._ocr_lock:
            if region not in self.regions:  # removed while waiting
                return
            try:
                outcome = await run.io_bound(service.ocr_image, image)
            except Exception as exc:  # noqa: BLE001
                if region in self.regions:
                    self.regions.remove(region)
                    self._render_thumbs()
                self.set_status(f"Η αναγνώριση κειμένου απέτυχε: {exc}", "error")
                return
        if region not in self.regions:  # removed while being read
            return
        region.text = outcome.text
        self._render_thumbs()
        if not outcome.text.strip():
            self.set_status(
                "Δεν βρέθηκε κείμενο στην εικόνα. Δοκιμάστε μεγαλύτερη ή πιο καθαρή περιοχή.",
                "warn",
            )
            return
        self.set_text(append_text(self.text, outcome.text))
        self.set_status("Έτοιμο.")

    def remove_region(self, region: Region) -> None:
        if region not in self.regions:
            return
        self.regions.remove(region)
        self._render_thumbs()
        if region.text:
            text, removed = remove_region_text(self.text, region.text)
            if removed:
                self.set_text(text)
                self.set_status("")
            else:
                self.set_status(
                    "Το κείμενο αυτής της εικόνας έχει τροποποιηθεί και δεν μπορεί να "
                    "αφαιρεθεί αυτόματα· διαγράψτε το από το πλαίσιο κειμένου.",
                    "warn",
                )

    def _render_thumbs(self) -> None:
        self.thumbs.clear()
        with self.thumbs:
            for region in self.regions:
                with ui.column().classes("items-center gap-1"):
                    ui.image(region.thumb).style(
                        f"width: {THUMB_SIZE[0]}px; height: {THUMB_SIZE[1]}px"
                    ).props("fit=contain")
                    with ui.row().classes("items-center gap-1"):
                        if region.text is None:
                            ui.spinner(size="sm")
                            ui.label("Ανάγνωση…").classes("text-sm text-gray-500")
                        ui.button(
                            icon="delete", on_click=lambda r=region: self.remove_region(r)
                        ).props("flat dense round color=negative").mark(
                            f"remove-region-{self.key}-{region.rid}"
                        )


def build_compare_tab(session_dir: str) -> None:
    ui.add_head_html(STYLE)

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-6"):
        is_real, greek_missing = service.engine_status()
        if not is_real:
            ui.label(
                "Δοκιμαστική λειτουργία: η αναγνώριση κειμένου (OCR) των Windows δεν είναι "
                "διαθέσιμη σε αυτόν τον υπολογιστή, οπότε οι εικόνες δίνουν δείγμα κειμένου."
            ).classes("w-full text-sm banner")
        elif greek_missing:
            ui.label(
                "Η γλώσσα OCR για τα Ελληνικά δεν είναι εγκατεστημένη στον υπολογιστή που "
                "εκτελεί το DocFlag, οπότε τα ελληνικά μπορεί να μην αναγνωρίζονται σωστά. "
                "Στον υπολογιστή αυτόν: Ρυθμίσεις > Ώρα και γλώσσα > Γλώσσα και περιοχή > "
                "Προσθήκη γλώσσας > Ελληνικά, με επιλεγμένη την «Οπτική αναγνώριση "
                "χαρακτήρων», και επανεκκίνηση του DocFlag."
            ).classes("w-full text-sm banner")

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center w-full gap-4"):
                mode = ui.toggle(
                    {MODE_TEXT: "Κείμενο", MODE_IMAGE: "Εικόνα"}, value=MODE_TEXT
                ).mark("compare-mode")
                ignore_case = ui.checkbox("Αγνόηση πεζών/κεφαλαίων", value=True).bind_visibility_from(
                    mode, "value", value=MODE_TEXT
                ).mark("ignore-case")
                ignore_punct = ui.checkbox("Αγνόηση σημείων στίξης", value=True).bind_visibility_from(
                    mode, "value", value=MODE_TEXT
                ).mark("ignore-punct")
                sensitivity = ui.select(
                    SENSITIVITY_LABELS, value=1, label="Ευαισθησία"
                ).classes("w-48").bind_visibility_from(mode, "value", value=MODE_IMAGE)
            ui.label(
                "Κείμενο: προσθέστε στιγμιότυπα (διαβάζονται με OCR και μπορείτε να διορθώσετε "
                "το κείμενο) ή ένα αρχείο Word ή PDF σε κάθε πλευρά· οι πίνακες συγκρίνονται "
                "γραμμή προς γραμμή. Εικόνα: ένα στιγμιότυπο ή PDF (η πρώτη σελίδα) σε κάθε "
                "πλευρά, συγκρίνονται ως εικόνες."
            ).classes("text-sm text-gray-500")

        editor = ui.column().classes("w-full gap-6")
        results = ui.column().classes("w-full gap-4")
        results.set_visibility(False)

        with editor:
            with ui.row().classes("w-full gap-6 items-start"):
                with ui.column().classes("side-col"):
                    side_a = Side("A", "Έγγραφο Α", "Πρωτότυπο", session_dir, mode)
                with ui.column().classes("side-col"):
                    side_b = Side("B", "Έγγραφο Β", "Αναθεώρηση", session_dir, mode)
            compare_button = ui.button("Σύγκριση", icon="compare_arrows").props(
                "unelevated size=lg"
            ).mark("compare-button")

        def back() -> None:
            results.set_visibility(False)
            editor.set_visibility(True)

        def show_text_result(result: DiffResult) -> None:
            st = result.stats
            results.clear()
            with results:
                ui.button("← Επεξεργασία", on_click=back).props("flat").mark("compare-back")
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center gap-6"):
                        with ui.column().classes("gap-0"):
                            ui.label("ΟΜΟΙΟΤΗΤΑ").classes("text-sm text-gray-500")
                            ui.label(service.format_similarity(st.similarity, st.total == 0)).classes(
                                "similarity"
                            ).mark("similarity")
                        for kind, label, count in (
                            (REMOVED, "Διαγραφές", st.removed),
                            (ADDED, "Προσθήκες", st.added),
                            (CHANGED, "Αλλαγές", st.changed),
                        ):
                            with ui.row().classes("items-center gap-2"):
                                ui.html(f'<span class="swatch diff-{kind}"></span>', sanitize=False)
                                ui.label(f"{label}: {count}").mark(f"count-{kind}")
                    if not st.total:
                        ui.label("Τα κείμενα είναι ίδια.").classes("text-sm")
                with ui.row().classes("w-full gap-6 items-start"):
                    panes = []
                    for title, segments in (("Έγγραφο Α", result.left), ("Έγγραφο Β", result.right)):
                        with ui.card().classes("side-col"):
                            ui.label(title).classes("text-lg font-semibold")
                            pane = ui.element("div").classes("w-full diff-pane").on(
                                "scroll", js_handler="(e) => docflagSyncScroll(e.target)"
                            )
                            with pane:
                                ui.html(segments_html(segments), sanitize=False).classes(
                                    "diff-text"
                                ).mark("diff-" + title[-1])
                            panes.append(pane)
                    panes[0].props(f"data-partner=c{panes[1].id}")
                    panes[1].props(f"data-partner=c{panes[0].id}")

        def show_image_result(result: ImageDiffResult) -> None:
            identical = not result.boxes and result.similarity >= 99.95
            results.clear()
            with results:
                ui.button("← Επεξεργασία", on_click=back).props("flat").mark("compare-back")
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center gap-6"):
                        with ui.column().classes("gap-0"):
                            ui.label("ΟΜΟΙΟΤΗΤΑ").classes("text-sm text-gray-500")
                            ui.label(service.format_similarity(result.similarity, identical)).classes(
                                "similarity"
                            ).mark("similarity")
                        with ui.row().classes("items-center gap-2"):
                            ui.html('<span class="swatch swatch-regions"></span>', sanitize=False)
                            ui.label(f"Περιοχές διαφορών: {result.regions}").mark("count-regions")
                        heat = ui.checkbox("Θερμικός χάρτης", value=True)
                    note = {
                        "resized": "Οι εικόνες δεν ευθυγραμμίστηκαν· η Β προσαρμόστηκε στο μέγεθος "
                        "της Α, οπότε μικρές μετατοπίσεις μπορεί να εμφανιστούν ως διαφορές.",
                        "aspect": "Οι εικόνες έχουν διαφορετικές αναλογίες και δεν ευθυγραμμίστηκαν· "
                        "το ποσοστό είναι ενδεικτικό.",
                    }.get(result.warning or "", "Οι εικόνες είναι ίδιες." if identical else "")
                    if note:
                        ui.label(note).classes("text-sm")
                with ui.row().classes("w-full gap-6 items-start"):
                    images = []
                    for title in ("Έγγραφο Α", "Έγγραφο Β"):
                        with ui.card().classes("side-col"):
                            ui.label(title).classes("text-lg font-semibold")
                            images.append(ui.image().classes("w-full"))

                def render() -> None:
                    overlay = result.heat if heat.value else None
                    for image, source in zip(images, (result.a, result.b)):
                        image.set_source(render_overlay(source, result.boxes, overlay))

                heat.on_value_change(render)
                render()

        async def compare() -> None:
            compare_button.props("loading")
            compare_button.set_enabled(False)
            try:
                if mode.value == MODE_TEXT:
                    if not side_a.text.strip() or not side_b.text.strip():
                        ui.notify("Και τα δύο έγγραφα πρέπει να έχουν κείμενο.", type="warning")
                        return
                    result = await run.io_bound(
                        service.compare_text, side_a.text, side_b.text,
                        ignore_case.value, ignore_punct.value,
                    )
                    show_text_result(result)
                else:
                    if side_a.image is None or side_b.image is None:
                        ui.notify("Και τα δύο έγγραφα πρέπει να έχουν εικόνα.", type="warning")
                        return
                    try:
                        result = await run.io_bound(
                            service.compare_image, side_a.image, side_b.image, sensitivity.value
                        )
                    except Exception as exc:  # noqa: BLE001
                        ui.notify(f"Η σύγκριση εικόνων απέτυχε: {exc}", type="negative")
                        return
                    show_image_result(result)
                editor.set_visibility(False)
                results.set_visibility(True)
            finally:
                compare_button.props(remove="loading")
                compare_button.set_enabled(True)

        compare_button.on_click(compare)
