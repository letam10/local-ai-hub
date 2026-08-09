from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk


API = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parents[3]


def call_api(path: str, payload: dict) -> dict:
    request = urllib.request.Request(API + path, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": str(exc)}


class VisionWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Vision Studio")
        self.geometry("900x620")
        self.path_var = tk.StringVar()
        self.prompt_var = tk.StringVar(value="person")
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Vision Studio", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(top, text="UI Parser · Object Detection · Grounded Detection · Segmentation / Tracking · OCR / Documents").pack(anchor="w", pady=(4, 12))
        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose image / PDF", command=self.choose).pack(side="left", padx=6)
        ttk.Label(top, text="Text prompt for Grounding DINO:").pack(anchor="w", pady=(10, 2))
        ttk.Entry(top, textvariable=self.prompt_var).pack(fill="x")

        buttons = ttk.Frame(self, padding=(12, 0, 12, 8))
        buttons.pack(fill="x")
        actions = [("UI Parser", "/vision/ui/parse"), ("Detect", "/vision/detect"), ("Ground", "/vision/ground"), ("Segment", "/vision/segment"), ("OCR / Document", "/ocr/parse")]
        for label, route in actions:
            ttk.Button(buttons, text=label, command=lambda r=route: self.run(r)).pack(side="left", padx=(0, 6))

        self.output = tk.Text(self, wrap="word", height=25)
        self.output.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def choose(self) -> None:
        selected = filedialog.askopenfilename(filetypes=[("Media", "*.png *.jpg *.jpeg *.webp *.pdf *.mp4"), ("All files", "*.*")])
        if selected:
            self.path_var.set(selected)

    def run(self, route: str) -> None:
        path = self.path_var.get().strip()
        if not path:
            self.output.insert("end", "Choose an input first.\n")
            return
        payload = {"path": path, "prompt": self.prompt_var.get().strip()}
        self.output.insert("end", f"Calling {route}…\n")
        threading.Thread(target=self._worker, args=(route, payload), daemon=True).start()

    def _worker(self, route: str, payload: dict) -> None:
        result = call_api(route, payload)
        self.after(0, lambda: self.output.insert("end", json.dumps(result, ensure_ascii=False, indent=2) + "\n\n"))


if __name__ == "__main__":
    VisionWindow().mainloop()
