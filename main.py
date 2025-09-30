"""
Kindle Manga Optimizer v5.0 (MOBI + Preview pro)
- Presets legibles (manga limpio, antiguo, JPEG artifacts, texto B/N, sólo recorte)
- Vista previa en vivo: Antes/Después y 2×2 comparativo
- Pipeline pro: CLAHE, NLMeans, Unsharp sin halos, Sauvola/Niblack (ximgproc opcional)
- Recorte automático de bordes + margen blanco; Dither E-Ink opcional
- Export: nombres secuenciales de páginas (evita sobrescrituras)
- JPEG 4:4:4 + progresivo (líneas finas más limpias)
- KCC -> MOBI con metadatos; autodetección KCC y kindlegen (Kindle Previewer 3)
- Volumen inicial configurable; nombre de salida: "Serie - vNN.mobi"
"""
import os
import re
import sys
import shutil
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
import queue

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageEnhance, ImageOps, ImageTk, ImageDraw
import numpy as np
import cv2


# -------------------------- Modelos y Perfiles --------------------------
@dataclass
class Chapter:
    name: str
    dir: Path
    images: list[Path]
    enabled: bool = True

    @property
    def pages(self) -> int:
        return len(self.images)


@dataclass
class SourceProfile:
    key: str
    label: str
    chapter_dir_regex: re.Pattern | None
    image_file_regex: re.Pattern | None
    expects_subfolders: bool = True

    def sort_chapter_key(self, p: Path) -> tuple:
        if self.chapter_dir_regex:
            m = self.chapter_dir_regex.search(p.name)
            if m:
                try:
                    return (0, int(m.group(1)))
                except:
                    pass
        return (1, p.name.lower())

    def sort_image_key(self, f: Path) -> tuple:
        if self.image_file_regex:
            m = self.image_file_regex.search(f.name)
            if m:
                try:
                    return (0, int(m.group(1)))
                except:
                    pass
        return (1, f.name.lower())


PROFILE_TMO = SourceProfile(
    key="TMO",
    label="TMO (mixto)",
    chapter_dir_regex=re.compile(r"(?:cap[ií]tulo|chapter|ch)[^\d]*(\d+)", re.I),
    image_file_regex=re.compile(r"(\d+)\.(?:jpg|jpeg|png|webp)$", re.I),
    expects_subfolders=True
)

PROFILE_INMANGA = SourceProfile(
    key="INMANGA",
    label="INMANGA (Chapter XX / 001.jpg)",
    chapter_dir_regex=re.compile(r"chapter\s*0*?(\d+)$", re.I),
    image_file_regex=re.compile(r"0*?(\d+)\.(?:jpg|jpeg|png|webp)$", re.I),
    expects_subfolders=True
)

PROFILES = {
    PROFILE_TMO.key: PROFILE_TMO,
    PROFILE_INMANGA.key: PROFILE_INMANGA
}


# -------------------------- App --------------------------
class KindleMangaOptimizer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Kindle Manga Optimizer v5.0 (MOBI)")
        self.root.geometry("1180x880")

        # Config imagen (básicos)
        self.target_width = tk.IntVar(value=1200)
        self.jpg_quality = tk.IntVar(value=84)
        self.contrast_boost = tk.DoubleVar(value=1.15)
        self.sharpness_boost = tk.DoubleVar(value=1.2)
        self.noise_reduction = tk.BooleanVar(value=True)
        self.auto_contrast = tk.BooleanVar(value=True)
        self.to_grayscale = tk.BooleanVar(value=False)
        self.adaptive_threshold = tk.BooleanVar(value=False)  

        # Presets legibles
        self.preset_name = tk.StringVar(value="Manga limpio (rápido)")
        self.eink_dither = tk.BooleanVar(value=False)

        # Preview
        self.preview_mode = tk.StringVar(value="Antes/Después")  
        self.comp_presets = [
            tk.StringVar(value="Manga limpio (rápido)"),
            tk.StringVar(value="Manga antiguo / bajo contraste"),
            tk.StringVar(value="Escaneo con artefactos JPEG"),
            tk.StringVar(value="Texto pequeño B/N (letra clara)")
        ]

        # Preview: zoom y modo de ajuste
        self.zoom = tk.DoubleVar(value=1.0)                 
        self.fit_mode = tk.StringVar(value="Encajar (sin cortes)")  
        
                # ====== Lupa (zoom local bajo el mouse) ======
        self.magnifier_enabled = tk.BooleanVar(value=False) 
        self.magnifier_size = tk.IntVar(value=220)         
        self.magnifier_scale = tk.DoubleVar(value=2.0)      

        # Toggle rápido de la lupa con Q
        self.root.bind("<Key-q>", lambda e: self._toggle_magnifier())
        self.root.bind("<Key-Q>", lambda e: self._toggle_magnifier())

        # caches/estado de preview y transformaciones para mapear coords
        self._preview_pil = None      
        self._preview_photo = None
        self._canvas_img_id = None
        self._offset = [0, 0]          
        self._pan_start = None

        # estado de dibujo actual para invertir coordenadas (canvas -> imagen)
        self._draw_state = {
            "scale": 1.0,  
            "img_x": 0,     
            "img_y": 0,     
        }

        # overlay de lupa
        self._mag_photo = None
        self._mag_img_id = None

        # Estado interno para canvas (pan)
        self._preview_pil = None      
        self._preview_photo = None    
        self._canvas_img_id = None    
        self._pan_start = None        
        self._offset = [0, 0]         

        # Parsing/plan
        self.process_subfolders = tk.BooleanVar(value=True)
        self.group_size = tk.IntVar(value=10)
        self.profile_key = tk.StringVar(value="INMANGA")
        self.clean_ebooks_before = tk.BooleanVar(value=True)
        self.clean_temp_before = tk.BooleanVar(value=True)
        self.start_volume = tk.IntVar(value=1)  # Volumen inicial

        # Metadatos / nombres
        self.series_title = tk.StringVar(value="")
        self.author = tk.StringVar(value="")

        # Kindle Previewer 3 (para kindlegen)
        self.kp3_dir = tk.StringVar(
            value=r"C:\Users\arturo.tzakum\AppData\Local\Amazon\Kindle Previewer 3"
        )

        # Detectar ximgproc (Sauvola/Niblack)
        try:
            import cv2.ximgproc  # noqa
            self.has_ximgproc = True
        except Exception:
            self.has_ximgproc = False

        self.base_path = Path.cwd()
        self.setup_directories()

        self.chapters: list[Chapter] = []
        self.selected_folder: Path | None = None

        # threading / cancel
        self.worker_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.ui_queue: "queue.Queue[str]" = queue.Queue()

        self.setup_ui()
        self.root.after(100, self._drain_ui_queue)

    # ---------------- Directorios ----------------
    def setup_directories(self):
        for name in ['imagenes', 'procesadas', 'temp', 'ebooks']:
            (self.base_path / name).mkdir(exist_ok=True)

    # ---------------- UI ----------------
    def setup_ui(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        config_frame = ttk.Frame(notebook)
        notebook.add(config_frame, text="Configuración")
        self.setup_config_tab(config_frame)

        preview_frame = ttk.Frame(notebook)
        notebook.add(preview_frame, text="Vista Previa / Capítulos")
        self.setup_preview_tab(preview_frame)

        plan_frame = ttk.Frame(notebook)
        notebook.add(plan_frame, text="Plan de salida")
        self.setup_plan_tab(plan_frame)

        process_frame = ttk.Frame(notebook)
        notebook.add(process_frame, text="Procesar")
        self.setup_process_tab(process_frame)

    def setup_config_tab(self, parent):
        # Origen
        folder_frame = ttk.LabelFrame(parent, text="Origen")
        folder_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(folder_frame, text="Seleccionar carpeta raíz",
                   command=self.select_folder).pack(side=tk.LEFT, padx=5, pady=10)
        self.folder_label = ttk.Label(folder_frame, text="Ninguna carpeta seleccionada")
        self.folder_label.pack(side=tk.LEFT, padx=10)

        # Perfil
        profile_frame = ttk.LabelFrame(parent, text="Perfil de parsing")
        profile_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(profile_frame, text="Perfil:").pack(side=tk.LEFT, padx=(6, 4))
        self.profile_combo = ttk.Combobox(profile_frame, textvariable=self.profile_key, state="readonly",
                                          values=list(PROFILES.keys()))
        self.profile_combo.pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(profile_frame, text="Procesar subcarpetas como capítulos",
                        variable=self.process_subfolders, command=self.rescan_if_ready)\
            .pack(side=tk.LEFT, padx=12)

        # Metadatos / nombres
        meta_frame = ttk.LabelFrame(parent, text="Metadatos / Nombres de salida")
        meta_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(meta_frame, text="Serie/Título:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(meta_frame, textvariable=self.series_title, width=40).grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(meta_frame, text="Autor (opcional):").grid(row=1, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(meta_frame, textvariable=self.author, width=40).grid(row=1, column=1, padx=6, pady=4)
        ttk.Label(meta_frame, text="(El archivo será: 'Serie - vNN.mobi')").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=6)

        # Kindle Previewer 3
        kp_frame = ttk.LabelFrame(parent, text="Kindle Previewer 3 (para MOBI / KindleGen)")
        kp_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(kp_frame, text="Ruta instalación KP3:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(kp_frame, textvariable=self.kp3_dir, width=70).grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(kp_frame, text="(Se buscará 'kindlegen.exe' dentro de esta carpeta)").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=6)

        # Imagen (básicos)
        img_config = ttk.LabelFrame(parent, text="Configuración de imagen (básicos)")
        img_config.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(img_config, text="Ancho objetivo (px):").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Spinbox(img_config, from_=800, to=2000, textvariable=self.target_width, width=10)\
            .grid(row=0, column=1, padx=4)
        ttk.Label(img_config, text="Calidad JPG (%):").grid(row=1, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Spinbox(img_config, from_=50, to=100, textvariable=self.jpg_quality, width=10)\
            .grid(row=1, column=1, padx=4)

        visual_frame = ttk.LabelFrame(parent, text="Ajustes finos (se aplican tras el preset)")
        visual_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Checkbutton(visual_frame, text="Auto-contraste (global)", variable=self.auto_contrast).pack(anchor=tk.W)
        ttk.Checkbutton(visual_frame, text="Reducción de ruido básica (bilateral)", variable=self.noise_reduction).pack(anchor=tk.W)
        ttk.Checkbutton(visual_frame, text="Escala de grises inicial", variable=self.to_grayscale).pack(anchor=tk.W)
        ttk.Checkbutton(visual_frame, text="(legacy) Umbral adaptativo", variable=self.adaptive_threshold).pack(anchor=tk.W)
        ttk.Label(visual_frame, text="Contraste global:").pack(anchor=tk.W)
        ttk.Scale(visual_frame, from_=0.5, to=2.0, variable=self.contrast_boost, orient=tk.HORIZONTAL).pack(fill=tk.X)
        ttk.Label(visual_frame, text="Nitidez global:").pack(anchor=tk.W)
        ttk.Scale(visual_frame, from_=0.5, to=3.0, variable=self.sharpness_boost, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Presets legibles
        presETF = ttk.LabelFrame(parent, text="Presets de mejora (recomendados)")
        presETF.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(presETF, text="Preset:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Combobox(
            presETF, textvariable=self.preset_name, state="readonly",
            values=[
                "Manga limpio (rápido)",
                "Manga antiguo / bajo contraste",
                "Escaneo con artefactos JPEG",
                "Texto pequeño B/N (letra clara)",
                "Sólo recorte y márgenes"
            ]
        ).grid(row=0, column=1, padx=6, pady=4)
        ttk.Checkbutton(presETF, text="Dither E-Ink (Floyd–Steinberg)", variable=self.eink_dither)\
            .grid(row=0, column=2, padx=12, pady=4)

        # Agrupación
        grouping = ttk.LabelFrame(parent, text="Agrupación de capítulos")
        grouping.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(grouping, text="Capítulos por archivo (volumen):").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Spinbox(grouping, from_=1, to=100, textvariable=self.group_size, width=6,
                    command=self.update_plan_view).grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(grouping, text="Volumen inicial (vNN):").grid(row=0, column=2, sticky=tk.W, padx=12, pady=4)
        ttk.Spinbox(grouping, from_=1, to=999, textvariable=self.start_volume, width=6,
                    command=self.update_plan_view).grid(row=0, column=3, padx=6, pady=4)
        ttk.Button(grouping, text="Recalcular plan", command=self.update_plan_view)\
            .grid(row=0, column=4, padx=12, pady=4)

        # Limpieza
        cleanf = ttk.LabelFrame(parent, text="Limpieza automática antes de convertir")
        cleanf.pack(fill=tk.X, padx=5, pady=5)
        ttk.Checkbutton(cleanf, text="Limpiar temp/", variable=self.clean_temp_before).pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(cleanf, text="Limpiar ebooks/ (salida)", variable=self.clean_ebooks_before)\
            .pack(side=tk.LEFT, padx=6)

    def setup_preview_tab(self, parent):
        # Left: lista de capítulos
        left = ttk.Frame(parent)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0,6), pady=6)
        ttk.Label(left, text="Capítulos (orden y selección):").pack(anchor=tk.W)
        self.chapter_list = tk.Listbox(left, width=48, exportselection=False)
        self.chapter_list.pack(fill=tk.BOTH, expand=True)
        self.chapter_list.bind('<<ListboxSelect>>', self.on_chapter_select)

        btns = ttk.Frame(left)
        btns.pack(fill=tk.X, pady=6)
        ttk.Button(btns, text="⬆️ Subir", command=lambda: self.move_chapter(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="⬇️ Bajar", command=lambda: self.move_chapter(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="✅ Incluir / ❌ Excluir", command=self.toggle_chapter).pack(side=tk.LEFT, padx=6)

        # Right: controles de preview + Canvas
        right = ttk.Frame(parent)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6,0), pady=6)

        # Controles superiores
        ctrl = ttk.Frame(right)
        ctrl.pack(fill=tk.X, pady=(0,6))
        ttk.Label(ctrl, text="Vista:").pack(side=tk.LEFT)
        ttk.Combobox(ctrl, textvariable=self.preview_mode, state="readonly",
                     values=["Antes/Después", "2x2"]).pack(side=tk.LEFT, padx=6)

        ttk.Label(ctrl, text="Ajuste:").pack(side=tk.LEFT, padx=(12,4))
        ttk.Combobox(ctrl, textvariable=self.fit_mode, state="readonly",
                     values=["Encajar (sin cortes)", "Zoom manual"]).pack(side=tk.LEFT, padx=4)

        ttk.Button(ctrl, text="Actualizar vista", command=self.render_preview_now).pack(side=tk.LEFT, padx=10)

        # Controles de zoom
        zoomf = ttk.Frame(right)
        zoomf.pack(fill=tk.X, pady=(0,6))
        ttk.Label(zoomf, text="Zoom:").pack(side=tk.LEFT)
        ttk.Button(zoomf, text="–", command=lambda: self._nudge_zoom(-0.1)).pack(side=tk.LEFT, padx=2)
        ttk.Scale(zoomf, from_=0.5, to=3.0, variable=self.zoom, orient=tk.HORIZONTAL,
                  command=lambda e: self._redraw_preview()).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(zoomf, text="+", command=lambda: self._nudge_zoom(+0.1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(zoomf, text="Encajar", command=self._fit_view).pack(side=tk.LEFT, padx=6)

        # ----- Controles de LUPA -----
        magf = ttk.Frame(right)
        magf.pack(fill=tk.X, pady=(0,6))
        ttk.Checkbutton(magf, text="Lupa (Q)", variable=self.magnifier_enabled,
                        command=lambda: self._clear_magnifier()).pack(side=tk.LEFT)
        ttk.Label(magf, text="Tamaño:").pack(side=tk.LEFT, padx=(12,4))
        ttk.Spinbox(magf, from_=120, to=420, increment=20,
                    textvariable=self.magnifier_size, width=6,
                    command=self._clear_magnifier).pack(side=tk.LEFT)
        ttk.Label(magf, text="Factor:").pack(side=tk.LEFT, padx=(12,4))
        ttk.Spinbox(magf, from_=1.5, to=3.0, increment=0.1,
                    textvariable=self.magnifier_scale, width=5,
                    command=self._clear_magnifier).pack(side=tk.LEFT)
        ttk.Label(magf, text="(mantén el mouse sobre la imagen)").pack(side=tk.LEFT, padx=8)

        # Selección de presets para 2x2
        grid_ctrl = ttk.Frame(right)
        grid_ctrl.pack(fill=tk.X)
        self.comp_boxes = []
        for i in range(4):
            b = ttk.Combobox(grid_ctrl, textvariable=self.comp_presets[i], state="readonly",
                values=[
                    "Manga limpio (rápido)",
                    "Manga antiguo / bajo contraste",
                    "Escaneo con artefactos JPEG",
                    "Texto pequeño B/N (letra clara)",
                    "Sólo recorte y márgenes"
                ])
            b.grid(row=0, column=i, padx=4, pady=4)
            self.comp_boxes.append(b)

        # Canvas de preview (con scroll/zoom manual)
        self.preview_canvas = tk.Canvas(right, bg="#222222", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)

        # Eventos para redibujar y pan
        self.preview_canvas.bind("<Configure>", lambda e: self._redraw_preview())
        self.preview_canvas.bind("<ButtonPress-1>", self._pan_start_evt)
        self.preview_canvas.bind("<B1-Motion>", self._pan_drag_evt)
        self.preview_canvas.bind("<Motion>", self._on_mouse_move)
        self.preview_canvas.bind("<Leave>", lambda e: self._clear_magnifier())
        # Mensaje inicial
        self.preview_canvas.create_text(
            10, 10, text="Selecciona un capítulo y pulsa 'Actualizar vista'.",
            anchor="nw", fill="#ddd", font=("Segoe UI", 11)
        )


    def setup_plan_tab(self, parent):
        ttk.Label(parent, text="Plan de salida (previo a convertir):")\
            .pack(anchor=tk.W, padx=4, pady=(6,0))
        self.plan_tree = ttk.Treeview(parent, columns=("pages",), show="tree headings", height=12)
        self.plan_tree.heading("#0", text="Volumen / Capítulo")
        self.plan_tree.heading("pages", text="Páginas")
        self.plan_tree.column("#0", width=720, anchor=tk.W)
        self.plan_tree.column("pages", width=120, anchor=tk.CENTER)
        self.plan_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.plan_summary = ttk.Label(parent, text="—")
        self.plan_summary.pack(anchor=tk.W, padx=6, pady=(0,8))

    def setup_process_tab(self, parent):
        info_frame = ttk.LabelFrame(parent, text="Estado del procesamiento")
        info_frame.pack(fill=tk.X, padx=5, pady=5)
        self.status_label = ttk.Label(info_frame, text="Listo para procesar")
        self.status_label.pack(pady=6)
        self.progress = ttk.Progressbar(info_frame, mode='determinate')
        self.progress.pack(fill=tk.X, padx=10, pady=5)
        self.progress_images = ttk.Progressbar(info_frame, mode='determinate')
        self.progress_images.pack(fill=tk.X, padx=10, pady=(0,8))

        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, padx=5, pady=8)
        self.btn_convert = ttk.Button(button_frame, text="🔄 Convertir según plan", command=self.start_process_thread)
        self.btn_convert.pack(side=tk.LEFT, padx=5)
        self.btn_cancel = ttk.Button(button_frame, text="✖ Cancelar", command=self.cancel_process, state="disabled")
        self.btn_cancel.pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="📚 Abrir carpeta MOBI", command=self.open_ebooks_folder).pack(side=tk.LEFT, padx=5)

        log_frame = ttk.LabelFrame(parent, text="Log de actividad")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text = tk.Text(log_frame, height=12, wrap=tk.WORD)
        log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------------- Cola de mensajes (UI / hilos) ----------------
    def _drain_ui_queue(self):
        while True:
            try:
                msg = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            self._log_ui(msg)
        self.root.after(100, self._drain_ui_queue)

    def ui_log(self, message: str):
        self.ui_queue.put(message)

    def _log_ui(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)

    def log(self, message: str):
        self.ui_log(message)

    # ---------------- Origen/Parsing ----------------
    def select_folder(self):
        folder = filedialog.askdirectory(title="Seleccionar carpeta raíz")
        if folder:
            self.selected_folder = Path(folder)
            if not self.series_title.get().strip():
                self.series_title.set(self.selected_folder.name)
            self.folder_label.config(text=f"Carpeta: {self.selected_folder.name}")
            self.scan_images()

    def rescan_if_ready(self):
        if self.selected_folder:
            self.scan_images()

    def scan_images(self):
        self.chapters.clear()
        if not self.selected_folder:
            return
        profile = PROFILES[self.profile_key.get()]
        formats = {'.jpg', '.jpeg', '.png', '.webp'}
        if profile.expects_subfolders and self.process_subfolders.get():
            subdirs = [d for d in self.selected_folder.iterdir() if d.is_dir()]
            subdirs.sort(key=profile.sort_chapter_key)
            for sub in subdirs:
                files = [f for f in sub.iterdir() if f.is_file() and f.suffix.lower() in formats]
                files.sort(key=profile.sort_image_key)
                if files:
                    self.chapters.append(Chapter(name=sub.name, dir=sub, images=files))
        else:
            files = [f for f in self.selected_folder.iterdir() if f.is_file() and f.suffix.lower() in formats]
            files.sort(key=profile.sort_image_key)
            if files:
                self.chapters.append(Chapter(name=self.selected_folder.name, dir=self.selected_folder, images=files))
        self.refresh_chapter_list()
        self.update_plan_view()
        self.log(f"Escaneo completo: {len(self.chapters)} capítulo(s). Perfil={profile.key}")

    def refresh_chapter_list(self):
        self.chapter_list.delete(0, tk.END)
        for ch in self.chapters:
            tag = "✅" if ch.enabled else "❌"
            self.chapter_list.insert(tk.END, f"{tag} {ch.name}  ({ch.pages} págs)")

    def on_chapter_select(self, event=None):
        # no renderizamos automáticamente; pedimos "Actualizar vista" para no bloquear
        pass

    def move_chapter(self, delta: int):
        idxs = self.chapter_list.curselection()
        if not idxs:
            return
        i = idxs[0]
        j = i + delta
        if 0 <= j < len(self.chapters):
            self.chapters[i], self.chapters[j] = self.chapters[j], self.chapters[i]
            self.refresh_chapter_list()
            self.chapter_list.select_set(j)
            self.update_plan_view()

    def toggle_chapter(self):
        idxs = self.chapter_list.curselection()
        if not idxs:
            return
        i = idxs[0]
        self.chapters[i].enabled = not self.chapters[i].enabled
        self.refresh_chapter_list()
        self.chapter_list.select_set(i)
        self.update_plan_view()

    # ---------------- Preview helpers ----------------
    def render_preview_now(self):
        idxs = self.chapter_list.curselection()
        if not idxs:
            self._preview_pil = None
            self._draw_canvas_message("Selecciona un capítulo en la lista.")
            return
        ch = self.chapters[idxs[0]]
        if not ch.images:
            self._preview_pil = None
            self._draw_canvas_message("Capítulo sin imágenes.")
            return

        img_path = ch.images[0]
        try:
            orig = Image.open(img_path).convert("RGB")
        except Exception as e:
            self._preview_pil = None
            self._draw_canvas_message(f"Error cargando imagen: {e}")
            return

        mode = self.preview_mode.get()
        if mode == "Antes/Después":
            proc = self.enhance_image_preset(orig, self.preset_name.get())
            composite = self._compose_side_by_side(orig, proc, title_left="Original", title_right=self.preset_name.get())
        else:
            imgs = []
            titles = []
            for v in self.comp_presets:
                p = v.get()
                imgs.append(self.enhance_image_preset(orig, p))
                titles.append(p)
            composite = self._compose_grid_2x2(orig, imgs, titles)

        self._preview_pil = composite
        self._offset = [0, 0]  # reset pan al generar nueva imagen
        self._redraw_preview()


    def _draw_canvas_message(self, text):
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(
            10, 10, text=text,
            anchor="nw", fill="#ddd", font=("Segoe UI", 11)
        )

    def _nudge_zoom(self, delta):
        z = max(0.5, min(3.0, float(self.zoom.get()) + delta))
        self.zoom.set(z)
        self._redraw_preview()

    def _fit_view(self):
        # fuerza "Encajar (sin cortes)" y centra
        self.fit_mode.set("Encajar (sin cortes)")
        self.zoom.set(1.0)
        self._offset = [0, 0]
        self._redraw_preview()

    def _pan_start_evt(self, event):
        if self.fit_mode.get() != "Zoom manual":
            return
        self._pan_start = (event.x, event.y)

    def _pan_drag_evt(self, event):
        if self.fit_mode.get() != "Zoom manual" or self._pan_start is None:
            return
        dx = event.x - self._pan_start[0]
        dy = event.y - self._pan_start[1]
        self._pan_start = (event.x, event.y)
        self._offset[0] += dx
        self._offset[1] += dy
        self._redraw_preview()

    def _redraw_preview(self):
        canvas = self.preview_canvas
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        canvas.delete("all")  # borramos fondo/imagen (overlay de lupa se vuelve a dibujar en <Motion>)

        if not self._preview_pil:
            self._draw_canvas_message("Sin vista previa (elige capítulo y pulsa 'Actualizar vista').")
            return

        pil = self._preview_pil
        zoom = float(self.zoom.get())

        if self.fit_mode.get() == "Encajar (sin cortes)":
            sx = cw / pil.width
            sy = ch / pil.height
            base = min(sx, sy)
            # si quieres NO permitir ampliar más de que quepa, limita zoom:
            s = min(base, base * zoom)   # cabe siempre, el zoom no supera el encaje
            tw = max(1, int(pil.width * s))
            th = max(1, int(pil.height * s))
            img = pil.resize((tw, th), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(img)
            x = (cw - tw) // 2
            y = (ch - th) // 2
            self._canvas_img_id = canvas.create_image(x, y, anchor="nw", image=self._preview_photo)

            # === guarda transformación para la lupa ===
            self._draw_state["scale"] = s
            self._draw_state["img_x"] = x
            self._draw_state["img_y"] = y

        else:  # Zoom manual
            s = zoom
            tw = max(1, int(pil.width * s))
            th = max(1, int(pil.height * s))
            img = pil.resize((tw, th), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(img)
            cx = (cw - tw) // 2 + self._offset[0]
            cy = (ch - th) // 2 + self._offset[1]
            self._canvas_img_id = canvas.create_image(cx, cy, anchor="nw", image=self._preview_photo)

            # === guarda transformación para la lupa ===
            self._draw_state["scale"] = s
            self._draw_state["img_x"] = cx
            self._draw_state["img_y"] = cy

        # al terminar un redraw, borra posible lupa previa
        self._clear_magnifier()
    
    def _toggle_magnifier(self):
        self.magnifier_enabled.set(not self.magnifier_enabled.get())
        self._clear_magnifier()

    def _clear_magnifier(self):
        # borra overlay de lupa (usamos una etiqueta/tag para borrado masivo)
        self.preview_canvas.delete("magnifier")
        self._mag_photo = None
        self._mag_img_id = None

    def _on_mouse_move(self, event):
        # si no hay imagen o la lupa está off, no hacemos nada
        if not self._preview_pil or not self.magnifier_enabled.get():
            return

        # mapeamos coords canvas -> coords de imagen compuesta (previa al escalado)
        s = self._draw_state["scale"]
        ix0 = self._draw_state["img_x"]
        iy0 = self._draw_state["img_y"]

        mx, my = event.x, event.y
        # posición del mouse relativa al origen de la imagen dibujada
        rx = mx - ix0
        ry = my - iy0

        # si estamos fuera del bitmap, borra la lupa y sal
        if rx < 0 or ry < 0:
            self._clear_magnifier(); return
        iw, ih = self._preview_pil.width, self._preview_pil.height
        # coordenadas en la imagen original (antes de escalar)
        px = rx / s
        py = ry / s
        if px < 0 or py < 0 or px >= iw or py >= ih:
            self._clear_magnifier(); return

        # Calcula caja de recorte en la imagen original para llenar el cuadro de lupa
        L = int(self.magnifier_size.get())           # tamaño de la lupa en el canvas
        Z = float(self.magnifier_scale.get())        # factor adicional de zoom en la lupa

        # Ancho/alto del recorte en "imagen original" que, al escalar por (s * Z), llena L
        # L = crop_w * s * Z  => crop_w = L / (s * Z)
        crop_w = max(8, int(L / (s * Z)))
        crop_h = crop_w  # cuadrado

        cx = int(px)
        cy = int(py)
        x1 = max(0, cx - crop_w // 2)
        y1 = max(0, cy - crop_h // 2)
        x2 = min(iw, x1 + crop_w)
        y2 = min(ih, y1 + crop_h)

        # corrige si al pegar el recorte se quedó pequeño por bordes
        if x2 - x1 < crop_w:
            x1 = max(0, x2 - crop_w)
        if y2 - y1 < crop_h:
            y1 = max(0, y2 - crop_h)

        # recorta y escala al tamaño L×L (si el recorte saliera fuera, ya lo limitamos arriba)
        try:
            tile = self._preview_pil.crop((x1, y1, x2, y2)).resize((L, L), Image.Resampling.LANCZOS)
        except Exception:
            self._clear_magnifier(); return

        # convierte a PhotoImage y dibuja como overlay con tag "magnifier"
        self._mag_photo = ImageTk.PhotoImage(tile)
        # centramos la lupa en el cursor
        lx = mx - L // 2
        ly = my - L // 2

        # borra cualquier overlay previo y dibuja nuevo
        self.preview_canvas.delete("magnifier")
        self._mag_img_id = self.preview_canvas.create_image(lx, ly, anchor="nw",
                                                            image=self._mag_photo, tags=("magnifier",))
        # marco alrededor
        self.preview_canvas.create_rectangle(lx, ly, lx + L, ly + L, outline="#00D1FF",
                                             width=2, tags=("magnifier",))
        # sombra suave opcional (marco externo)
        self.preview_canvas.create_rectangle(lx-1, ly-1, lx+L+1, ly+L+1, outline="#00141A",
                                             width=1, tags=("magnifier",))
        
    def _toggle_magnifier(self):
        new_state = not self.magnifier_enabled.get()
        self.magnifier_enabled.set(new_state)
        self._clear_magnifier()
        self.log(f"Lupa {'activada' if new_state else 'desactivada'} (Q)")

    def _compose_side_by_side(self, left_img: Image.Image, right_img: Image.Image,
                              title_left="Original", title_right="Procesado") -> Image.Image:
        # normaliza alturas
        h = 900
        l = left_img.copy()
        r = right_img.copy()
        l.thumbnail((9999, h), Image.Resampling.LANCZOS)
        r.thumbnail((9999, h), Image.Resampling.LANCZOS)
        w = l.width + r.width
        band_h = 36
        canvas = Image.new("RGB", (w, h + band_h), (30,30,30))
        # bandas y títulos
        draw = ImageDraw.Draw(canvas)
        canvas.paste(l, (0, band_h))
        canvas.paste(r, (l.width, band_h))
        draw.rectangle([(0,0),(l.width,band_h)], fill=(50,50,50))
        draw.rectangle([(l.width,0),(w,band_h)], fill=(50,50,50))
        draw.text((8,8), title_left, fill=(255,255,255))
        draw.text((l.width+8,8), title_right, fill=(255,255,255))
        return canvas

    def _compose_grid_2x2(self, orig: Image.Image, procs: list[Image.Image], titles: list[str]) -> Image.Image:
        # orig + 3 procesadas? No: usamos solo procesadas (los títulos dicen preset). Si prefieres, pon original en [0].
        imgs = [orig] + procs[:3]
        titles = ["Original"] + titles[:3]
        # normaliza cada una
        cell_w, cell_h = 600, 800
        band_h = 36
        cells = []
        for im in imgs:
            i2 = im.copy()
            i2.thumbnail((cell_w, cell_h-band_h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (cell_w, cell_h), (30,30,30))
            draw = ImageDraw.Draw(canvas)
            draw.rectangle([(0,0),(cell_w,band_h)], fill=(50,50,50))
            canvas.paste(i2, ((cell_w - i2.width)//2, band_h + (cell_h-band_h - i2.height)//2))
            cells.append(canvas)
        # grid 2x2
        grid = Image.new("RGB", (cell_w*2, cell_h*2), (20,20,20))
        positions = [(0,0),(cell_w,0),(0,cell_h),(cell_w,cell_h)]
        draw_g = ImageDraw.Draw(grid)
        for idx, (cx,cy) in enumerate(positions):
            grid.paste(cells[idx], (cx, cy))
            # título
            draw_g.text((cx+8, cy+8), titles[idx], fill=(255,255,255))
        return grid

    # ---------------- Imagen: helpers pro ----------------
    def _to_cv(self, img):
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    def _from_cv(self, mat):
        return Image.fromarray(cv2.cvtColor(mat, cv2.COLOR_BGR2RGB))

    def _unsharp_mask(self, img_cv, radius=1.2, amount=0.7):
        blur = cv2.GaussianBlur(img_cv, (0,0), radius)
        return cv2.addWeighted(img_cv, 1+amount, blur, -amount, 0)

    def _clahe_gray(self, img_cv, clip=2.0, tile=8):
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile,tile))
        g2 = clahe.apply(gray)
        return cv2.cvtColor(g2, cv2.COLOR_GRAY2BGR)

    def _nl_means(self, img_cv, strength=7):
        try:
            return cv2.fastNlMeansDenoisingColored(img_cv, None, strength, strength, 7, 21)
        except Exception:
            # fallback a bilateral si no está disponible
            return cv2.bilateralFilter(img_cv, 9, 75, 75)

    def _sauvola_like(self, img_cv):
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        if self.has_ximgproc:
            bin_ = cv2.ximgproc.niBlackThreshold(
                gray, maxValue=255, type=cv2.THRESH_BINARY, blockSize=35, k=0.2
            )
        else:
            bin_ = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 10
            )
        return cv2.cvtColor(bin_, cv2.COLOR_GRAY2BGR)

    def _auto_trim_and_pad(self, img_cv, pad_px=16):
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
        contours, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img_cv
        x,y,w,h = cv2.boundingRect(np.vstack(contours))
        cropped = img_cv[y:y+h, x:x+w]
        h_, w_ = cropped.shape[:2]
        canvas = np.full((h_ + 2*pad_px, w_ + 2*pad_px, 3), 255, dtype=np.uint8)
        canvas[pad_px:pad_px+h_, pad_px:pad_px+w_] = cropped
        return canvas

    def _apply_eink_dither(self, pil_img_rgb):
        return pil_img_rgb.convert("P", palette=Image.ADAPTIVE, colors=256, dither=Image.FLOYDSTEINBERG).convert("RGB")

    # ---------------- Imagen: preset principal ----------------
    def enhance_image_preset(self, img: Image.Image, preset: str) -> Image.Image:
        # Paso 0: básicos previos (compatibilidad con tus toggles)
        if self.to_grayscale.get():
            img = ImageOps.grayscale(img).convert("RGB")
        if self.auto_contrast.get():
            img = ImageOps.autocontrast(img)
        if self.noise_reduction.get():
            try:
                cv_tmp = self._to_cv(img)
                cv_tmp = cv2.bilateralFilter(cv_tmp, 9, 75, 75)
                img = self._from_cv(cv_tmp)
            except Exception:
                pass
        if self.adaptive_threshold.get():
            try:
                cv_tmp = self._to_cv(img)
                gray = cv2.cvtColor(cv_tmp, cv2.COLOR_BGR2GRAY)
                thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                            cv2.THRESH_BINARY, 35, 10)
                img = Image.fromarray(thr).convert("RGB")
            except Exception:
                pass

        img_cv = self._to_cv(img)

        # Preset legible
        p = preset.strip().lower()
        if p.startswith("manga limpio"):
            img_cv = self._clahe_gray(img_cv, clip=2.0, tile=8)
            img_cv = self._unsharp_mask(img_cv, radius=1.0, amount=0.6)

        elif p.startswith("manga antiguo"):
            img_cv = self._clahe_gray(img_cv, clip=2.6, tile=8)
            img_cv = self._unsharp_mask(img_cv, radius=1.0, amount=0.5)

        elif p.startswith("escaneo con artefactos"):
            img_cv = self._nl_means(img_cv, strength=6)
            img_cv = self._unsharp_mask(img_cv, radius=1.2, amount=0.6)

        elif p.startswith("texto pequeño"):
            img_cv = self._sauvola_like(img_cv)

        elif p.startswith("sólo recorte"):
            pass  # se aplicará recorte/pad abajo

        # Recorte + margen
        img_cv = self._auto_trim_and_pad(img_cv, pad_px=16)

        # Ajustes finos globales
        img = self._from_cv(img_cv)
        img = ImageEnhance.Contrast(img).enhance(self.contrast_boost.get())
        img = ImageEnhance.Sharpness(img).enhance(self.sharpness_boost.get())

        if self.eink_dither.get():
            img = self._apply_eink_dither(img)

        return img

    # (compat) versión usada en procesamiento final con el preset actual
    def enhance_image(self, img: Image.Image) -> Image.Image:
        return self.enhance_image_preset(img, self.preset_name.get())

    # ---------------- Procesamiento de páginas ----------------
    def process_single_image_seq(self, path: Path, dest: Path, seq_num: int) -> Path | None:
        try:
            img = Image.open(path).convert("RGB")
            if img.width > self.target_width.get():
                h = int(img.height * self.target_width.get() / img.width)
                img = img.resize((self.target_width.get(), h), Image.Resampling.LANCZOS)
            img = self.enhance_image(img)
            out = dest / f"{seq_num:05d}.jpg"
            img.save(
                out, "JPEG",
                quality=int(self.jpg_quality.get()),
                optimize=True,
                subsampling=0,      # 4:4:4
                progressive=True
            )
            return out
        except Exception as e:
            self.log(f"Error procesando {path.name}: {e}")
            return None

    # ---------------- Planificación ----------------
    def build_plan(self):
        enabled = [c for c in self.chapters if c.enabled]
        g = max(1, int(self.group_size.get()))
        return [enabled[i:i+g] for i in range(0, len(enabled), g)]

    def update_plan_view(self):
        for i in self.plan_tree.get_children():
            self.plan_tree.delete(i)
        plan = self.build_plan()
        total_vols = len(plan)
        total_pages = sum(ch.pages for vol in plan for ch in vol)

        start_v = max(1, int(self.start_volume.get()))
        for idx, vol in enumerate(plan):
            vnum = start_v + idx
            vol_pages = sum(ch.pages for ch in vol)
            vol_id = self.plan_tree.insert("", "end", text=f"Volumen {vnum:02d} (v{vnum:02d})", values=(vol_pages,))
            for ch in vol:
                tag = "✅" if ch.enabled else "❌"
                self.plan_tree.insert(vol_id, "end", text=f"  {tag} {ch.name}", values=(ch.pages,))
        self.plan_summary.config(text=f"Volúmenes: {total_vols}   Páginas totales: {total_pages}")

    # ---------------- Conversión (hilo) ----------------
    def start_process_thread(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.cancel_event.clear()
        self.btn_convert.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.worker_thread = threading.Thread(target=self._process_plan_worker, daemon=True)
        self.worker_thread.start()

    def cancel_process(self):
        self.cancel_event.set()
        self.log("⚠ Cancelando... (se detendrá al finalizar el paso en curso)")

    def _process_plan_worker(self):
        try:
            if not self.selected_folder:
                self.log("⚠ Selecciona primero una carpeta.")
                return
            plan = self.build_plan()
            if not plan:
                self.log("⚠ No hay capítulos habilitados.")
                return

            temp_dir = self.base_path / 'temp'
            ebooks_dir = self.base_path / 'ebooks'

            if self.clean_temp_before.get():
                shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)

            if self.clean_ebooks_before.get():
                shutil.rmtree(ebooks_dir, ignore_errors=True)
            ebooks_dir.mkdir(parents=True, exist_ok=True)

            series = self.series_title.get().strip() or (self.selected_folder.name if self.selected_folder else "Manga")
            total_vols = len(plan)
            self.log(f"Inicio de conversión: {total_vols} volúmen(es). Serie: {series}")

            self._set_progress(self.progress, maximum=total_vols, value=0)
            created = 0

            start_v = max(1, int(self.start_volume.get()))
            for idx, vol in enumerate(plan):
                if self.cancel_event.is_set():
                    self.log("⛔ Proceso cancelado por el usuario.")
                    break

                vnum = start_v + idx
                vol_tmp = temp_dir / f"vol_{vnum:02d}"
                shutil.rmtree(vol_tmp, ignore_errors=True)
                vol_tmp.mkdir(parents=True, exist_ok=True)

                total_imgs = sum(len(ch.images) for ch in vol)
                self._set_progress(self.progress_images, maximum=max(1, total_imgs), value=0)
                seq = 1
                for ch in vol:
                    for img in ch.images:
                        if self.cancel_event.is_set():
                            break
                        self.process_single_image_seq(img, vol_tmp, seq_num=seq)
                        seq += 1
                        self._set_progress(self.progress_images, value=seq-1)
                    if self.cancel_event.is_set():
                        break

                if self.cancel_event.is_set():
                    self.log("⛔ Proceso cancelado tras exportar imágenes.")
                    break

                out_name = f"{series} - v{vnum:02d}"
                ok = self.convert_folder_to_mobi(vol_tmp, out_name, series_title=series, volume_index=vnum)
                if ok:
                    created += 1
                else:
                    self.log(f"❌ Falló conversión del volumen v{vnum:02d} (continuando con el siguiente).")

                self._set_progress(self.progress, value=idx + 1)

            self.log(f"✅ Proceso finalizado. {created} archivo(s) MOBI generados.")
        finally:
            self.btn_convert.config(state="normal")
            self.btn_cancel.config(state="disabled")
            self._set_status("Listo.")

    def _set_progress(self, bar: ttk.Progressbar, maximum: int | None = None, value: int | None = None):
        if maximum is not None:
            bar['maximum'] = maximum
        if value is not None:
            bar['value'] = value
        self.root.update_idletasks()

    def _set_status(self, text: str):
        self.status_label.config(text=text)
        self.root.update_idletasks()

    # ---------------- Localización de KCC / KindleGen ----------------
    def resolve_kcc_exe(self) -> Path | None:
        candidates = sorted(self.base_path.glob("KCC_c2e_*.exe"))
        if not candidates:
            return None
        return candidates[-1]

    def ensure_kindlegen_in_path(self) -> Path | None:
        local_kg = self.base_path / "kindlegen.exe"
        if local_kg.exists():
            self.log(f"kindlegen.exe encontrado localmente: {local_kg}")
            return local_kg

        previewer_root = Path(self.kp3_dir.get().strip('"'))
        if previewer_root.exists():
            self.log(f"Buscando kindlegen.exe dentro de: {previewer_root}")
            matches = list(previewer_root.rglob("kindlegen.exe"))
            if matches:
                kg = matches[0]
                kg_dir = str(kg.parent)
                os.environ["PATH"] = kg_dir + os.pathsep + os.environ.get("PATH", "")
                self.log(f"kindlegen.exe encontrado: {kg} (añadido al PATH)")
                return kg

        self.log("⚠ No se encontró kindlegen.exe. KCC podría fallar con 'KindleGen is missing!'")
        return None

    # ---------------- KCC (MOBI) ----------------
    def convert_folder_to_mobi(self, folder: Path, output_name: str, series_title: str, volume_index: int) -> bool:
        kcc_exe = self.resolve_kcc_exe()
        output_dir = self.base_path / 'ebooks'
        output_dir.mkdir(exist_ok=True)

        if not kcc_exe or not kcc_exe.exists():
            self.log("❌ No se encontró KCC_c2e_*.exe en la carpeta del programa.")
            return False

        self.ensure_kindlegen_in_path()

        imgs = list(folder.glob("*.jpg"))
        if not imgs:
            self.log(f"⚠ No hay imágenes JPG en {folder.name}; se omite conversión.")
            return False

        title = f"{series_title} - v{volume_index:02d}"
        author = self.author.get().strip()

        cmd = [
            str(kcc_exe),
            "--manga-style",
            "--profile", "KPW",
            "--stretch",
            "--upscale",
            "--format", "MOBI",
            "--title", title
        ]
        if author:
            cmd += ["--author", author]
        cmd += ["--output", str(output_dir), str(folder)]

        self.log("KCC cmd: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                cwd=str(self.base_path),
                env=os.environ.copy()
            )
            self.log(f"KCC stdout:\n{res.stdout.strip()}")
            if res.returncode != 0:
                self.log(f"KCC stderr:\n{res.stderr.strip()}")
                self.log(f"❌ KCC terminó con código {res.returncode}.")
                return False

            mobis = list(output_dir.glob("*.mobi"))
            if not mobis:
                self.log("❌ No se detectó archivo MOBI generado.")
                return False
            mobi_file = max(mobis, key=lambda p: p.stat().st_mtime)

            new_name = output_dir / f"{output_name}.mobi"
            if new_name.exists():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                new_name = output_dir / f"{output_name}_{ts}.mobi"
            mobi_file.rename(new_name)
            self.log(f"✅ MOBI: {new_name.name}")
            return True
        except Exception as e:
            self.log(f"❌ Excepción al ejecutar KCC: {e}")
            return False

    # ---------------- Utilidades ----------------
    def open_ebooks_folder(self):
        path = self.base_path / "ebooks"
        if sys.platform == "win32":
            os.startfile(path)
        else:
            os.system(f"xdg-open '{path}'")

    def run(self):
        self.root.mainloop()


def main():
    try:
        import cv2  # noqa
        from PIL import Image  # noqa
    except ImportError as e:
        print(f"Falta dependencia: {e}")
        return
    app = KindleMangaOptimizer()
    app.run()


if __name__ == "__main__":
    main()
