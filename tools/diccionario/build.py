"""
Regenera el diccionario de datos (Muevo_Diccionario_de_Datos.docx y diccionario_datos_muevo.pdf)
desde los modelos. Falla si una tabla no tiene descripción o dominio, o si sobra alguna en
descripciones.json / dominios.json, para que el diccionario no quede desactualizado en silencio.

    cd C:\\Claude\\muevo-backend
    .venv\\Scripts\\python tools\\diccionario\\build.py          # docx + pdf
    .venv\\Scripts\\python tools\\diccionario\\build.py --sin-pdf

Requisitos: Node.js (la primera vez instala `docx` en tools/diccionario) y, para el PDF,
Microsoft Word (se convierte por automatización COM; sin Word se omite).

Al agregar una tabla: sumá su descripción a descripciones.json y su dominio a dominios.json.
"""
import json
import os
import shutil
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", ".."))
BUILD = os.path.join(AQUI, "build")
DOCX = os.path.join(RAIZ, "Muevo_Diccionario_de_Datos.docx")
PDF = os.path.join(RAIZ, "diccionario_datos_muevo.pdf")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, AQUI)
from introspeccion import esquema  # noqa: E402


def cargar(nombre):
    with open(os.path.join(AQUI, nombre), encoding="utf-8") as f:
        return json.load(f)


def validar(esq, dominios, descripciones):
    tablas = {t["table"] for t in esq["tables"]}
    en_dominios = [t for d in dominios for t in d["tables"]]
    problemas = []
    if sorted(tablas - set(en_dominios)):
        problemas.append(f"Tablas sin dominio en dominios.json: {sorted(tablas - set(en_dominios))}")
    if sorted(set(en_dominios) - tablas):
        problemas.append(f"Tablas en dominios.json que ya no existen: {sorted(set(en_dominios) - tablas)}")
    repetidas = sorted({t for t in en_dominios if en_dominios.count(t) > 1})
    if repetidas:
        problemas.append(f"Tablas repetidas en dominios.json: {repetidas}")
    if sorted(tablas - set(descripciones)):
        problemas.append(f"Tablas sin descripción en descripciones.json: {sorted(tablas - set(descripciones))}")
    if sorted(set(descripciones) - tablas):
        problemas.append(f"Descripciones de tablas que ya no existen: {sorted(set(descripciones) - tablas)}")
    sin_clase = [t["table"] for t in esq["tables"] if t["class"] == "?"]
    if sin_clase:
        problemas.append(f"Tablas sin clase de modelo: {sin_clase}")
    return problemas


def main():
    sin_pdf = "--sin-pdf" in sys.argv
    esq = esquema()
    problemas = validar(esq, cargar("dominios.json"), cargar("descripciones.json"))
    if problemas:
        print("No se generó el diccionario:")
        for p in problemas:
            print("  -", p)
        sys.exit(1)

    os.makedirs(BUILD, exist_ok=True)
    with open(os.path.join(BUILD, "esquema.json"), "w", encoding="utf-8") as f:
        json.dump(esq, f, ensure_ascii=False)

    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        sys.exit("Falta Node.js (node y npm en el PATH).")
    if not os.path.isdir(os.path.join(AQUI, "node_modules", "docx")):
        print("Instalando la dependencia docx…")
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=AQUI, check=True, shell=(os.name == "nt"))
    subprocess.run([node, os.path.join(AQUI, "generar.js"), DOCX], cwd=AQUI, check=True)
    print(f"{esq['num_tables']} tablas → {DOCX}")

    if sin_pdf:
        return
    if os.name != "nt" or not shutil.which("powershell"):
        print("PDF omitido: la conversión usa Microsoft Word en Windows.")
        return
    ps = (
        "$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
        f"$d = $w.Documents.Open('{DOCX}'); $d.SaveAs([ref]'{PDF}', [ref]17); $d.Close(); $w.Quit()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(PDF):
        print("No se pudo convertir a PDF (¿Word instalado?):", (r.stderr or r.stdout).strip()[:300])
    else:
        print(f"PDF → {PDF}")


if __name__ == "__main__":
    main()
