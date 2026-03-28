#!/usr/bin/env python3
"""
DTE Pista GUI — GrupoRVQ
Descarga masiva por rango de NO_UNICO + búsqueda por NRC / Nombre
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import subprocess
import json
import os
import glob
import unicodedata
import html
import re
import queue
import time
import io
import base64

try:
    import qrcode
    from PIL import Image, ImageTk
    QR_DISPONIBLE = True
except ImportError:
    QR_DISPONIBLE = False

# ─── Configuración ────────────────────────────────────────────────
API_HOST = "192.168.176.2"
API_PORT = 8096
API_BASE = f"http://{API_HOST}:{API_PORT}/api/digital/pistaDET"
CARPETA_DEFAULT = r"\\192.168.176.2\dte\pista"   # ajusta si es necesario
CARPETA_LOCAL   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dte")
CONCURRENCIA    = 6   # descargas paralelas (hilos)
# ──────────────────────────────────────────────────────────────────

COLORES = {
    "bg":      "#0f1117",
    "surf":    "#1a1d27",
    "card":    "#21253a",
    "border":  "#2d3150",
    "accent":  "#e63222",
    "green":   "#10b981",
    "yellow":  "#f59e0b",
    "text":    "#dde1ec",
    "muted":   "#5a6278",
    "white":   "#ffffff",
}
FONT_MONO  = ("Courier New", 9)
FONT_SANS  = ("Segoe UI", 9)
FONT_BOLD  = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 11, "bold")
FONT_BIG   = ("Segoe UI", 13, "bold")


# ══════════════════════════════════════════════════════════════════
#  LÓGICA DE DATOS
# ══════════════════════════════════════════════════════════════════

QR_HOST = "216.184.103.211:8080"

def generar_url_qr(d):
    """Construye la URL para el QR a partir de fecEmi y codigoGeneracion del JSON."""
    fec = d.get("fecha", "")
    cod = d.get("cod_gen", "")
    if not fec or not cod:
        return None
    return f"http://{QR_HOST}/RV_dte_api/clientepdf/?fecEmi={fec}&codGeneracion={cod}"


def generar_qr_imagen(url, size=120):
    """
    Genera un QR PNG en memoria a partir de la URL.
    Retorna un objeto PIL.Image o None si qrcode no está instalado.
    """
    if not QR_DISPONIBLE or not url:
        return None
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=3,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img = img.resize((size, size), Image.LANCZOS)
    return img


def generar_qr_base64(url, size=120):
    """Retorna el QR como cadena base64 PNG para embeber en HTML."""
    img = generar_qr_imagen(url, size)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")



def extraer_json_de_html(texto_html):
    """
    La API devuelve HTML con el JSON dentro de <pre>…</pre>
    con entidades HTML (&quot; etc.). Lo extraemos y parseamos.
    """
    # Buscar bloque <pre>
    m = re.search(r"<pre>(.*?)</pre>", texto_html, re.DOTALL | re.IGNORECASE)
    if not m:
        # Intentar extraer JSON directamente si no hay <pre>
        idx = texto_html.find("{")
        if idx != -1:
            try:
                return json.loads(texto_html[idx:])
            except Exception:
                pass
        return None

    contenido = m.group(1)
    # Decodificar entidades HTML (&quot; → ", &amp; → &, etc.)
    contenido = html.unescape(contenido)
    contenido = contenido.strip()

    try:
        return json.loads(contenido)
    except Exception:
        return None


def es_pista(raw):
    """
    Retorna True si el DTE es de PISTA (Caja 1).
    Criterios en orden de prioridad:
    1. apendice tiene campo CAJA con valor "1"
    2. apendice NO tiene PST-FACTURA=S  (que marca tienda)
    3. apendice tiene OBSERVACION=NSW   (señal de pista en estos DTEs)
    """
    apendice = raw.get("apendice") or []
    pst = False
    caja_val = None
    obs_val  = None

    for item in apendice:
        campo = str(item.get("campo", "") or item.get("etiqueta", "")).upper()
        valor = str(item.get("valor", "")).upper().strip()
        if "CAJA" in campo or "ID_CAJA" in campo:
            caja_val = valor
        if "PST" in campo or "PST-FACTURA" in campo:
            pst = True
        if "OBSERVACION" in campo:
            obs_val = valor

    # Si tiene campo CAJA explícito
    if caja_val is not None:
        return caja_val in ("1", "CAJA 1", "CAJA1")

    # PST-FACTURA=S → definitivamente tienda
    if pst:
        return False

    # OBSERVACION=NSW → pista
    if obs_val == "NSW":
        return True

    # Sin info suficiente: incluir (no excluir por defecto)
    return True


def parsear_dte(raw, no_unico_hint=""):
    ident  = raw.get("identificacion") or {}
    emisor = raw.get("emisor") or {}
    recep  = raw.get("receptor") or {}
    resum  = raw.get("resumen") or {}
    apend  = raw.get("apendice") or []
    resp   = raw.get("respuestaHacienda") or {}
    items  = raw.get("cuerpoDocumento") or []
    pagos  = resum.get("pagos") or []

    def get_apendice(*campos):
        for item in apend:
            k = str(item.get("campo", "") or item.get("etiqueta", "")).upper()
            if any(c.upper() in k for c in campos):
                return str(item.get("valor", ""))
        return ""

    no_unico = get_apendice("NO_UNICO") or str(no_unico_hint)
    caja     = get_apendice("CAJA", "ID_CAJA")
    empleado = get_apendice("EMP", "EMPLEADO")
    sucursal = get_apendice("SUCURSAL")

    return {
        "no_unico":   no_unico,
        "caja":       caja,
        "empleado":   empleado,
        "sucursal":   sucursal,
        "cod_gen":    ident.get("codigoGeneracion", ""),
        "no_control": ident.get("numeroControl", ""),
        "tipo_doc":   ident.get("tipoDte", ""),
        "fecha":      ident.get("fecEmi", ""),
        "hora":       ident.get("horEmi", ""),
        "sello":      resp.get("sello", "") or raw.get("selloRecibido", ""),
        "biz_nombre": emisor.get("nombre", ""),
        "biz_nrc":    emisor.get("nrc", ""),
        "biz_nit":    emisor.get("nit", ""),
        "biz_giro":   emisor.get("descActividad", ""),
        "biz_dir":    (emisor.get("direccion") or {}).get("complemento", ""),
        "cli_nombre": recep.get("nombre", ""),
        "cli_nrc":    recep.get("nrc", "") or "",
        "cli_nit":    recep.get("numDocumento", "") or "",
        "cli_dir":    (recep.get("direccion") or {}).get("complemento", "") if recep.get("direccion") else "",
        "cli_tel":    recep.get("telefono", "") or "",
        "cli_correo": recep.get("correo", "") or "",
        "total":      float(resum.get("totalPagar") or resum.get("montoTotalOperacion") or 0),
        "pagos":      pagos,
        "items":      items,
        "_pista":     es_pista(raw),
        "_raw":       raw,
    }


def fmt_fecha(f):
    if not f or "-" not in str(f):
        return str(f or "")
    try:
        y, m, d = str(f).split("-")
        return f"{d}/{m}/{y}"
    except Exception:
        return str(f)


def normalizar(t):
    if not t:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def descargar_uno(no_unico, carpeta_destino):
    """
    Descarga un DTE por NO_UNICO.
    Retorna (dict_dte, es_pista, error_str)
    """
    url  = f"{API_BASE}/{no_unico}"
    dest = os.path.join(carpeta_destino, f"{no_unico}.json")

    cmd = ["curl", "-s", "--connect-timeout", "8", "-m", "15", url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=20, encoding="utf-8", errors="replace")
        texto = res.stdout.strip()
    except FileNotFoundError:
        return None, False, "curl no encontrado"
    except subprocess.TimeoutExpired:
        return None, False, "timeout"

    if not texto:
        return None, False, "respuesta vacía"

    raw = extraer_json_de_html(texto)
    if raw is None:
        return None, False, f"sin JSON ({texto[:60]})"

    pista = es_pista(raw)
    d = parsear_dte(raw, no_unico_hint=str(no_unico))

    if pista:
        os.makedirs(carpeta_destino, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    return d, pista, None


def cargar_carpeta(carpeta):
    """Lee todos los .json de la carpeta y retorna lista de registros."""
    archivos = glob.glob(os.path.join(carpeta, "*.json"))
    if not archivos:
        archivos = glob.glob(os.path.join(CARPETA_LOCAL, "*.json"))
    registros = []
    for ruta in archivos:
        try:
            with open(ruta, encoding="utf-8-sig") as f:
                raw = json.load(f)
            nombre_archivo = os.path.splitext(os.path.basename(ruta))[0]
            d = parsear_dte(raw, no_unico_hint=nombre_archivo)
            d["_ruta"] = ruta
            registros.append(d)
        except Exception:
            pass
    return registros


def formatear_ticket(d):
    A = 35
    SEP = "*" * A
    EQ  = "=" * A
    DA  = "-" * A

    def center(t): return str(t).center(A)

    def wrap_lines(texto, ancho):
        palabras = str(texto or "").split()
        lineas, cur = [], ""
        for w in palabras:
            if len(cur) + len(w) + (1 if cur else 0) <= ancho:
                cur += (" " if cur else "") + w
            else:
                if cur: lineas.append(cur)
                cur = w
        if cur: lineas.append(cur)
        return lineas or [""]

    def kv(k, v):
        pre = k + ": "
        resto = A - len(pre)
        partes = wrap_lines(str(v or ""), max(resto, 10))
        out = pre + partes[0]
        for p in partes[1:]:
            out += "\n" + " " * len(pre) + p
        return out

    def lr(a, b):
        sp = A - len(str(a)) - len(str(b))
        return str(a) + chr(32) * max(1, sp) + str(b)

    # Detectar CCF (lleva datos cliente) vs FAC (consumidor final)
    tipo_dte = str(d.get("tipo_doc") or "").upper().strip()
    cli_nit  = str(d.get("cli_nit")  or "").strip()
    cli_nrc  = str(d.get("cli_nrc")  or "").strip()
    cli_nomb = str(d.get("cli_nombre") or "").strip()
    es_ccf   = (tipo_dte == "02") or bool(cli_nit) or bool(cli_nrc)
    if cli_nomb and "CONSUMIDOR" not in cli_nomb.upper():
        es_ccf = True
    tipo_map = {"01": "FAC", "02": "CCF", "03": "CCF", "04": "NR",
                "05": "NR",  "06": "GP",  "07": "CR",  "11": "FAC"}
    tipo_lbl = tipo_map.get(tipo_dte, tipo_dte) if tipo_dte else ""

    lineas = []

    # Cabecera DTE
    if d.get("cod_gen"):    lineas += ["CODIGO GENERACION:", d["cod_gen"]]
    if d.get("no_control"): lineas += ["NO CONTROL:", d["no_control"]]
    if d.get("sello"):      lineas += ["SELLO:", d["sello"]]
    lineas += [SEP]

    # Emisor
    lineas += [center("**            GrupoRVQ             **")]
    biz = d.get("biz_nombre") or "RAMIREZ VENTURA S.A. DE C.V."
    lineas += [center("** " + biz + " **")]
    suc = d.get("sucursal") or ""
    if suc:
        if not suc.upper().startswith("TEXACO"):
            suc = "TEXACO " + suc
        lineas += [center(suc)]
    if d.get("biz_dir"):
        for l in wrap_lines("DIR: KM38 CARRETERA A COMALAPA, SAN LUIS TALPA,LA PAZ", A):
            lineas += [l]
    if d.get("biz_nrc"): lineas += ["NRC: " + d["biz_nrc"]]
    if d.get("biz_nit"): lineas += ["NIT: " + d["biz_nit"]]
    if d.get("biz_giro"):
        for l in wrap_lines("GIRO: " + d["biz_giro"], A):
            lineas += [l]
    lineas += [SEP]

    # Datos del cliente - solo CCF o con NIT/NRC
    if es_ccf:
        lineas += [center("*** DATOS DEL CLIENTE ***"), SEP]
        if cli_nrc:             lineas += ["NRC: " + cli_nrc]
        if cli_nit:             lineas += ["NIT: " + cli_nit]
        if cli_nomb:            lineas += [kv("NOMBRE", cli_nomb)]
        if d.get("cli_dir"):
            for l in wrap_lines("DIR: " + d["cli_dir"], A):
                lineas += [l]
        if d.get("cli_tel"):    lineas += ["TEL: " + d["cli_tel"]]
        if d.get("cli_correo"): lineas += [kv("CORREO", d["cli_correo"])]
        lineas += [SEP]

    # Aviso fiscal
    lineas += [center("NO ES UN DOCUMENTO FISCAL"),
               center("PARA CONSULTAS ESCRIBE AL CORREO"),
               center("soporte.dte@gruporvq.com"),
               center("2522-8849"), SEP]

    # Fecha, empleado, tipo, caja, no_unico
    fh = fmt_fecha(d.get("fecha", ""))
    if d.get("hora"):     fh += " " + d["hora"]
    if d.get("empleado"): fh = lr(fh, "EMPLEADO: " + d["empleado"])
    lineas += [fh]
    tipo = tipo_lbl or tipo_dte
    caja = d.get("caja", "")
    nou  = d.get("no_unico", "")
    if tipo or caja:
        lineas += [lr("TIPO DOC: " + tipo if tipo else "",
                      "CAJA: " + caja     if caja else "")]
    if nou:
        lineas += [lr("#NO_UNICO: " + nou, "")]
    lineas += [DA]

   

# Total y pagos
    lineas += ["", "TOTAL: $" + format(d.get("total", 0), ".2f"), DA]
    lineas += ["## FORMAS DE PAGO ##", DA]
    
    cod_map = {"01": "EFE", "02": "TRJ", "03": "CHQ", "04": "TRF", "05": "CRD"}
    pagos = d.get("pagos") or []
    
    if pagos:
        for p in pagos:
            cod   = str(p.get("codigo", "01"))
            lbl   = cod_map.get(cod, cod.upper())
            monto = float(p.get("montoPago") or p.get("monto") or 0)
            # Alineado a la izquierda (sin lr)
            lineas += [f"{lbl}: $" + format(monto, ".2f")]
    else:
        # Alineado a la izquierda si no hay desglose
        lineas += ["EFE: $" + format(d.get("total", 0), ".2f"), DA]

    # Pie de página: Mantenemos el center() para estos campos
    lineas += [EQ, center("Documento DTE"), center("... V0.4.0.9 ..."), EQ, ""]

    return "\n".join(lineas)


def _qr_escpos_bytes(url, ancho_px=160):
    """
    Convierte una URL en imagen QR y la serializa como stream ESC/POS
    usando el comando 'GS v 0' (raster bit image).
    Retorna bytes listos para escribir a la impresora, o b'' si falla.
    """
    if not QR_DISPONIBLE or not url:
        return b''
    try:
        import math
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("1")

        # Redimensionar al ancho deseado manteniendo proporción
        img = img.resize((ancho_px, ancho_px), Image.LANCZOS).convert("1")
        w, h = img.size

        # ESC/POS GS v 0: modo normal (m=0)
        # xL, xH = bytes de ancho en bytes (ceil(w/8))
        # yL, yH = bytes de alto en líneas
        bytes_per_row = math.ceil(w / 8)
        xL = bytes_per_row & 0xFF
        xH = (bytes_per_row >> 8) & 0xFF
        yL = h & 0xFF
        yH = (h >> 8) & 0xFF

        header = bytes([0x1D, 0x76, 0x30, 0x00, xL, xH, yL, yH])

        raster = bytearray()
        pixels = img.load()
        for y in range(h):
            for bx in range(bytes_per_row):
                byte = 0
                for bit in range(8):
                    px = bx * 8 + bit
                    if px < w:
                        # PIL "1" mode: 0=negro, 255=blanco
                        if pixels[px, y] == 0:
                            byte |= (0x80 >> bit)
                raster.append(byte)

        return header + bytes(raster)
    except Exception:
        return b''


class DTEApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DTE Pista — GrupoRVQ")
        self.configure(bg=COLORES["bg"])
        self.geometry("1100x700")
        self.minsize(900, 580)

        self._registros = []      # caché de registros cargados
        self._descargando = False
        self._stop_flag = False
        self._log_q = queue.Queue()

        self._build_ui()
        self._poll_log()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        C = COLORES

        # Topbar
        topbar = tk.Frame(self, bg=C["surf"], height=50)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        logo = tk.Label(topbar, text="G", bg=C["accent"], fg="white",
                        font=("Segoe UI", 13, "bold"), width=3)
        logo.pack(side="left", padx=(12,8), pady=8)
        tk.Label(topbar, text="DTE Pista  ·  GrupoRVQ",
                 bg=C["surf"], fg="white", font=FONT_TITLE).pack(side="left")
        self._lbl_status = tk.Label(topbar, text="● Listo",
                                    bg=C["surf"], fg=C["green"], font=FONT_SANS)
        self._lbl_status.pack(side="right", padx=14)

        # Notebook (pestañas)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook",         background=C["bg"],   borderwidth=0)
        style.configure("TNotebook.Tab",     background=C["surf"], foreground=C["muted"],
                        font=FONT_BOLD, padding=[14, 6])
        style.map("TNotebook.Tab",
                  background=[("selected", C["card"])],
                  foreground=[("selected", C["white"])])
        style.configure("TProgressbar", troughcolor=C["border"],
                        background=C["accent"], thickness=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=0, pady=0)

        tab_dl  = tk.Frame(nb, bg=C["bg"])
        tab_bus = tk.Frame(nb, bg=C["bg"])
        nb.add(tab_dl,  text="⬇  Descargar")
        nb.add(tab_bus, text="🔍  Buscar")

        self._build_tab_descargar(tab_dl)
        self._build_tab_buscar(tab_bus)

    # ── TAB DESCARGAR ────────────────────────────────────────────

    def _build_tab_descargar(self, parent):
        C = COLORES

        # Panel de config (izquierda)
        left = tk.Frame(parent, bg=C["surf"], width=280)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        pad = dict(padx=16, pady=6)

        tk.Label(left, text="Descarga por rango", bg=C["surf"],
                 fg=C["white"], font=FONT_TITLE).pack(anchor="w", padx=16, pady=(18,4))
        tk.Label(left, text="Descarga todos los DTE de un rango\nde números únicos y filtra solo pista.",
                 bg=C["surf"], fg=C["muted"], font=FONT_SANS, justify="left").pack(anchor="w", **pad)

        ttk.Separator(left).pack(fill="x", padx=16, pady=8)

        tk.Label(left, text="Desde #NO_UNICO", bg=C["surf"],
                 fg=C["text"], font=FONT_SANS).pack(anchor="w", padx=16)
        self._desde = tk.Entry(left, bg=C["card"], fg=C["white"],
                               insertbackground=C["white"],
                               font=FONT_MONO, relief="flat", bd=6)
        self._desde.pack(fill="x", padx=16, pady=(2,8))
        self._desde.insert(0, "16999")

        tk.Label(left, text="Hasta #NO_UNICO", bg=C["surf"],
                 fg=C["text"], font=FONT_SANS).pack(anchor="w", padx=16)
        self._hasta = tk.Entry(left, bg=C["card"], fg=C["white"],
                               insertbackground=C["white"],
                               font=FONT_MONO, relief="flat", bd=6)
        self._hasta.pack(fill="x", padx=16, pady=(2,8))
        self._hasta.insert(0, "18000")

        tk.Label(left, text="Carpeta destino", bg=C["surf"],
                 fg=C["text"], font=FONT_SANS).pack(anchor="w", padx=16)
        self._carpeta_dl = tk.Entry(left, bg=C["card"], fg=C["white"],
                                    insertbackground=C["white"],
                                    font=FONT_MONO, relief="flat", bd=6)
        self._carpeta_dl.pack(fill="x", padx=16, pady=(2,8))
        self._carpeta_dl.insert(0, CARPETA_LOCAL)

        tk.Label(left, text="Descargas paralelas", bg=C["surf"],
                 fg=C["text"], font=FONT_SANS).pack(anchor="w", padx=16)
        self._conc = tk.Spinbox(left, from_=1, to=20, width=5,
                                bg=C["card"], fg=C["white"],
                                buttonbackground=C["card"],
                                font=FONT_MONO, relief="flat")
        self._conc.pack(anchor="w", padx=16, pady=(2,12))
        self._conc.delete(0, "end")
        self._conc.insert(0, str(CONCURRENCIA))

        self._btn_start = tk.Button(left, text="▶  Iniciar descarga",
                                    bg=C["accent"], fg="white",
                                    font=FONT_BOLD, relief="flat",
                                    cursor="hand2", command=self._iniciar_descarga)
        self._btn_start.pack(fill="x", padx=16, pady=4)

        self._btn_stop = tk.Button(left, text="⬛  Detener",
                                   bg=C["card"], fg=C["muted"],
                                   font=FONT_BOLD, relief="flat",
                                   cursor="hand2", command=self._detener,
                                   state="disabled")
        self._btn_stop.pack(fill="x", padx=16, pady=4)

        ttk.Separator(left).pack(fill="x", padx=16, pady=10)

        # Contadores
        self._lbl_ok    = tk.Label(left, text="✓ Pista guardados: 0",
                                   bg=C["surf"], fg=C["green"], font=FONT_SANS)
        self._lbl_ok.pack(anchor="w", padx=16)
        self._lbl_skip  = tk.Label(left, text="— Tienda omitidos: 0",
                                   bg=C["surf"], fg=C["yellow"], font=FONT_SANS)
        self._lbl_skip.pack(anchor="w", padx=16)
        self._lbl_err   = tk.Label(left, text="✗ Errores: 0",
                                   bg=C["surf"], fg=C["accent"], font=FONT_SANS)
        self._lbl_err.pack(anchor="w", padx=16)

        # Panel derecho: barra de progreso + log
        right = tk.Frame(parent, bg=C["bg"])
        right.pack(side="right", fill="both", expand=True)

        # Progreso
        prog_frame = tk.Frame(right, bg=C["bg"])
        prog_frame.pack(fill="x", padx=20, pady=(20, 8))

        self._lbl_prog = tk.Label(prog_frame, text="Esperando inicio…",
                                  bg=C["bg"], fg=C["muted"], font=FONT_SANS)
        self._lbl_prog.pack(anchor="w")

        self._pbar = ttk.Progressbar(prog_frame, mode="determinate",
                                     style="TProgressbar")
        self._pbar.pack(fill="x", pady=(4,0))

        # Log
        tk.Label(right, text="Log de descarga", bg=C["bg"],
                 fg=C["muted"], font=FONT_SANS).pack(anchor="w", padx=20)

        self._log = scrolledtext.ScrolledText(
            right, bg=C["card"], fg=C["text"],
            font=FONT_MONO, relief="flat", bd=0,
            state="disabled", wrap="word",
            insertbackground=C["white"]
        )
        self._log.pack(fill="both", expand=True, padx=20, pady=(4, 20))
        self._log.tag_config("ok",   foreground=C["green"])
        self._log.tag_config("err",  foreground=C["accent"])
        self._log.tag_config("warn", foreground=C["yellow"])
        self._log.tag_config("info", foreground=C["muted"])

    # ── TAB BUSCAR ───────────────────────────────────────────────

    def _build_tab_buscar(self, parent):
        C = COLORES

        # Barra de búsqueda
        top = tk.Frame(parent, bg=C["surf"])
        top.pack(fill="x")

        tk.Label(top, text="Carpeta JSON:", bg=C["surf"],
                 fg=C["text"], font=FONT_SANS).pack(side="left", padx=(16,4), pady=10)
        self._carpeta_bus = tk.Entry(top, bg=C["card"], fg=C["white"],
                                     insertbackground=C["white"],
                                     font=FONT_MONO, relief="flat", bd=4, width=35)
        self._carpeta_bus.pack(side="left", pady=10)
        self._carpeta_bus.insert(0, CARPETA_LOCAL)

        tk.Button(top, text="Cargar", bg=C["card"], fg=C["text"],
                  font=FONT_SANS, relief="flat", cursor="hand2",
                  command=self._cargar_carpeta_buscar).pack(side="left", padx=8, pady=10)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", pady=6)

        tk.Label(top, text="Buscar:", bg=C["surf"],
                 fg=C["text"], font=FONT_SANS).pack(side="left", padx=(12,4))
        self._q_entry = tk.Entry(top, bg=C["card"], fg=C["white"],
                                  insertbackground=C["white"],
                                  font=FONT_MONO, relief="flat", bd=4, width=28)
        self._q_entry.pack(side="left", pady=10)
        self._q_entry.bind("<Return>", lambda e: self._buscar())

        tk.Button(top, text="🔍 Buscar", bg=C["accent"], fg="white",
                  font=FONT_BOLD, relief="flat", cursor="hand2",
                  command=self._buscar).pack(side="left", padx=8)

        self._lbl_total = tk.Label(top, text="0 registros cargados",
                                   bg=C["surf"], fg=C["muted"], font=FONT_SANS)
        self._lbl_total.pack(side="right", padx=16)

        # Body: lista | ticket
        body = tk.Frame(parent, bg=C["bg"])
        body.pack(fill="both", expand=True)

        # Lista de resultados
        list_frame = tk.Frame(body, bg=C["surf"], width=340)
        list_frame.pack(side="left", fill="y")
        list_frame.pack_propagate(False)

        tk.Label(list_frame, text="Resultados", bg=C["surf"],
                 fg=C["muted"], font=FONT_SANS).pack(anchor="w", padx=12, pady=(8,2))

        self._listbox = tk.Listbox(
            list_frame, bg=C["card"], fg=C["text"],
            font=FONT_MONO, relief="flat", bd=0,
            selectbackground=C["accent"], selectforeground="white",
            activestyle="none", cursor="hand2"
        )
        self._listbox.pack(fill="both", expand=True, padx=6, pady=6)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        sb = tk.Scrollbar(list_frame, command=self._listbox.yview,
                          bg=C["border"], troughcolor=C["surf"])
        sb.pack(side="right", fill="y")
        self._listbox.config(yscrollcommand=sb.set)

        # Ticket viewer
        ticket_frame = tk.Frame(body, bg=C["bg"])
        ticket_frame.pack(side="right", fill="both", expand=True)

        # Header con botón imprimir
        ticket_header = tk.Frame(ticket_frame, bg=C["bg"])
        ticket_header.pack(fill="x", padx=16, pady=(8, 2))
        tk.Label(ticket_header, text="Vista de ticket",
                 bg=C["bg"], fg=C["muted"], font=FONT_SANS).pack(side="left")
        self._btn_print = tk.Button(
            ticket_header, text="\U0001f5a8  Imprimir",
            bg=C["green"], fg="white", font=FONT_BOLD,
            relief="flat", cursor="hand2", padx=12, pady=3,
            state="disabled", command=self._imprimir_ticket
        )
        self._btn_print.pack(side="right")

        # Área scrolleable para centrar el papel
        canvas_outer = tk.Frame(ticket_frame, bg=C["bg"])
        canvas_outer.pack(fill="both", expand=True, padx=12, pady=(0, 16))

        # Papel con sombra (borde gris) + margen interno
        ticket_paper = tk.Frame(canvas_outer, bg="#aaaaaa")
        ticket_paper.pack(anchor="n", pady=6)
        ticket_inner = tk.Frame(ticket_paper, bg="#ffffff", padx=16, pady=12)
        ticket_inner.pack(padx=2, pady=2)

        self._ticket_view = scrolledtext.ScrolledText(
            ticket_inner, bg="#ffffff", fg="#000000",
            font=("Consolas", 10, "normal"), relief="flat", bd=0,
            state="normal", wrap="none",
            width=44, height=36,
            highlightthickness=0,
            cursor="arrow",
        )
        # Bloquear edición sin desactivar el widget (disabled opaca el texto en Windows)
        self._ticket_view.bind("<Key>", lambda e: "break")
        self._ticket_view.bind("<Button-2>", lambda e: "break")
        self._ticket_view.bind("<Control-v>", lambda e: "break")
        self._ticket_view.pack()

        self._resultados = []   # lista de registros filtrados

    # ── LÓGICA DESCARGA ──────────────────────────────────────────

    def _iniciar_descarga(self):
        if self._descargando:
            return
        try:
            desde = int(self._desde.get().strip())
            hasta = int(self._hasta.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Los valores de rango deben ser números enteros.")
            return
        if desde > hasta:
            messagebox.showerror("Error", "El número inicial debe ser menor o igual al final.")
            return

        carpeta = self._carpeta_dl.get().strip() or CARPETA_LOCAL
        try:
            conc = max(1, min(20, int(self._conc.get())))
        except ValueError:
            conc = CONCURRENCIA

        self._descargando = True
        self._stop_flag   = False
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._log_append(f"Iniciando descarga #{desde} → #{hasta}  ({hasta-desde+1} registros)\n", "info")
        self._log_append(f"Carpeta: {carpeta}  |  Concurrencia: {conc}\n", "info")
        self._pbar["value"] = 0
        self._pbar["maximum"] = hasta - desde + 1

        self._cnt_ok   = 0
        self._cnt_skip = 0
        self._cnt_err  = 0

        t = threading.Thread(target=self._worker_descarga,
                             args=(desde, hasta, carpeta, conc), daemon=True)
        t.start()

    def _worker_descarga(self, desde, hasta, carpeta, conc):
        total   = hasta - desde + 1
        numeros = list(range(desde, hasta + 1))
        done    = 0

        import concurrent.futures

        def tarea(num):
            if self._stop_flag:
                return num, None, False, "detenido"
            return num, *descargar_uno(num, carpeta)

        with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
            futuros = {ex.submit(tarea, n): n for n in numeros}
            for fut in concurrent.futures.as_completed(futuros):
                if self._stop_flag:
                    break
                try:
                    num, d, pista, err = fut.result()
                except Exception as e:
                    num = futuros[fut]
                    d, pista, err = None, False, str(e)

                done += 1

                if err:
                    self._cnt_err += 1
                    if self._cnt_err <= 10:
                        self._log_q.put((f"✗ #{num}: {err}\n", "err"))
                elif pista:
                    self._cnt_ok += 1
                    nombre = (d.get("cli_nombre") or "CONSUMIDOR FINAL")[:28]
                    self._log_q.put((f"✓ #{num}  {nombre}  ${d.get('total',0):.2f}\n", "ok"))
                else:
                    self._cnt_skip += 1
                    self._log_q.put((f"— #{num}  (tienda, omitido)\n", "warn"))

                # Actualizar UI
                pct = int(done / total * 100)
                self._log_q.put(("__PROG__", done, total, pct))

        self._log_q.put(("__DONE__",))

    def _detener(self):
        self._stop_flag = True
        self._log_append("⬛ Deteniendo…\n", "warn")

    def _poll_log(self):
        """Procesa la cola de mensajes del hilo de descarga."""
        try:
            while True:
                msg = self._log_q.get_nowait()
                if msg[0] == "__DONE__":
                    self._descargando = False
                    self._btn_start.config(state="normal")
                    self._btn_stop.config(state="disabled")
                    self._lbl_status.config(text="● Listo", fg=COLORES["green"])
                    self._log_append(
                        f"\n=== FIN ===  ✓ {self._cnt_ok} pista  "
                        f"— {self._cnt_skip} tienda  ✗ {self._cnt_err} errores\n", "ok")
                elif msg[0] == "__PROG__":
                    _, done, total, pct = msg
                    self._pbar["value"] = done
                    self._lbl_prog.config(
                        text=f"{done}/{total} ({pct}%)  "
                             f"✓{self._cnt_ok}  —{self._cnt_skip}  ✗{self._cnt_err}")
                    self._lbl_ok.config(text=f"✓ Pista guardados: {self._cnt_ok}")
                    self._lbl_skip.config(text=f"— Tienda omitidos: {self._cnt_skip}")
                    self._lbl_err.config(text=f"✗ Errores: {self._cnt_err}")
                else:
                    texto, tag = msg
                    self._log_append(texto, tag)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _log_append(self, texto, tag=""):
        self._log.config(state="normal")
        self._log.insert("end", texto, tag)
        self._log.see("end")
        self._log.config(state="disabled")

    # ── LÓGICA BÚSQUEDA ─────────────────────────────────────────

    def _cargar_carpeta_buscar(self):
        carpeta = self._carpeta_bus.get().strip() or CARPETA_LOCAL
        self._lbl_total.config(text="Cargando…", fg=COLORES["yellow"])
        self.update_idletasks()

        def worker():
            registros = cargar_carpeta(carpeta)
            self._registros = registros
            self.after(0, lambda: self._lbl_total.config(
                text=f"{len(registros)} registros cargados",
                fg=COLORES["green"]))
            self.after(0, lambda: self._buscar())

        threading.Thread(target=worker, daemon=True).start()

    def _buscar(self):
        q = normalizar(self._q_entry.get().strip())
        registros = self._registros

        if not registros:
            self._cargar_carpeta_buscar()
            return

        if not q:
            self._resultados = registros
        else:
            self._resultados = [
                d for d in registros
                if any(q in normalizar(d.get(c, ""))
                       for c in ["cli_nombre","cli_nrc","cli_nit",
                                  "no_unico","no_control","cod_gen"])
            ]

        self._listbox.delete(0, "end")
        for d in self._resultados:
            fecha = fmt_fecha(d.get("fecha",""))
            nombre = (d.get("cli_nombre") or "CONSUMIDOR FINAL")[:22]
            nou    = d.get("no_unico","")
            total  = f"${d.get('total',0):.2f}"
            self._listbox.insert("end",
                f"#{nou:<7}  {fecha}  {total:<8}  {nombre}")

        n = len(self._resultados)
        self._lbl_total.config(
            text=f"{n} resultado{'s' if n!=1 else ''}",
            fg=COLORES["green"] if n else COLORES["accent"])

    def _on_select(self, event):
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._resultados):
            return
        self._registro_activo = self._resultados[idx]
        d = self._registro_activo
        ticket = formatear_ticket(d)

        self._ticket_view.config(state="normal")
        self._ticket_view.delete("1.0", "end")

        self._ticket_view.insert("end", ticket)

        # ── QR al final del ticket ──────────────────────────────
        if QR_DISPONIBLE:
            url_qr = generar_url_qr(d)
            if url_qr:
                img_pil = generar_qr_imagen(url_qr, size=110)
                if img_pil:
                    tk_img = ImageTk.PhotoImage(img_pil)
                    self._qr_img = tk_img  # evitar garbage collection

                    # Contenedor centrado que se incrusta como ventana
                    qr_frame = tk.Frame(self._ticket_view, bg="#ffffff")
                    tk.Label(qr_frame, image=tk_img, bg="#ffffff").pack()
                    tk.Label(qr_frame,
                             text="Escanea para descargar tu DTE",
                             bg="#ffffff", fg="#333333",
                             font=("Consolas", 8)).pack()

                    # Insertar frame como ventana embebida
                    self._ticket_view.insert("end", "\n")
                    self._ticket_view.window_create(
                        "end",
                        window=qr_frame,
                        padx=0,
                        pady=4,
                    )
                    self._ticket_view.insert("end", "\n")

                    # Recentrar una vez que el widget tenga tamaño real
                    def _centrar_qr(fr=qr_frame, tv=self._ticket_view):
                        w = tv.winfo_width()
                        pad = max(0, (w - 114) // 2)
                        try:
                            tv.window_configure(fr, padx=pad)
                        except Exception:
                            pass
                    self.after(50, _centrar_qr)
                    # También recentrar si cambia el tamaño de ventana
                    def _recentrar(event, fr=qr_frame, tv=self._ticket_view):
                        w = tv.winfo_width()
                        pad = max(0, (w - 114) // 2)
                        try:
                            tv.window_configure(fr, padx=pad)
                        except Exception:
                            pass
                    self._ticket_view.bind("<Configure>", _recentrar, add="+")

        self._btn_print.config(state="normal")

    def _imprimir_ticket(self):
        """Imprime directo con win32print (ESC/POS). Fallback: navegador."""
        if not hasattr(self, "_registro_activo") or not self._registro_activo:
            return

        d = self._registro_activo

        # ── Intentar impresión directa ESC/POS ──────────────────
        try:
            import win32print
            import win32ui
            self._imprimir_escpos(d)
            return
        except ImportError:
            pass  # pywin32 no instalado → usar navegador
        except Exception as e:
            resp = messagebox.askyesno(
                "Error de impresión",
                f"No se pudo imprimir directamente:\n{e}\n\n"
                "¿Abrir en navegador como respaldo?"
            )
            if not resp:
                return

        # ── Fallback: navegador ──────────────────────────────────
        self._imprimir_navegador(d)

    # ── ESC/POS directo ─────────────────────────────────────────────
    def _imprimir_escpos(self, d):
        """
        Genera el stream ESC/POS y lo manda directo a la impresora
        predeterminada (o la seleccionada). Funciona con Epson TM-T88V
        y cualquier impresora térmica ESC/POS.
        """
        import win32print

        # ── Comandos ESC/POS ──────────────────────────────────────
        ESC  = b'\x1b'
        GS   = b'\x1d'
        INIT        = ESC + b'@'           # Inicializar impresora
        CUT_PARTIAL = GS  + b'V\x42\x00'  # Corte parcial
        LF          = b'\n'

        # Alineación
        ALIGN_LEFT   = ESC + b'a\x00'
        ALIGN_CENTER = ESC + b'a\x01'
        ALIGN_RIGHT  = ESC + b'a\x02'

        # Negrita
        BOLD_ON  = ESC + b'E\x01'
        BOLD_OFF = ESC + b'E\x00'

        # Tamaño de texto (normal / doble alto)
        SIZE_NORMAL = GS + b'!\x00'          # normal
        SIZE_2H     = GS + b'!\x01'          # doble alto
        SIZE_2W2H   = GS + b'!\x11'          # doble ancho + doble alto

        # Codepage: CP850 / Latin para caracteres especiales
        CODEPAGE_CP850 = ESC + b't\x02'

        def txt(s):
            """Convierte string Python → bytes CP850, reemplazando lo inmanejable."""
            return s.encode("cp850", errors="replace")

        def linea(s=""):
            return txt(s) + LF

        def centro(s, ancho=35):
            return linea(str(s).center(ancho))

        def sep(c="*", ancho=35):
            return linea(c * ancho)

        A = 35  # ancho de columnas (igual que formatear_ticket)

        def lr(a, b, ancho=A):
            sp = ancho - len(str(a)) - len(str(b))
            return linea(str(a) + " " * max(1, sp) + str(b))

        # ── Construir stream ──────────────────────────────────────
        buf = bytearray()
        buf += INIT
        buf += CODEPAGE_CP850

        tipo_dte = str(d.get("tipo_doc") or "").upper().strip()
        cli_nit  = str(d.get("cli_nit")  or "").strip()
        cli_nrc  = str(d.get("cli_nrc")  or "").strip()
        cli_nomb = str(d.get("cli_nombre") or "").strip()
        es_ccf   = (tipo_dte == "02") or bool(cli_nit) or bool(cli_nrc)
        if cli_nomb and "CONSUMIDOR" not in cli_nomb.upper():
            es_ccf = True
        tipo_map = {"01": "FAC", "02": "CCF", "03": "CCF", "04": "NR",
                    "05": "NR",  "06": "GP",  "07": "CR",  "11": "FAC"}
        tipo_lbl = tipo_map.get(tipo_dte, tipo_dte) if tipo_dte else ""

        def wrap_lines(texto, ancho):
            palabras = str(texto or "").split()
            lineas, cur = [], ""
            for w in palabras:
                if len(cur) + len(w) + (1 if cur else 0) <= ancho:
                    cur += (" " if cur else "") + w
                else:
                    if cur: lineas.append(cur)
                    cur = w
            if cur: lineas.append(cur)
            return lineas or [""]

        # Cabecera DTE (pequeño, alineado izq)
        buf += SIZE_NORMAL + ALIGN_LEFT
        if d.get("cod_gen"):
            buf += linea("CODIGO GENERACION:")
            buf += linea(d["cod_gen"])
        if d.get("no_control"):
            buf += linea("NO CONTROL:")
            buf += linea(d["no_control"])
        if d.get("sello"):
            buf += linea("SELLO:")
            buf += linea(d["sello"])
        buf += sep("*")

        # GrupoRVQ — doble ancho+alto, centrado, negrita
        buf += ALIGN_CENTER + SIZE_2W2H + BOLD_ON
        buf += linea("GrupoRVQ")
        buf += SIZE_NORMAL + BOLD_OFF

        # Empresa
        biz = d.get("biz_nombre") or "RAMIREZ VENTURA S.A. DE C.V."
        buf += BOLD_ON + centro(biz) + BOLD_OFF

        suc = d.get("sucursal") or ""
        if suc:
            if not suc.upper().startswith("TEXACO"):
                suc = "TEXACO " + suc
            buf += BOLD_ON + centro(suc) + BOLD_OFF

        buf += ALIGN_LEFT + SIZE_NORMAL
        for l in wrap_lines("DIR: KM38 CARRETERA A COMALAPA, SAN LUIS TALPA, LA PAZ", A):
            buf += linea(l)
        if d.get("biz_nrc"): buf += linea("NRC: " + d["biz_nrc"])
        if d.get("biz_nit"): buf += linea("NIT: " + d["biz_nit"])
        if d.get("biz_giro"):
            for l in wrap_lines("GIRO: " + d["biz_giro"], A):
                buf += linea(l)
        buf += sep("*")

        # Datos cliente
        if es_ccf:
            buf += ALIGN_CENTER + BOLD_ON
            buf += linea("*** DATOS DEL CLIENTE ***")
            buf += BOLD_OFF + ALIGN_LEFT
            buf += sep("*")
            if cli_nrc:             buf += linea("NRC: " + cli_nrc)
            if cli_nit:             buf += linea("NIT: " + cli_nit)
            if cli_nomb:
                for l in wrap_lines("NOMBRE: " + cli_nomb, A):
                    buf += linea(l)
            if d.get("cli_dir"):
                for l in wrap_lines("DIR: " + d["cli_dir"], A):
                    buf += linea(l)
            if d.get("cli_tel"):    buf += linea("TEL: " + d["cli_tel"])
            if d.get("cli_correo"):
                for l in wrap_lines("CORREO: " + d["cli_correo"], A):
                    buf += linea(l)
            buf += sep("*")

        # Aviso fiscal
        buf += ALIGN_CENTER
        buf += linea("NO ES UN DOCUMENTO FISCAL")
        buf += linea("PARA CONSULTAS ESCRIBE AL CORREO")
        buf += linea("soporte.dte@gruporvq.com")
        buf += linea("2522-8849")
        buf += sep("*")

        # Fecha / empleado / tipo
        buf += ALIGN_LEFT
        fh = fmt_fecha(d.get("fecha", ""))
        if d.get("hora"):     fh += " " + d["hora"]
        if d.get("empleado"):
            buf += lr(fh, "EMPLEADO: " + d["empleado"])
        else:
            buf += linea(fh)
        tipo = tipo_lbl or tipo_dte
        caja = d.get("caja", "")
        nou  = d.get("no_unico", "")
        if tipo or caja:
            buf += lr("TIPO DOC: " + tipo if tipo else "",
                      "CAJA: " + caja     if caja else "")
        if nou:
            buf += linea("#NO_UNICO: " + nou)
        buf += sep("-")

        # TOTAL — doble ancho+alto, negrita
        buf += ALIGN_LEFT + SIZE_2W2H + BOLD_ON
        total_str = "TOTAL: $" + format(d.get("total", 0), ".2f")
        buf += linea(total_str)
        buf += SIZE_NORMAL + BOLD_OFF
        buf += sep("-")

        # Formas de pago
        buf += ALIGN_CENTER + BOLD_ON
        buf += linea("## FORMAS DE PAGO ##")
        buf += BOLD_OFF + ALIGN_LEFT
        buf += sep("-")
        cod_map = {"01": "EFE", "02": "TRJ", "03": "CHQ", "04": "TRF", "05": "CRD"}
        pagos = d.get("pagos") or []
        if pagos:
            for p in pagos:
                cod   = str(p.get("codigo", "01"))
                lbl   = cod_map.get(cod, cod.upper())
                monto = float(p.get("montoPago") or p.get("monto") or 0)
                buf += BOLD_ON + linea(f"{lbl}: $" + format(monto, ".2f")) + BOLD_OFF
        else:
            buf += BOLD_ON
            buf += linea("EFE: $" + format(d.get("total", 0), ".2f"))
            buf += BOLD_OFF

        # Pie
        buf += sep("=")
        buf += ALIGN_CENTER
        buf += linea("Documento DTE")
        buf += linea("... V0.4.0.9 ...")
        buf += sep("=")

        # ── QR como imagen ESC/POS GS v 0 ────────────────────────
        url_qr = generar_url_qr(d)
        if url_qr and QR_DISPONIBLE:
            qr_bytes = _qr_escpos_bytes(url_qr)
            if qr_bytes:
                buf += ALIGN_CENTER
                buf += LF
                buf += qr_bytes
                buf += LF
                buf += linea("Escanea para descargar tu DTE")

        # Avance y corte
        buf += b'\n' * 4
        buf += CUT_PARTIAL

        # ── Enviar a impresora ────────────────────────────────────
        printer_name = win32print.GetDefaultPrinter()
        hprinter = win32print.OpenPrinter(printer_name)
        try:
            hjob = win32print.StartDocPrinter(hprinter, 1,
                                              ("Ticket DTE", None, "RAW"))
            try:
                win32print.StartPagePrinter(hprinter)
                win32print.WritePrinter(hprinter, bytes(buf))
                win32print.EndPagePrinter(hprinter)
            finally:
                win32print.EndDocPrinter(hprinter)
        finally:
            win32print.ClosePrinter(hprinter)

    # ── Fallback navegador ───────────────────────────────────────────
    def _imprimir_navegador(self, d):
        """Genera HTML temporal y lo abre en el navegador."""
        ticket_txt = formatear_ticket(d)
        import html as htmlmod

        qr_html = ""
        url_qr = generar_url_qr(d)
        if url_qr:
            b64 = generar_qr_base64(url_qr, size=120)
            if b64:
                qr_html = f"""
<div style="text-align:center; margin-top:6px;">
  <img src="data:image/png;base64,{b64}"
       style="width:28mm; height:28mm; display:block; margin:0 auto;" />
  <span style="font-size:7pt; color:#333;">Escanea para descargar tu DTE</span>
</div>"""

        html_content = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  @page {{ size: 80mm auto; margin: 4mm 3mm; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Courier New',monospace; font-size:9pt;
          line-height:1.45; width:72mm; color:#000; background:#fff; }}
  pre {{ white-space:pre-wrap; word-break:break-word;
         font-family:inherit; font-size:inherit; }}
</style>
<script>window.onload=function(){{window.print();}};</script>
</head><body><pre>{ticket_txt}</pre>{qr_html}</body></html>"""

        import tempfile, sys
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html_content)
            tmp_path = f.name

        if sys.platform == "win32":
            os.startfile(tmp_path)
        else:
            subprocess.Popen(["xdg-open", tmp_path])


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = DTEApp()
    app.mainloop()
