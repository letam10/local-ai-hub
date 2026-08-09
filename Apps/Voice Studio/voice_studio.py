from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk


API = "http://127.0.0.1:8765"


def call_api(path: str, payload: dict) -> dict:
    request = urllib.request.Request(API + path, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": str(exc)}


class VoiceWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Voice Studio")
        self.geometry("860x600")
        self.text_var = tk.StringVar(value="Hello from Local AI Hub.")
        self.reference_var = tk.StringVar()
        self.language_var = tk.StringVar(value="auto")
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Voice Studio", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Text to Speech · Voice Design · Voice Clone · Voice Conversion · Voice Library · Batch · Settings").pack(anchor="w", pady=(4, 12))
        ttk.Label(frame, text="Text").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.text_var).pack(fill="x", pady=(2, 8))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="Language").pack(side="left")
        ttk.Entry(row, textvariable=self.language_var, width=12).pack(side="left", padx=(6, 18))
        ttk.Label(row, text="Reference audio").pack(side="left")
        ttk.Entry(row, textvariable=self.reference_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Choose", command=self.choose).pack(side="left")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=12)
        ttk.Button(buttons, text="Text to Speech", command=lambda: self.run("/voice/tts")).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Voice Design", command=lambda: self.run("/voice/design")).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Voice Clone", command=lambda: self.run("/voice/clone")).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Voice Conversion", command=lambda: self.run("/voice/convert")).pack(side="left")
        self.output = tk.Text(frame, wrap="word")
        self.output.pack(fill="both", expand=True)

    def choose(self) -> None:
        selected = filedialog.askopenfilename(filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a"), ("All files", "*.*")])
        if selected:
            self.reference_var.set(selected)

    def run(self, route: str) -> None:
        payload = {"text": self.text_var.get(), "language": self.language_var.get(), "reference_audio": self.reference_var.get() or None}
        self.output.insert("end", f"Calling {route}…\n")
        threading.Thread(target=self._worker, args=(route, payload), daemon=True).start()

    def _worker(self, route: str, payload: dict) -> None:
        result = call_api(route, payload)
        self.after(0, lambda: self.output.insert("end", json.dumps(result, ensure_ascii=False, indent=2) + "\n\n"))


if __name__ == "__main__":
    VoiceWindow().mainloop()
