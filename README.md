# DocFlag

Highlight a list of words in Word documents, and replace others. DocFlag runs
on one PC as a small web page; anyone on the same network can open it, drop in
Word files (`.docx` or `.doc`) and download copies with every listed word marked
with Word's highlighter and every listed replacement made (e.g. `τηλ.` →
`τηλέφωνο`).

- Originals are never changed: you get `<name>_flagged.docx` (also for `.doc`
  input), or a `.zip` with all copies plus a `report.csv` when you flag
  several files.
- Whole words and phrases; upper/lower case and accents are ignored (`draft`
  matches `DRAFT` but not `draftsman`; `εμπιστευτικό` matches `ΕΜΠΙΣΤΕΥΤΙΚΟ`).
- `*` stands for any run of letters, to catch endings: `εμπιστευτικ*` matches
  `εμπιστευτικά`, `εμπιστευτικού`, ...
- Spaces at the start or end of a word count: ` .` (space, full stop) only
  matches a full stop with a space before it. Type it as is in the single
  field; in a pasted or imported list, put it in double quotes: `" ."`.
- Replacements are made before highlighting, with the same matching (except
  that `*` is an ordinary character), and follow the case of the text they
  replace: `Τηλ.` → `Τηλέφωνο`, `ΤΗΛ.` → `ΤΗΛΕΦΩΝΟ`. Replaced text takes the
  formatting of the text it replaces and can be highlighted in its own colour.
  An empty replacement deletes the text.
- Body text, tables, headers, footers, footnotes, endnotes, text boxes and
  hyperlinks are covered. Text deleted with Track Changes is skipped.
- The word list and the replacement list are shared and saved on the server:
  add, remove, paste a list, import/export a `.txt`, or reset to the defaults.
  A replacement list has one `text => replacement` per line (`→` or a tab
  also work as the separator); adding a text that is already listed updates
  its replacement.

## Run on Windows (no Python needed)

1. Download `DocFlag-windows.zip` from the latest run of the
   **Build Windows package** workflow (GitHub → Actions), or from a Release.
2. Unzip it anywhere and double-click `DocFlag.exe`. A console window opens
   and your browser shows the page.
3. The first time, Windows Firewall asks whether to allow DocFlag: tick
   **Private networks** and allow it, otherwise other PCs can't connect.
4. Other PCs open the address shown in the page header and the console,
   e.g. `http://192.168.1.20:8086`.

Close the console window to stop DocFlag. The lists are kept in
`data\wordlist.json` and `data\replacements.json` next to `DocFlag.exe`;
keep that folder when you replace the program with a newer version.

Options: `DocFlag.exe --port 9000` to use another port, `--no-browser` to
not open a browser on start, `--host 127.0.0.1` to refuse network access.

## Run from source

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/python docflag.py
```

## Default lists

`docflag/default_words.txt` (one word or phrase per line) and
`docflag/default_replacements.txt` (one `text => replacement` per line) are
the lists used on first start and restored by **Reset to defaults**. Edit
them before building to ship your own defaults.

## Develop

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
```

Build the package (on the OS you are building for; Windows builds are done by
`.github/workflows/build-windows.yml`, and pushing a `v*` tag attaches the zip
to a GitHub Release):

```bash
pyinstaller packaging/docflag.spec --noconfirm
```

## Limitations

- Legacy `.doc` files are converted to `.docx` first, which needs Microsoft
  Word or LibreOffice installed on the PC that runs DocFlag (not on the PCs
  that use it). The highlighted copy is always a `.docx`.
- No login: anyone who can reach the address can use it and edit the
  lists. Run it on trusted networks only.
- Imported `.txt` lists must be UTF-8, UTF-16 or Greek "ANSI" (Windows-1253).
- Highlights and replacements inside automatically generated fields (e.g. a
  table of contents) disappear when Word refreshes the field.
- A space in a word or replacement stands for one or more spaces, so `"  "`
  (two spaces) cannot be told apart from one. Replacements never span a tab
  or line break.
