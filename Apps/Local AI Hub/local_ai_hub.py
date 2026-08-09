from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk


ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8765"


def request_json(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return 0, {"status": "unavailable", "error": str(exc)}


def ensure_api() -> None:
    code, _ = request_json("GET", "/health")
    if code:
        return
    python = os.environ.get("LOCALAIHUB_PYTHON") or sys.executable
    subprocess.Popen(
        [python, "-m", "Hub.api_server"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    time.sleep(0.7)


class HubWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Local AI Hub")
        self.geometry("980x620")
        self.minsize(760, 480)
        self.status_var = tk.StringVar(value="Starting…")
        self.gpu_var = tk.StringVar(value="GPU: —")
        self.disk_var = tk.StringVar(value="Disk: —")
        self.jobs_var = tk.StringVar(value="Jobs: —")
        self._build()
        threading.Thread(target=ensure_api, daemon=True).start()
        self.after(1200, self.refresh)

    def _build(self) -> None:
        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="Local AI Hub", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(header, textvariable=self.status_var).pack(side="left", padx=20)
        for variable in (self.gpu_var, self.disk_var, self.jobs_var):
            ttk.Label(header, textvariable=variable).pack(side="right", padx=8)

        body = ttk.Frame(self, padding=(12, 0, 12, 12))
        body.pack(fill="both", expand=True)
        self.services = ttk.Treeview(body, columns=("status", "version", "model", "port"), show="headings")
        for key, title, width in (("status", "Status", 110), ("version", "Version", 100), ("model", "Model / Path", 460), ("port", "Port", 80)):
            self.services.heading(key, text=title)
            self.services.column(key, width=width, anchor="w")
        self.services.heading("status", text="Status")
        self.services.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.services.yview)
        scroll.pack(side="right", fill="y")
        self.services.configure(yscrollcommand=scroll.set)

        controls = ttk.Frame(self, padding=12)
        controls.pack(fill="x")
        ttk.Button(controls, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(controls, text="Open Output", command=lambda: os.startfile(ROOT / "Output")).pack(side="left", padx=6)
        ttk.Button(controls, text="Open Logs", command=lambda: os.startfile(ROOT / "Logs")).pack(side="left")
        ttk.Button(controls, text="Open Config", command=lambda: os.startfile(ROOT / "Config")).pack(side="left", padx=6)

    def refresh(self) -> None:
        threading.Thread(target=self._refresh_worker, daemon=True).start()
        self.after(5000, self.refresh)

    def _refresh_worker(self) -> None:
        health_code, health = request_json("GET", "/health")
        components_code, components = request_json("GET", "/components")
        if health_code:
            self.after(0, lambda: self._apply_health(health))
        if components_code:
            self.after(0, lambda: self._apply_components(components.get("components", [])))

    def _apply_health(self, data: dict) -> None:
        self.status_var.set(f"{data.get('status', 'unknown').upper()} · {data.get('bind', '')}")
        gpu = data.get("gpu") or {}
        self.gpu_var.set(f"GPU: {gpu.get('name', '—')} / {gpu.get('memory_free_mib', '—')} MiB free")
        disk = data.get("disk") or {}
        self.disk_var.set(f"Disk free: {round(int(disk.get('free_bytes', 0)) / 1_073_741_824, 1) if disk.get('free_bytes') else '—'} GB")
        self.jobs_var.set(f"Jobs: {data.get('active_jobs', 0)} active")

    def _apply_components(self, items: list[dict]) -> None:
        for row in self.services.get_children():
            self.services.delete(row)
        for item in items:
            model = item.get("model") or item.get("path") or ""
            self.services.insert("", "end", values=(item.get("observed_status", item.get("status", "")), item.get("version") or "", model, item.get("port") or ""))


if __name__ == "__main__":
    HubWindow().mainloop()
