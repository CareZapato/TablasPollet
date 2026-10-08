"""
API local: conecta la web con la base PostgreSQL "tablaspollet" y sirve el sitio.

La base tiene el mismo modelo de datos que muestra la web (ver bd/crear_bd.py). La API lee sus tablas
y las entrega con el mismo formato que las fuentes estáticas (modelo.json + CSV por tabla),
de modo que la web puede alternar entre Excel trabajado, CSV originales y la base.

Endpoints:
    GET /api/estado                          Estado de la conexión y filas por tabla de la base.
    GET /api/fuentes/bd/modelo.json          Lee la base y devuelve el modelo, las filas y la calidad.
    GET /api/fuentes/bd/{tabla}.csv          Tabla leída de la base (de la última lectura).
    GET /api/fuentes/bd/export/{archivo}     Descargas CSV (.zip), Excel (.xlsx) y PostgreSQL (.sql).
    GET /                                    Sitio web (carpeta web/).

Uso:
    python api/servidor.py            -> http://localhost:8000
Variables de entorno opcionales: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE, PUERTO.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

RAIZ = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RAIZ / "etl"), str(RAIZ / "bd")]

import procesar_excel as etl  # noqa: E402
from crear_bd import BASE, CONEXION, ESQUEMA, TABLAS_BD, leer_modelo  # noqa: E402
from exportar import exportar_todo  # noqa: E402

CACHE = RAIZ / "api" / ".cache" / "bd"
URL_BD = f"postgresql://{CONEXION['user']}@{CONEXION['host']}:{CONEXION['port']}/{BASE}"
bloqueo = threading.Lock()

app = FastAPI(title="Lavadero de datos · API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.middleware("http")
async def permitir_red_privada(request: Request, call_next):
    """Permite que la web publicada (por ejemplo en Render) llame a esta API en localhost."""
    respuesta = await call_next(request)
    respuesta.headers["Access-Control-Allow-Private-Network"] = "true"
    return respuesta


def conectar() -> psycopg.Connection:
    return psycopg.connect(**CONEXION, dbname=BASE, connect_timeout=2)


def actualizar(descargas: bool = False) -> None:
    """Lee las tablas del modelo desde la base y las deja como modelo.json + un CSV por tabla."""
    with conectar() as conn:
        tablas, modelo, hallazgos = leer_modelo(conn)
    f = etl.FUENTE_BD
    with bloqueo:
        shutil.rmtree(CACHE, ignore_errors=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        for nombre, df in tablas.items():
            df.to_csv(CACHE / f"{nombre}.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
            modelo[nombre]["filas"] = len(df)
        metadatos = {"id": f["id"], "nombre": f["nombre"], "descripcion": f["descripcion"], "fuente": URL_BD,
                     "generado": datetime.now().isoformat(timespec="seconds"), "tablas": modelo, "calidad": hallazgos}
        (CACHE / "modelo.json").write_text(json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8")
        if descargas:
            exportar_todo(CACHE, f["id"], f["nombre"], tablas, modelo, hallazgos)


def error_bd(e: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=f"Sin conexión con {URL_BD}: {e}")


@app.get("/api/estado")
def estado():
    respuesta = {"ok": True, "base": BASE, "esquema": ESQUEMA, "host": CONEXION["host"], "puerto": CONEXION["port"],
                 "usuario": CONEXION["user"], "conectado": False, "tablas": {}, "error": None}
    try:
        with conectar() as conn:
            for tabla in TABLAS_BD:
                existe = conn.execute("SELECT to_regclass(%s)", (tabla,)).fetchone()[0]
                respuesta["tablas"][tabla] = (conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
                                              if existe else None)
        faltantes = [t for t, n in respuesta["tablas"].items() if n is None]
        if faltantes:
            respuesta["error"] = f"Faltan tablas: {', '.join(faltantes)}. Ejecuta python bd/crear_bd.py"
        else:
            respuesta["conectado"] = True
    except Exception as e:  # noqa: BLE001 - se informa cualquier fallo de conexión
        respuesta["error"] = str(e).strip()
    return respuesta


@app.get("/api/fuentes/bd/modelo.json")
def modelo():
    try:
        actualizar()
    except psycopg.Error as e:
        raise error_bd(e) from e
    return FileResponse(CACHE / "modelo.json", headers={"Cache-Control": "no-store"})


@app.get("/api/fuentes/bd/{ruta:path}")
def archivo(ruta: str):
    destino = (CACHE / ruta).resolve()
    if not destino.is_relative_to(CACHE.resolve()):
        raise HTTPException(status_code=404)
    try:
        if ruta.startswith("export/"):
            actualizar(descargas=True)
        elif not destino.exists():
            actualizar()
    except psycopg.Error as e:
        raise error_bd(e) from e
    if not destino.is_file():
        raise HTTPException(status_code=404, detail=f"No existe {ruta}")
    return FileResponse(destino, filename=destino.name if ruta.startswith("export/") else None,
                        headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=RAIZ / "web", html=True), name="web")


if __name__ == "__main__":
    puerto = int(os.getenv("PUERTO", "8000"))
    print(f"Lavadero de datos en http://localhost:{puerto}  ·  base {URL_BD}")
    uvicorn.run(app, host="127.0.0.1", port=puerto)
