import logging
import queue
import shutil
import tempfile
import threading
from pathlib import Path

from match_processor import (
    parse_setter_numbers,
    pdf_processing_error,
    process_match_pdf
)


LOGGER = logging.getLogger(__name__)


def main() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Volleyball-Spieltaganalyse")
    root.geometry("650x440")
    root.minsize(580, 400)

    outer = ttk.Frame(root, padding=24)
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(0, weight=1)

    ttk.Label(
        outer,
        text="Volleyball-Spieltaganalyse",
        font=("TkDefaultFont", 18, "bold")
    ).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(
        outer,
        text=(
            "Wählen Sie einen Spielbericht als PDF. Die Setter-Felder "
            "beziehen sich auf die Teams in der Reihenfolge des PDF-Kopfs."
        ),
        wraplength=590
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 20))

    pdf_path = tk.StringVar()
    team_a_setters = tk.StringVar()
    team_b_setters = tk.StringVar()
    status_text = tk.StringVar(value="Bereit.")
    work_queue: queue.Queue[tuple[bool, str]] = queue.Queue()

    ttk.Label(outer, text="Spielbericht (PDF)").grid(
        row=2, column=0, columnspan=2, sticky="w"
    )
    pdf_entry = ttk.Entry(outer, textvariable=pdf_path)
    pdf_entry.grid(row=3, column=0, sticky="ew", pady=(5, 14))

    def choose_pdf() -> None:
        selected = filedialog.askopenfilename(
            title="Spielbericht auswählen",
            filetypes=[("PDF-Dateien", "*.pdf"), ("Alle Dateien", "*.*")]
        )
        if selected:
            pdf_path.set(selected)

    browse_button = ttk.Button(
        outer,
        text="Durchsuchen …",
        command=choose_pdf
    )
    browse_button.grid(row=3, column=1, sticky="e", padx=(10, 0), pady=(5, 14))

    ttk.Label(
        outer,
        text="Setter des zuerst im PDF-Kopf genannten Teams"
    ).grid(row=4, column=0, columnspan=2, sticky="w")
    ttk.Entry(outer, textvariable=team_a_setters).grid(
        row=5, column=0, columnspan=2, sticky="ew", pady=(5, 14)
    )

    ttk.Label(
        outer,
        text="Setter des danach im PDF-Kopf genannten Teams"
    ).grid(row=6, column=0, columnspan=2, sticky="w")
    ttk.Entry(outer, textvariable=team_b_setters).grid(
        row=7, column=0, columnspan=2, sticky="ew", pady=(5, 6)
    )
    ttk.Label(
        outer,
        text=(
            "Mehrere Trikotnummern mit Kommas trennen (z. B. 4, 12). "
            "Felder können leer bleiben, wenn Setter unbekannt sind oder "
            "es sich um U14 handelt."
        ),
        wraplength=590
    ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 20))

    def set_busy(is_busy: bool) -> None:
        state = "disabled" if is_busy else "normal"
        start_button.configure(state=state)
        browse_button.configure(state=state)
        status_text.set("Analyse läuft …" if is_busy else "Bereit.")

    def run_analysis(
        source: Path,
        setters: list[list[int]],
        destination: Path
    ) -> None:
        try:
            with tempfile.TemporaryDirectory(
                prefix="spieltaganalyse-"
            ) as temporary_dir:
                report_path = process_match_pdf(
                    source,
                    setters,
                    temporary_dir
                )
                if source.resolve() == destination.resolve():
                    raise ValueError(
                        "Der Ausgabebericht darf nicht die Eingabe-PDF "
                        "überschreiben."
                    )
                shutil.copyfile(report_path, destination)
            work_queue.put((True, str(destination)))
        except Exception as error:
            if not isinstance(error, (ValueError, FileNotFoundError)):
                LOGGER.exception("Desktop match analysis failed")
            work_queue.put((False, pdf_processing_error(error)))

    def start_analysis() -> None:
        source_text = pdf_path.get().strip()
        if not source_text:
            messagebox.showerror(
                "PDF fehlt",
                "Bitte wählen Sie zuerst einen Spielbericht als PDF aus.",
                parent=root
            )
            return

        try:
            setters = [
                parse_setter_numbers(team_a_setters.get()),
                parse_setter_numbers(team_b_setters.get())
            ]
        except ValueError as error:
            messagebox.showerror(
                "Ungültige Setter-Angabe",
                str(error),
                parent=root
            )
            return

        source = Path(source_text)
        if not source.is_file():
            messagebox.showerror(
                "Datei nicht gefunden",
                "Die ausgewählte PDF-Datei wurde nicht gefunden.",
                parent=root
            )
            return

        destination_text = filedialog.asksaveasfilename(
            title="Analysebericht speichern",
            initialdir=str(source.parent),
            initialfile=f"{source.stem}_analyse.pdf",
            defaultextension=".pdf",
            filetypes=[("PDF-Dateien", "*.pdf")]
        )
        if not destination_text:
            return

        set_busy(True)
        threading.Thread(
            target=run_analysis,
            args=(source, setters, Path(destination_text)),
            daemon=True
        ).start()

    start_button = ttk.Button(
        outer,
        text="Analyse starten und Bericht speichern",
        command=start_analysis
    )
    start_button.grid(row=9, column=0, columnspan=2, sticky="w")
    ttk.Label(outer, textvariable=status_text).grid(
        row=10, column=0, columnspan=2, sticky="w", pady=(14, 0)
    )

    def poll_results() -> None:
        try:
            succeeded, message = work_queue.get_nowait()
        except queue.Empty:
            root.after(100, poll_results)
            return

        set_busy(False)
        if succeeded:
            status_text.set(f"Bericht gespeichert: {message}")
            messagebox.showinfo(
                "Analyse abgeschlossen",
                f"Der Analysebericht wurde gespeichert:\n{message}",
                parent=root
            )
        else:
            status_text.set("Analyse fehlgeschlagen.")
            messagebox.showerror(
                "Analyse nicht möglich",
                message or (
                    "Bei der Analyse ist ein unerwarteter Fehler aufgetreten."
                ),
                parent=root
            )
        root.after(100, poll_results)

    root.after(100, poll_results)
    root.mainloop()


if __name__ == "__main__":
    main()
