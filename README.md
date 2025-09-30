# 📚 Kindle Manga Optimizer v4.7

[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Pillow](https://img.shields.io/badge/Pillow-11.2-green)](https://pypi.org/project/Pillow/)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.10-orange)](https://pypi.org/project/opencv-python/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

Aplicación de escritorio en **Python + Tkinter** para optimizar mangas/webtoons escaneados y convertirlos a **MOBI** compatible con Kindle.  
Usa **KCC (Kindle Comic Converter)** y **Kindle Previewer 3** para generar archivos Kindle listos para leer 📖.

---

## ✨ Características principales
- 📂 Soporte para perfiles: **TMO** e **INMANGA** (auto-ordenación de capítulos e imágenes).
- 🔄 Reordenar capítulos, habilitar/deshabilitar desde la interfaz.
- 👁 Vista previa **original vs procesada**, con filtros aplicados en tiempo real:
  - Contraste y Nitidez ajustables.
  - Escala de grises y **Umbral adaptativo** (ideal para mangas antiguos).
  - Reducción de ruido (OpenCV bilateral).
- 📦 Agrupación de capítulos → volúmenes automáticos (`v01`, `v02`, …).
- 🏷 Nombres de salida: `Serie - vNN.mobi`.
- ⚙️ Conversión mediante **KCC_c2e** + **kindlegen** (Kindle Previewer 3).
- 🛑 Botón **Cancelar** y logs detallados en la UI.
- 🧹 Limpieza opcional de carpetas `temp/` y `ebooks/`.

---

## 📂 Estructura de entrada
Ejemplo (perfil **INMANGA**):

```
📁 OnePiece
 ├── 📁 Chapter 01
 │    ├── 001.jpg
 │    ├── 002.jpg
 │    └── ...
 ├── 📁 Chapter 02
 │    ├── 001.jpg
 │    └── ...
```

Ejemplo (perfil **TMO**):
```
📁 Berserk
 ├── 📁 Capítulo 001
 │    ├── 001.png
 │    ├── 002.png
 │    └── ...
 ├── 📁 Capítulo 002
 │    ├── 001.png
 │    └── ...
```

---

## ⚙️ Instalación

### 1️⃣ Requisitos
- [Python 3.13](https://www.python.org/downloads/)
- [Kindle Previewer 3](https://www.amazon.com/Kindle-Previewer/b?ie=UTF8&node=21381691011) (incluye `kindlegen.exe`)
- [Kindle Comic Converter (KCC)](https://github.com/ciromattia/kcc)  
  Descargar `KCC_c2e_9.1.0.exe` y colocarlo en la carpeta del proyecto.

### 2️⃣ Instalar dependencias
```bash
py -3.13 -m pip install -r requirements.txt
```

`requirements.txt` incluye:
- Pillow (procesamiento de imágenes)
- OpenCV (filtros y umbral)
- NumPy (arrays y operaciones rápidas)

---

## ▶️ Uso
Ejecutar la aplicación con:

```bash
py -3.13 main.py
```

Se abrirá la interfaz gráfica con pestañas para:

1. **Configuración** → Carpeta raíz, perfil, opciones de imagen, metadatos.  
2. **Vista previa** → Comparar imagen original vs procesada.  
3. **Plan de salida** → Previsualizar agrupación en volúmenes.  
4. **Procesar** → Iniciar conversión a **MOBI** con progreso en tiempo real.

---

## 📦 Crear ejecutable (.exe)

1. Instalar PyInstaller:
   ```bash
   py -3.13 -m pip install pyinstaller
   ```
2. Generar build:
   ```bash
   py -3.13 -m PyInstaller --noconfirm --onefile --windowed main.py
   ```
3. El ejecutable estará en:
   ```
   dist/KindleMangaOptimizer.exe
   ```
4. Copiar a `dist/` también:
   - `KCC_c2e_9.1.0.exe`
   - `kindlegen.exe` (desde Kindle Previewer 3)

---

## 🎨 Modos de mejora recomendados

- **Manga moderno (escaneo limpio):**  
  Usar solo ajuste de ancho + calidad JPG.  
- **Manga antiguo / baja calidad:**  
  Activar **Umbral adaptativo** + Escala de grises.  
- **Manga con manchas o ruido:**  
  Usar **Reducción de ruido (OpenCV bilateral)** + Contraste.  
- **Manga con páginas borrosas:**  
  Subir el nivel de **Nitidez**.  

> 🔬 También es posible integrar métodos de IA como **Real-ESRGAN** o **waifu2x** para super-resolución, aunque requieren GPU y más tiempo de procesamiento.

---

## 📷 Capturas de pantalla
*(agrega tus propias imágenes de la app en acción aquí)*

---

## 📝 Licencia
Este proyecto se distribuye bajo licencia [MIT](LICENSE).

---

## 👨‍💻 Autor
Desarrollado para optimizar mangas/webtoons y generar archivos Kindle de alta calidad, balanceando **tiempo de conversión vs calidad visual**.
