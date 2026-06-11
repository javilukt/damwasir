#!/usr/bin/env python3
"""
Extractor RA+CE + metadatos de los RDs de FP Informática:
  · RD 405/2023  →  DAM + DAW
  · RD 1629/2009 →  ASIR  (módulo 0373: RA, CE y contenidos del RD 405/2023)

Extrae por módulo: código, nombre, ECTS, horas, RAs+CEs,
contenidos básicos (jerarquizados) y orientaciones pedagógicas (por subsecciones).

Sin dependencias externas — solo stdlib.
Genera: ra_ce.json  y  visualizador.html

Uso:  python3 extraer_ra_ce.py
"""

import json
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

DIR      = Path(__file__).parent
XML_405  = DIR / "documentos maestos" / "RD 405_2023.xml"
XML_1629 = DIR / "documentos maestos" / "RD 1629_2009.xml"
JSON_OUT = DIR / "ra_ce.json"
HTML_OUT = DIR / "visualizador.html"

BULLET_CHARS = '−–—•‐-'   # −–—•‐-


# ─── HTML → lista de párrafos ─────────────────────────────────────────────────

class ParagraphCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[tuple[str, str]] = []
        self._in = False;  self._cls = "";  self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._in = True;  self._buf = []
            self._cls = dict(attrs).get("class", "")

    def handle_endtag(self, tag):
        if tag == "p" and self._in:
            self._in = False
            text = unicodedata.normalize("NFC", " ".join("".join(self._buf).split()))
            if text:
                self.paragraphs.append((self._cls, text))

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)


def get_paragraphs(xml_path: Path) -> list[tuple[str, str]]:
    raw     = xml_path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"<texto>(.*?)</texto>", raw, re.DOTALL))
    if not matches:
        sys.exit(f"No se encontró <texto> en {xml_path.name}")
    frag = max(matches, key=lambda x: len(x.group(1))).group(1)
    col  = ParagraphCollector()
    col.feed(frag)
    return col.paragraphs


# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_bullet(text: str) -> bool:
    return bool(text) and text[0] in BULLET_CHARS

def strip_bullet(text: str) -> str:
    return re.sub(r'^[^\w¿¡(]+', '', text).strip()


def _flush_bloque(bloque, target):
    if bloque is not None:
        target.append(bloque)

def _flush_ped(ped, target):
    if ped is not None:
        target.append(ped)


# ─── Máquina de estados para un módulo ───────────────────────────────────────
# Recibe la lista COMPLETA de párrafos desde el inicio del módulo hacia adelante.
# Termina cuando detecta el inicio del siguiente módulo o un nuevo artículo.
# Devuelve el dict del módulo.

def parse_module(ps_full: list, start: int, asir_mode: bool = False) -> tuple[dict, int]:
    """
    Parsea un módulo comenzando en ps_full[start].
    Devuelve (módulo_dict, índice_primer_párrafo_del_siguiente_módulo).
    """
    mod: dict = {
        "codigo": "", "nombre": "", "ects": None, "horas": None,
        "RAs": [], "contenidos": [], "orientaciones": [],
    }

    state   = "header"   # header → ra → contenidos → orientaciones
    cur_ra  = None
    in_ce   = False
    cur_blq = None       # {"bloque": str, "items": [str]}
    cur_ped = None       # {"texto": str, "bullets": [str]}

    i = start
    while i < len(ps_full):
        css, text = ps_full[i]

        # ── Detectar fin de módulo (siguiente módulo o artículo)
        if i > start:
            if not asir_mode and re.match(r"El módulo profesional \d+\.", text):
                break
            if asir_mode and re.match(r"Módulo [Pp]rofesional: ", text):
                break
            if "articulo" in css and ("segundo" in text.lower() or "tercero" in text.lower()):
                break
            if "disposición" in text.lower() and "articulo" in css:
                break

        # ── HEADER: extrae ects y código antes de la sección RA
        if state == "header":
            m_ects = re.match(r"Equivalencia en créditos ECTS[:\s]+(\d+)", text)
            if m_ects:
                mod["ects"] = int(m_ects.group(1))
            m_cod = re.match(r"Código[:\s]+(\d+)", text)
            if m_cod and not mod["codigo"]:
                mod["codigo"] = m_cod.group(1)
            if "resultados de aprendizaje" in text.lower():
                state = "ra"
            i += 1
            continue

        # ── Transiciones de estado
        m_dur = re.match(r"Duración[:\s]+(\d+)\s*horas", text)
        if m_dur:
            mod["horas"] = int(m_dur.group(1))
            if cur_ra:
                mod["RAs"].append(cur_ra);  cur_ra = None
            _flush_bloque(cur_blq, mod["contenidos"]); cur_blq = None
            state = "contenidos";  i += 1;  continue

        if "orientaciones pedagógicas" in text.lower():
            _flush_bloque(cur_blq, mod["contenidos"]); cur_blq = None
            _flush_ped(cur_ped, mod["orientaciones"]);  cur_ped = None
            state = "orientaciones";  i += 1;  continue

        if re.match(r"Contenidos( básicos)?[.:]?$", text, re.I):
            i += 1;  continue   # saltar la cabecera "Contenidos básicos:"

        # ── Estado RA+CE
        if state == "ra":
            mr = re.match(r"^(\d+)\.\s+([A-ZÁÉÍÓÚÑ].+)$", text)
            if mr:
                if cur_ra:
                    mod["RAs"].append(cur_ra)
                cur_ra = {"numero": int(mr.group(1)),
                          "enunciado": mr.group(2).strip(), "CEs": []}
                in_ce = False
                i += 1;  continue

            if text == "Criterios de evaluación:":
                in_ce = True;  i += 1;  continue

            if in_ce and cur_ra:
                mc = re.match(r"^([a-z])\)\s+(.+)$", text)
                if mc:
                    cur_ra["CEs"].append({"letra": mc.group(1),
                                          "texto": mc.group(2).strip()})

        # ── Estado CONTENIDOS
        elif state == "contenidos":
            if is_bullet(text):
                if cur_blq is None:
                    cur_blq = {"bloque": "", "items": []}
                cur_blq["items"].append(strip_bullet(text))
            else:
                _flush_bloque(cur_blq, mod["contenidos"])
                cur_blq = {"bloque": text.rstrip(":. "), "items": []}

        # ── Estado ORIENTACIONES
        elif state == "orientaciones":
            if is_bullet(text):
                if cur_ped is None:
                    cur_ped = {"texto": "", "bullets": []}
                cur_ped["bullets"].append(strip_bullet(text))
            else:
                _flush_ped(cur_ped, mod["orientaciones"])
                cur_ped = {"texto": text, "bullets": []}

        i += 1

    # Volcar acumuladores pendientes
    if cur_ra:
        mod["RAs"].append(cur_ra)
    _flush_bloque(cur_blq, mod["contenidos"])
    _flush_ped(cur_ped,    mod["orientaciones"])

    return mod, i


# ─── Parser secciones título (Art.5/6/8/9) ───────────────────────────────────

def _parse_item(text: str) -> tuple[str, str]:
    """Extrae (id, texto) de una línea tipo 'a) texto' o '1. texto'.
    Limpia comillas angulares de cierre que deja el XML."""
    m = re.match(r'^([a-záéíóúñ])\)\s+(.+)$', text)
    if m: return m.group(1), m.group(2).rstrip('»').strip()
    m = re.match(r'^(\d+)\.\s+(.+)$', text)
    if m: return m.group(1), m.group(2).rstrip('»').strip()
    return "", ""

_ORDINAL_ES = re.compile(
    r'^(Dos|Tres|Cuatro|Cinco|Seis|Siete|Ocho|Nueve|Diez|Once|Doce)\.\s', re.I)

def parse_title_data(ps: list, art5_idx: int) -> dict:
    """
    Extrae las secciones de perfil del título a partir del índice de Art.5.
    Devuelve dict con: competencias, cualificaciones, prospectiva, objetivos_generales.
    """
    data: dict = {
        "competencias":      [],
        "cualificaciones":   {"completas": [], "incompletas": []},
        "prospectiva":       [],
        "objetivos_generales": [],
    }
    state     = None
    cual_mode = None
    cur_cual  = None

    def flush_cual():
        nonlocal cur_cual
        if cur_cual and cual_mode:
            data["cualificaciones"][cual_mode].append(cur_cual)
        cur_cual = None

    i = art5_idx
    while i < len(ps):
        css, text = ps[i]

        # Parar: llegamos a los módulos o a otra sección del documento
        if "anexo_num" in css and "ANEXO" in text:           break
        if re.match(r"El módulo profesional \d+\.", text):    break
        if re.match(r"Módulo [Pp]rofesional:", text):         break
        if css == "articulo" and re.search(
                r"(segundo|tercero|cuarto|disposici)", text, re.I): break
        # No paramos en capitulo_tit/num: en ASIR aparecen entre Art.8 y Art.9

        # Artículo → cambio de estado
        if css == "articulo":
            flush_cual()
            tl = text.lower()
            # Orden de prioridad: cualificaci antes que competenci (Art.6 contiene ambas)
            if "cualificaci" in tl:             state = "cualificaciones"; cual_mode = None
            elif "prospectiva" in tl:           state = "prospectiva"
            elif "objetivos generales" in tl:   state = "objetivos_generales"
            elif "competenci" in tl:            state = "competencias"
            else:                               state = None
            i += 1; continue

        # Líneas de transición "Tres. El artículo X queda redactado…"
        if _ORDINAL_ES.match(text):
            i += 1; continue

        # Introductorias sin contenido útil
        if "son las que se relacionan" in text or \
           "tendrán en cuenta" in text.lower() or \
           "son los siguientes" in text.lower():
            i += 1; continue

        # ── Competencias / Prospectiva / Objetivos: lista de ítems
        if state in ("competencias", "prospectiva", "objetivos_generales"):
            id_, txt = _parse_item(text)
            if id_:
                data[state].append({"id": id_, "texto": txt})

        # ── Cualificaciones
        elif state == "cualificaciones":
            if re.search(r"1\.\s+Cualificaci.{0,30}completa", text, re.I):
                cual_mode = "completas"
            elif re.search(r"2\.\s+Cualificaci.{0,30}incompleta", text, re.I):
                flush_cual(); cual_mode = "incompletas"
            elif text.startswith("UC") or re.match(r"UC\d", text):
                if cur_cual is None:
                    cur_cual = {"id": "", "nombre": "", "ucs": []}
                cur_cual["ucs"].append(text)
            elif cual_mode:
                id_, nombre = _parse_item(text)
                if id_:
                    flush_cual()
                    cur_cual = {"id": id_, "nombre": nombre, "ucs": []}
                elif nombre == "" and text and not re.match(r"\d+\.", text) \
                        and "cualificaci" not in text.lower() \
                        and "catálogo" not in text.lower():
                    # incompleta sin letra (ej. ASIR)
                    flush_cual()
                    cur_cual = {"id": "", "nombre": text, "ucs": []}

        i += 1

    flush_cual()
    return data


# ─── Parser RD 405/2023  (DAM + DAW) ─────────────────────────────────────────

def parse_405(xml_path: Path) -> dict:
    ps = get_paragraphs(xml_path)
    result: dict = {}

    # Localizar índices Art.5 por título (DAM=segundo, DAW=tercero)
    art5_by_titulo: dict[str, int] = {}
    cur_titulo = None
    for i, (css, text) in enumerate(ps):
        if css == "articulo":
            tl = text.lower()
            if "segundo" in tl:   cur_titulo = "DAM"
            elif "tercero" in tl: cur_titulo = "DAW"
            # Buscar específicamente Artículo 5 (no Art.6 que también contiene "competencia")
            if cur_titulo and re.search(r'artículo 5\.', tl):
                art5_by_titulo[cur_titulo] = i

    # Módulos
    modules_info = []
    cur_titulo = None
    for i, (css, text) in enumerate(ps):
        if css == "articulo":
            if "segundo" in text.lower():   cur_titulo = "DAM"
            elif "tercero" in text.lower(): cur_titulo = "DAW"
        mm = re.match(r"El módulo profesional (\d+)\. (.+?) queda redactado", text)
        if mm and cur_titulo:
            modules_info.append((cur_titulo, mm.group(2).strip(), i))

    NOMBRES = {
        "DAM": "Desarrollo de Aplicaciones Multiplataforma",
        "DAW": "Desarrollo de Aplicaciones Web",
    }
    for t in ("DAM", "DAW"):
        td = parse_title_data(ps, art5_by_titulo[t])
        result[t] = {"nombre": NOMBRES[t], **td, "modulos": []}

    for titulo, nombre, start in modules_info:
        mod, _ = parse_module(ps, start)
        mod["nombre"] = nombre
        result[titulo]["modulos"].append(mod)

    return result


# ─── Parser RD 1629/2009  (ASIR) ─────────────────────────────────────────────

def parse_asir(xml_path: Path) -> dict:
    ps = get_paragraphs(xml_path)

    # Art.5 en ASIR (específico: no confundir con Art.6 que también tiene "competencia")
    art5_idx = next(i for i, (css, text) in enumerate(ps)
                    if css == "articulo" and re.search(r'artículo 5\.', text.lower()))

    td = parse_title_data(ps, art5_idx)
    result = {"ASIR": {
        "nombre": "Administración de Sistemas Informáticos en Red",
        **td,
        "modulos": [],
    }}

    in_annexo    = False
    modules_info = []

    for i, (css, text) in enumerate(ps):
        if "anexo_num" in css and "ANEXO I" in text:
            in_annexo = True; continue
        if not in_annexo:
            continue
        mm = re.match(r"Módulo [Pp]rofesional: (.+?)\.?\s*$", text)
        if mm and "parrafo" in css:
            modules_info.append((mm.group(1).strip(), i))

    for nombre, start in modules_info:
        mod, _ = parse_module(ps, start, asir_mode=True)
        mod["nombre"] = nombre
        result["ASIR"]["modulos"].append(mod)

    return result


# ─── HTML Template ────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FP Informática — DAM · DAW · ASIR</title>
<style>
:root{
  --dam:#1565c0;--daw:#2e7d32;--asir:#bf360c;
  --bg:#f0f2f5;--card:#fff;--border:#dde1e7;
  --ra-bg:#e8f0fe;--cont-bg:#f3f8f0;--ped-bg:#fdf6ec;
  --prof-bg:#f3e5f5;--cual-bg:#e8eaf6;--prosp-bg:#e0f2f1;--obj-bg:#fff8e1;
  --shadow:0 1px 3px rgba(0,0,0,.1);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:#1a1a2e;font-size:15px}

header{background:#1a237e;color:#fff;padding:.85rem 1.5rem;
  display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem}
header h1{font-size:.97rem;font-weight:700}
header small{opacity:.75;font-size:.76rem}

.controls{display:flex;gap:.55rem;align-items:center;padding:.75rem 1.5rem;
  background:#fff;border-bottom:1px solid var(--border);flex-wrap:wrap}
.tab-group{display:flex;gap:.3rem;flex-wrap:wrap}
.tab{padding:.3rem .8rem;border-radius:20px;border:2px solid transparent;
  cursor:pointer;font-weight:600;font-size:.78rem;transition:all .15s}
.tab[data-t="ALL"] {border-color:#555;color:#555}
.tab[data-t="DAM"] {border-color:var(--dam);color:var(--dam)}
.tab[data-t="DAW"] {border-color:var(--daw);color:var(--daw)}
.tab[data-t="ASIR"]{border-color:var(--asir);color:var(--asir)}
.tab[data-t="ALL"].on {background:#555;color:#fff}
.tab[data-t="DAM"].on {background:var(--dam);color:#fff}
.tab[data-t="DAW"].on {background:var(--daw);color:#fff}
.tab[data-t="ASIR"].on{background:var(--asir);color:#fff}
#q{flex:1;min-width:160px;padding:.32rem .75rem;border:1px solid var(--border);
  border-radius:20px;font-size:.82rem;outline:none}
#q:focus{border-color:#3949ab;box-shadow:0 0 0 2px #3949ab22}
.btn{padding:.3rem .7rem;border-radius:6px;border:1px solid var(--border);
  background:#fff;cursor:pointer;font-size:.76rem;color:#555}
.btn:hover{background:#f5f5f5}

main{padding:.9rem 1.5rem 2rem;display:flex;flex-direction:column;gap:.65rem}

/* ── Sección título ── */
.tsec{display:none}
.tsec.vis{display:block}
.tsec-hd{display:flex;align-items:center;gap:.55rem;padding:.35rem 0 .3rem;
  border-bottom:3px solid;margin-bottom:.5rem}
.tsec[data-t="DAM"]  .tsec-hd{border-color:var(--dam);color:var(--dam)}
.tsec[data-t="DAW"]  .tsec-hd{border-color:var(--daw);color:var(--daw)}
.tsec[data-t="ASIR"] .tsec-hd{border-color:var(--asir);color:var(--asir)}
.tsec-hd h2{font-size:.92rem;font-weight:700}

/* ── Badges ── */
.badge{display:inline-flex;align-items:center;font-size:.64rem;font-weight:700;
  padding:.12rem .38rem;border-radius:4px;color:#fff;white-space:nowrap;line-height:1.4}
.bd-dam{background:var(--dam)} .bd-daw{background:var(--daw)}
.bd-asir{background:var(--asir)} .bd-cod{background:#546e7a}
.bd-ra{background:#3949ab} .bd-nota{background:#6d4c41;font-size:.6rem}
.bd-ects{background:#607d8b} .bd-h{background:#78909c}
.stat{font-size:.72rem;color:#888;margin-left:auto}

/* ── Perfil del título (card colapsable) ── */
.perfil-card{background:var(--card);border:1px solid var(--border);border-radius:8px;
  overflow:hidden;box-shadow:var(--shadow);margin-bottom:.6rem}
.perfil-hd{display:flex;align-items:center;gap:.5rem;padding:.5rem .75rem;
  cursor:pointer;user-select:none;font-weight:700;font-size:.85rem}
.perfil-hd:hover{filter:brightness(.98)}
.tsec[data-t="DAM"]  .perfil-hd{background:#e3f2fd;color:var(--dam)}
.tsec[data-t="DAW"]  .perfil-hd{background:#e8f5e9;color:var(--daw)}
.tsec[data-t="ASIR"] .perfil-hd{background:#fbe9e7;color:var(--asir)}
.perfil-chv{font-size:.65rem;margin-left:auto;transition:transform .2s}
.perfil-card.open .perfil-chv{transform:rotate(90deg)}
.perfil-body{display:none;padding:.4rem .75rem .75rem}
.perfil-card.open .perfil-body{display:flex;flex-wrap:wrap;gap:.4rem}

/* ── Sub-paneles del perfil ── */
.ppanel{flex:1 1 320px;border-radius:7px;overflow:hidden;border:1px solid var(--border)}
.ppanel-hd{display:flex;align-items:center;gap:.4rem;padding:.38rem .6rem;
  cursor:pointer;user-select:none;font-weight:600;font-size:.81rem}
.ppanel-hd:hover{filter:brightness(.97)}
.ppanel[data-k="comp"]  .ppanel-hd{background:var(--prof-bg);color:#6a1b9a}
.ppanel[data-k="cual"]  .ppanel-hd{background:var(--cual-bg);color:#283593}
.ppanel[data-k="prosp"] .ppanel-hd{background:var(--prosp-bg);color:#00695c}
.ppanel[data-k="obj"]   .ppanel-hd{background:var(--obj-bg);color:#e65100}
.ppanel-chv{font-size:.58rem;margin-left:auto;transition:transform .2s}
.ppanel.open .ppanel-chv{transform:rotate(90deg)}
.ppanel-body{display:none;padding:.3rem .6rem .5rem}
.ppanel.open .ppanel-body{display:block}

/* listas dentro de los paneles del perfil */
.item-list{list-style:none;display:flex;flex-direction:column;gap:.2rem}
.item-row{font-size:.79rem;line-height:1.4;display:flex;gap:.38rem;
  padding:.18rem .35rem;border-radius:0 4px 4px 0}
.ppanel[data-k="comp"]  .item-row{border-left:3px solid #ce93d8;background:#fdf6ff}
.ppanel[data-k="prosp"] .item-row{border-left:3px solid #80cbc4;background:#f5fffe}
.ppanel[data-k="obj"]   .item-row{border-left:3px solid #ffcc02;background:#fffdf0}
.item-id{font-weight:700;flex-shrink:0;min-width:1.3rem;color:#666}

/* cualificaciones */
.cual-group{margin-bottom:.5rem}
.cual-group-hd{font-size:.74rem;font-weight:700;color:#3949ab;margin:.25rem 0 .2rem;
  padding:.15rem .3rem;background:#e8eaf6;border-radius:4px}
.cual-item{border:1px solid #c5cae9;border-radius:6px;margin-bottom:.25rem;overflow:hidden}
.cual-item-hd{display:flex;align-items:flex-start;gap:.38rem;padding:.32rem .5rem;
  cursor:pointer;background:#eef0fb;font-size:.79rem;line-height:1.38}
.cual-item-hd:hover{background:#dde1f8}
.cual-id{font-weight:700;flex-shrink:0;color:#283593;min-width:1rem}
.cual-chv{font-size:.56rem;margin-left:auto;padding-top:.12rem;transition:transform .2s;flex-shrink:0}
.cual-item.open .cual-chv{transform:rotate(90deg)}
.cual-ucs{display:none;padding:.25rem .5rem .38rem 1.8rem}
.cual-item.open .cual-ucs{display:block}
.uc-list{list-style:none;display:flex;flex-direction:column;gap:.15rem}
.uc-item{font-size:.75rem;color:#37474f;line-height:1.35;padding:.1rem 0}

/* ── Módulo ── */
.mod{background:var(--card);border:1px solid var(--border);border-radius:8px;
  overflow:hidden;box-shadow:var(--shadow);margin-bottom:.35rem}
.mod-h{display:flex;align-items:center;gap:.5rem;padding:.55rem .75rem;
  cursor:pointer;user-select:none}
.mod-h:hover{background:#fafbff}
.mod-name{font-weight:600;font-size:.86rem;flex:1;min-width:0}
.meta-badges{display:flex;gap:.25rem;flex-shrink:0}
.chv{font-size:.65rem;color:#aaa;flex-shrink:0;transition:transform .2s}
.mod.open>.mod-h .chv{transform:rotate(90deg)}
.mod-body{display:none;padding:.25rem .75rem .75rem}
.mod.open>.mod-body{display:block}

/* ── Sub-secciones módulo ── */
.sub-sec{border:1px solid var(--border);border-radius:7px;margin-bottom:.35rem;overflow:hidden}
.sub-sec-h{display:flex;align-items:center;gap:.45rem;padding:.45rem .65rem;
  cursor:pointer;user-select:none;font-weight:600;font-size:.82rem}
.sub-sec-h:hover{filter:brightness(.97)}
.sub-sec[data-k="ra"]   .sub-sec-h{background:var(--ra-bg);color:#1a237e}
.sub-sec[data-k="cont"] .sub-sec-h{background:var(--cont-bg);color:#1b5e20}
.sub-sec[data-k="ped"]  .sub-sec-h{background:var(--ped-bg);color:#bf360c}
.sub-chv{font-size:.62rem;margin-left:auto;transition:transform .2s}
.sub-sec.open .sub-chv{transform:rotate(90deg)}
.sub-body{display:none;padding:.3rem .65rem .6rem}
.sub-sec.open .sub-body{display:block}

.ra{border:1px solid #c5cae9;border-radius:6px;margin-bottom:.28rem;overflow:hidden}
.ra-h{display:flex;align-items:flex-start;gap:.4rem;padding:.42rem .6rem;
  cursor:pointer;background:var(--ra-bg);user-select:none}
.ra-h:hover{background:#d3e3fd}
.ra-txt{font-size:.82rem;flex:1;line-height:1.42}
.ra-chv{font-size:.6rem;color:#5c6bc0;padding-top:.18rem;flex-shrink:0;transition:transform .2s}
.ra.open .ra-chv{transform:rotate(90deg)}
.ra-body{display:none;padding:.4rem .6rem .55rem 2.1rem}
.ra.open .ra-body{display:block}
.ce-list{list-style:none;display:flex;flex-direction:column;gap:.2rem}
.ce{display:flex;gap:.38rem;font-size:.79rem;line-height:1.38;
  border-left:3px solid #9fa8da;padding:.22rem .38rem;border-radius:0 4px 4px 0;background:#fafbff}
.ce-l{font-weight:700;color:#3949ab;min-width:.8rem;flex-shrink:0}

.bloque{border:1px solid #c8e6c9;border-radius:6px;margin-bottom:.25rem;overflow:hidden}
.blq-h{display:flex;align-items:center;gap:.4rem;padding:.38rem .6rem;
  cursor:pointer;background:#e8f5e9;user-select:none;font-size:.81rem;font-weight:600;color:#1b5e20}
.blq-h:hover{background:#d0efda}
.blq-chv{font-size:.58rem;margin-left:auto;transition:transform .2s}
.bloque.open .blq-chv{transform:rotate(90deg)}
.blq-body{display:none;padding:.3rem .6rem .5rem 1.2rem}
.bloque.open .blq-body{display:block}
.cont-list{list-style:none;display:flex;flex-direction:column;gap:.18rem}
.cont-item{font-size:.79rem;line-height:1.38;padding:.18rem .35rem;
  border-left:3px solid #81c784;padding-left:.5rem;background:#f9fdf9;
  border-radius:0 4px 4px 0}

.ped-item{margin-bottom:.3rem}
.ped-txt{font-size:.81rem;line-height:1.42;padding:.28rem .4rem;
  background:#fff8f0;border-left:3px solid #ffcc80;border-radius:0 4px 4px 0;cursor:default}
.ped-txt.has-bullets{cursor:pointer}
.ped-txt.has-bullets:hover{background:#fff3e0}
.ped-bul-hd{display:flex;align-items:center;gap:.3rem}
.ped-chv{font-size:.6rem;margin-left:auto;transition:transform .2s;color:#e65100}
.ped-item.open .ped-chv{transform:rotate(90deg)}
.ped-bullets{display:none;padding:.25rem .4rem .4rem 1.4rem}
.ped-item.open .ped-bullets{display:block}
.ped-bul-list{list-style:none;display:flex;flex-direction:column;gap:.18rem}
.ped-bul-item{font-size:.79rem;line-height:1.38;padding:.18rem .35rem;
  border-left:3px solid #ffb74d;padding-left:.5rem;background:#fffbf5;
  border-radius:0 4px 4px 0}

.nota-banner{font-size:.73rem;background:#fff3e0;border:1px solid #ffcc02;
  border-radius:5px;padding:.28rem .55rem;margin-bottom:.35rem;color:#5d4037;
  display:flex;align-items:center;gap:.38rem}

mark{background:#fff176;border-radius:2px;padding:0 1px}
.hidden{display:none!important}
</style>
</head>
<body>

<header>
  <h1>RD 405/2023 &amp; RD 1629/2009 — Perfil profesional · Módulos · RA+CE · Contenidos</h1>
  <small>DAM &middot; DAW &middot; ASIR &middot; Familia Informática y Comunicaciones</small>
</header>

<div class="controls">
  <div class="tab-group">
    <div class="tab on" data-t="ALL">Todos</div>
    <div class="tab" data-t="DAM">DAM</div>
    <div class="tab" data-t="DAW">DAW</div>
    <div class="tab" data-t="ASIR">ASIR</div>
  </div>
  <input id="q" type="search" placeholder="Buscar competencia, módulo, RA, CE, objetivo…">
  <button class="btn" id="btn-exp">Expandir todo</button>
  <button class="btn" id="btn-col">Colapsar todo</button>
</div>

<main id="main"></main>

<script>
const DATA = __DATA_PLACEHOLDER__;

const esc = s => s.replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));

function hl(text, q) {
  if (!q) return esc(text);
  return esc(text).replace(
    new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`, 'gi'),
    '<mark>$1</mark>');
}

const TITULO_META = {
  DAM:  {badge:'bd-dam',  rd:'RD 405/2023'},
  DAW:  {badge:'bd-daw',  rd:'RD 405/2023'},
  ASIR: {badge:'bd-asir', rd:'RD 1629/2009'},
};

// ── Perfil del título ────────────────────────────────────────────────────────

function buildItemList(items) {
  return `<ul class="item-list">${items.map(it =>
    `<li class="item-row"><span class="item-id">${esc(it.id)}${it.id ? (it.id.match(/\d/) ? '.' : ')') : ''}</span><span>${esc(it.texto)}</span></li>`
  ).join('')}</ul>`;
}

function buildCualificaciones(cual) {
  let html = '';
  for (const [tipo, items] of [['completas', cual.completas], ['incompletas', cual.incompletas]]) {
    if (!items || items.length === 0) continue;
    const lbl = tipo === 'completas' ? 'Cualificaciones profesionales completas' : 'Cualificaciones profesionales incompletas';
    html += `<div class="cual-group"><div class="cual-group-hd">${lbl}</div>`;
    for (const q of items) {
      const hasUC = q.ucs && q.ucs.length > 0;
      const prefix = q.id ? `<span class="cual-id">${esc(q.id)})</span>` : '';
      html += `<div class="cual-item">
        <div class="cual-item-hd">${prefix}<span>${esc(q.nombre)}</span>${hasUC ? '<span class="cual-chv">&#9658;</span>' : ''}</div>
        ${hasUC ? `<div class="cual-ucs"><ul class="uc-list">${q.ucs.map(u => `<li class="uc-item">${esc(u)}</li>`).join('')}</ul></div>` : ''}
      </div>`;
    }
    html += '</div>';
  }
  const wrap = document.createElement('div');
  wrap.innerHTML = html;
  wrap.querySelectorAll('.cual-item').forEach(d => {
    const hd = d.querySelector('.cual-item-hd');
    if (d.querySelector('.cual-ucs')) hd.addEventListener('click', () => d.classList.toggle('open'));
  });
  return wrap;
}

function buildPPanel(key, label, contentFn, data) {
  const p = document.createElement('div');
  p.className = 'ppanel'; p.dataset.k = key;
  const hd = document.createElement('div');
  hd.className = 'ppanel-hd';
  hd.innerHTML = `${label}<span class="ppanel-chv">&#9658;</span>`;
  const body = document.createElement('div');
  body.className = 'ppanel-body';
  if (typeof contentFn === 'string') body.innerHTML = contentFn;
  else body.appendChild(contentFn(data));
  hd.addEventListener('click', () => p.classList.toggle('open'));
  p.appendChild(hd); p.appendChild(body);
  return p;
}

function buildPerfilCard(titulo, info) {
  const card = document.createElement('div');
  card.className = 'perfil-card';
  const hd = document.createElement('div');
  hd.className = 'perfil-hd';
  const nComp = (info.competencias||[]).length;
  const nObj  = (info.objetivos_generales||[]).length;
  hd.innerHTML = `Perfil del título &nbsp;
    <span class="badge" style="background:#7b1fa2">${nComp} competencias</span>
    <span class="badge" style="background:#1565c0;margin-left:.25rem">${nObj} objetivos</span>
    <span class="perfil-chv">&#9658;</span>`;
  const body = document.createElement('div');
  body.className = 'perfil-body';

  if (info.competencias?.length)
    body.appendChild(buildPPanel('comp',
      `Competencias profesionales, personales y sociales <span class="badge" style="background:#7b1fa2;margin-left:.3rem">${nComp}</span>`,
      () => { const d=document.createElement('div'); d.innerHTML=buildItemList(info.competencias); return d; }));

  if (info.cualificaciones)
    body.appendChild(buildPPanel('cual',
      'Cualificaciones profesionales',
      buildCualificaciones, info.cualificaciones));

  if (info.prospectiva?.length)
    body.appendChild(buildPPanel('prosp',
      `Prospectiva del título <span class="badge" style="background:#00796b;margin-left:.3rem">${info.prospectiva.length}</span>`,
      () => { const d=document.createElement('div'); d.innerHTML=buildItemList(info.prospectiva); return d; }));

  if (info.objetivos_generales?.length)
    body.appendChild(buildPPanel('obj',
      `Objetivos generales <span class="badge" style="background:#e65100;margin-left:.3rem">${nObj}</span>`,
      () => { const d=document.createElement('div'); d.innerHTML=buildItemList(info.objetivos_generales); return d; }));

  hd.addEventListener('click', () => card.classList.toggle('open'));
  card.appendChild(hd); card.appendChild(body);
  return card;
}

// ── Módulos ──────────────────────────────────────────────────────────────────

function buildRA(ra) {
  const d = document.createElement('div');
  d.className = 'ra';
  d.innerHTML = `
    <div class="ra-h">
      <span class="badge bd-ra">RA${ra.numero}</span>
      <span class="ra-txt">${esc(ra.enunciado)}</span>
      <span class="ra-chv">&#9658;</span>
    </div>
    <div class="ra-body">
      <ul class="ce-list">${ra.CEs.map(ce =>
        `<li class="ce"><span class="ce-l">${ce.letra})</span><span>${esc(ce.texto)}</span></li>`
      ).join('')}</ul>
    </div>`;
  d.querySelector('.ra-h').addEventListener('click', () => d.classList.toggle('open'));
  return d;
}

function buildContenidos(contenidos) {
  const wrap = document.createElement('div');
  for (const blq of contenidos) {
    const d = document.createElement('div');
    d.className = 'bloque';
    d.innerHTML = `
      <div class="blq-h">${esc(blq.bloque)}<span class="blq-chv">&#9658;</span></div>
      <div class="blq-body"><ul class="cont-list">${blq.items.map(it =>
        `<li class="cont-item">${esc(it)}</li>`).join('')}</ul></div>`;
    d.querySelector('.blq-h').addEventListener('click', () => d.classList.toggle('open'));
    wrap.appendChild(d);
  }
  return wrap;
}

function buildOrientaciones(orientaciones) {
  const wrap = document.createElement('div');
  for (const ped of orientaciones) {
    const d = document.createElement('div');
    d.className = 'ped-item';
    const hasBul = ped.bullets && ped.bullets.length > 0;
    d.innerHTML = `
      <div class="ped-txt${hasBul ? ' has-bullets' : ''}">
        <div class="ped-bul-hd"><span>${esc(ped.texto)}</span>
        ${hasBul ? '<span class="ped-chv">&#9658;</span>' : ''}</div>
      </div>
      ${hasBul ? `<div class="ped-bullets"><ul class="ped-bul-list">${ped.bullets.map(b =>
        `<li class="ped-bul-item">${esc(b)}</li>`).join('')}</ul></div>` : ''}`;
    if (hasBul) d.querySelector('.ped-txt').addEventListener('click', () => d.classList.toggle('open'));
    wrap.appendChild(d);
  }
  return wrap;
}

function buildSubSec(key, label, contentFn, data, openByDefault) {
  const sec = document.createElement('div');
  sec.className = `sub-sec${openByDefault ? ' open' : ''}`;
  sec.dataset.k = key;
  const hd = document.createElement('div'); hd.className = 'sub-sec-h';
  hd.innerHTML = `${label}<span class="sub-chv">&#9658;</span>`;
  const body = document.createElement('div'); body.className = 'sub-body';
  body.appendChild(contentFn(data));
  hd.addEventListener('click', () => sec.classList.toggle('open'));
  sec.appendChild(hd); sec.appendChild(body);
  return sec;
}

function buildUI(data) {
  const main = document.getElementById('main');

  for (const [titulo, info] of Object.entries(data)) {
    const meta = TITULO_META[titulo] || {badge:'bd-cod', rd:''};
    const sec  = document.createElement('div');
    sec.className = 'tsec vis';
    sec.dataset.t  = titulo;

    sec.innerHTML = `<div class="tsec-hd">
      <span class="badge ${meta.badge}">${titulo}</span>
      <h2>${info.nombre}</h2>
      <span class="stat">${meta.rd} &nbsp;·&nbsp; ${info.modulos.length} módulos</span>
    </div>`;

    sec.appendChild(buildPerfilCard(titulo, info));

    for (const mod of info.modulos) {
      const nRA = mod.RAs.length;
      const nCE = mod.RAs.reduce((s,r) => s + r.CEs.length, 0);
      const div  = document.createElement('div');
      div.className  = 'mod';
      div.dataset.t  = titulo;
      div.dataset.idx = [
        mod.codigo, mod.nombre,
        ...mod.RAs.flatMap(r => [r.enunciado, ...r.CEs.map(c => c.texto)]),
        ...(mod.contenidos||[]).flatMap(b => [b.bloque, ...b.items]),
        ...(mod.orientaciones||[]).flatMap(p => [p.texto, ...p.bullets]),
      ].join(' ').toLowerCase();

      const ectsTxt  = mod.ects  ? `<span class="badge bd-ects">${mod.ects} ECTS</span>` : '';
      const horasTxt = mod.horas ? `<span class="badge bd-h">${mod.horas} h</span>` : '';

      div.innerHTML = `
        <div class="mod-h">
          <span class="badge bd-cod">${mod.codigo}</span>
          <span class="mod-name">${esc(mod.nombre)}</span>
          <div class="meta-badges">${ectsTxt}${horasTxt}</div>
          <span class="stat">${nRA}&thinsp;RA &middot; ${nCE}&thinsp;CE</span>
          <span class="chv">&#9658;</span>
        </div>
        <div class="mod-body"></div>`;

      const body = div.querySelector('.mod-body');

      if (titulo === 'ASIR' && mod.codigo === '0373') {
        const nota = document.createElement('div');
        nota.className = 'nota-banner';
        nota.innerHTML = `<span class="badge bd-nota">NOTA</span>
          RA+CE y contenidos del <strong>RD 405/2023</strong> (actualiza este módulo para ASIR)`;
        body.appendChild(nota);
      }

      const raLabel = `Resultados de Aprendizaje &nbsp;<span class="badge bd-ra">${nRA}&thinsp;RA &middot; ${nCE}&thinsp;CE</span>`;
      body.appendChild(buildSubSec('ra', raLabel,
        _ => { const w=document.createElement('div'); for(const ra of mod.RAs) w.appendChild(buildRA(ra)); return w; },
        null, true));

      if (mod.contenidos?.length) {
        body.appendChild(buildSubSec('cont',
          `Contenidos básicos &nbsp;<span class="badge" style="background:#558b2f">${mod.contenidos.length} bloques</span>`,
          buildContenidos, mod.contenidos, false));
      }
      if (mod.orientaciones?.length) {
        body.appendChild(buildSubSec('ped', 'Orientaciones pedagógicas',
          buildOrientaciones, mod.orientaciones, false));
      }

      div.querySelector('.mod-h').addEventListener('click', () => div.classList.toggle('open'));
      sec.appendChild(div);
    }
    main.appendChild(sec);
  }
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab =>
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
    tab.classList.add('on');
    applyFilters();
  })
);

function applyFilters() {
  const q   = document.getElementById('q').value.trim().toLowerCase();
  const act = document.querySelector('.tab.on').dataset.t;

  document.querySelectorAll('.mod').forEach(div => {
    const okT = act === 'ALL' || div.dataset.t === act;
    const okQ = !q || div.dataset.idx.includes(q);
    div.classList.toggle('hidden', !okT || !okQ);
    if (q && okQ) div.classList.add('open'); else if (q) div.classList.remove('open');
  });

  document.querySelectorAll('.mod:not(.hidden)').forEach(div => {
    div.querySelectorAll('.mod-name,.ra-txt,.ce span:last-child,.cont-item,.ped-txt span:first-child').forEach(el => {
      const orig = el.dataset.orig ?? el.textContent;
      el.dataset.orig = orig;
      el.innerHTML = hl(orig, q);
    });
  });

  document.querySelectorAll('.tsec').forEach(s => {
    const hasVis = [...s.querySelectorAll('.mod')].some(d => !d.classList.contains('hidden'));
    s.classList.toggle('vis', (act === 'ALL' || s.dataset.t === act) && (!q || hasVis));
  });
}

document.getElementById('q').addEventListener('input', applyFilters);

document.getElementById('btn-exp').addEventListener('click', () => {
  document.querySelectorAll('.perfil-card,.mod:not(.hidden)').forEach(d => d.classList.add('open'));
  document.querySelectorAll('.ppanel,.mod:not(.hidden) .sub-sec').forEach(d => d.classList.add('open'));
  document.querySelectorAll('.mod:not(.hidden) .ra,.mod:not(.hidden) .bloque').forEach(d => d.classList.add('open'));
  document.querySelectorAll('.mod:not(.hidden) .ped-item').forEach(d => d.classList.add('open'));
  document.querySelectorAll('.cual-item').forEach(d => d.classList.add('open'));
});
document.getElementById('btn-col').addEventListener('click', () => {
  document.querySelectorAll('.perfil-card,.ppanel,.mod,.sub-sec,.ra,.bloque,.ped-item,.cual-item').forEach(d => d.classList.remove('open'));
});

buildUI(DATA);
</script>
</body>
</html>
"""


def generate_html(data: dict, out: Path):
    out.write_text(
        HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json.dumps(data, ensure_ascii=False)),
        encoding="utf-8",
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    for path in (XML_405, XML_1629):
        if not path.exists():
            sys.exit(f"No encuentro: {path}")

    print("Procesando RD 405/2023  (DAM + DAW)…", file=sys.stderr)
    data_405  = parse_405(XML_405)

    print("Procesando RD 1629/2009 (ASIR)…",       file=sys.stderr)
    data_asir = parse_asir(XML_1629)

    # Módulo 0373 en ASIR → RA+CE y contenidos del RD 405/2023
    ref_0373 = next((m for m in data_405["DAM"]["modulos"] if m["codigo"] == "0373"), None)
    for mod in data_asir["ASIR"]["modulos"]:
        if mod["codigo"] == "0373" and ref_0373:
            mod["RAs"]        = ref_0373["RAs"]
            mod["contenidos"] = ref_0373["contenidos"]
            # Orientaciones se mantienen del RD 1629 (contexto profesional ASIR)

    data = {**data_405, **data_asir}

    JSON_OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON  →  {JSON_OUT}", file=sys.stderr)
    generate_html(data, HTML_OUT)
    print(f"HTML  →  {HTML_OUT}\n", file=sys.stderr)

    for titulo, info in data.items():
        total_ra   = sum(len(m["RAs"]) for m in info["modulos"])
        total_ce   = sum(len(r["CEs"]) for m in info["modulos"] for r in m["RAs"])
        total_blq  = sum(len(m.get("contenidos", [])) for m in info["modulos"])
        total_ped  = sum(len(m.get("orientaciones", [])) for m in info["modulos"])
        print(f"{titulo} — {info['nombre']}", file=sys.stderr)
        print(f"  {len(info['modulos'])} mód · {total_ra} RA · {total_ce} CE · "
              f"{total_blq} bloques contenido · {total_ped} subsec. orientaciones", file=sys.stderr)
        for m in info["modulos"]:
            nCE  = sum(len(r["CEs"]) for r in m["RAs"])
            nBlq = len(m.get("contenidos", []))
            nota = " ← RD 405/2023" if titulo == "ASIR" and m["codigo"] == "0373" else ""
            print(f"  {m['codigo']}  {str(m.get('ects') or '?'):>2} ECTS  {str(m.get('horas') or '?'):>3}h  "
                  f"{len(m['RAs'])} RA  {nCE:>3} CE  {nBlq:>2} bloques"
                  f"  {m['nombre'][:40]}{nota}", file=sys.stderr)
        print(file=sys.stderr)


if __name__ == "__main__":
    main()
