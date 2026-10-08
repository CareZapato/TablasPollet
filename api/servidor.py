"""
API local: conecta la web con la base PostgreSQL "tablaspollet" y sirve el sitio.

Lee las tablas originales de la base, les aplica el mismo ETL que a las fuentes estáticas
(etl/procesar_excel.py) y entrega el resultado con el mismo formato (modelo.json + CSV por tabla),
de modo que la web puede alternar entre Excel trabajado, CSV originales y la base.

Endpoints:
    GET /api/estado                          Estado de la conexión y filas por tabla de la base.
    GET /api/fuentes/bd/modelo.json          Reprocesa la base, publica el resultado en el esquema "lavadero"
                                             y devuelve el modelo y la calidad.
    GET /api/fuentes/bd/{tabla}.csv          Tabla procesada (del último reproceso).
    GET /api/fuentes/bd/export/{archivo}     Descargas CSV (.zip), Excel (.xlsx) y PostgreSQL (.sql).
    GET /                                    Sitio web (carpeta web/).

Uso:
    python api/servidor.py            -> http://localhost:8000
Variables de entorno opcionales: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE, PUERTO.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
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
from crear_bd import BASE, CONEXION, TABLAS_BD  # noqa: E402
from exportar import sql_postgres  # noqa: E402

CACHE = RAIZ / "api" / ".cache" / "bd"
ESQUEMA = etl.FUENTE_BD["esquema"]
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


def leer_bd() -> dict[str, pd.DataFrame]:
    datos = {}
    with conectar() as conn:
        for tabla, (clave, _) in TABLAS_BD.items():
            columnas = etl.COLUMNAS_FUENTE[clave]
            consulta = f"SELECT {', '.join(c.lower() for c in columnas)} FROM {tabla} ORDER BY 1"
            df = pd.DataFrame(conn.execute(consulta).fetchall(), columns=columnas, dtype=object).fillna("")
            datos[clave] = etl._seleccionar(df, clave, tabla)
    return datos


def reprocesar(descargas: bool = False) -> None:
    datos = leer_bd()

    def cargar() -> dict[str, pd.DataFrame]:
        etl.registrar("cartera_clientes", "producto_financiero", "La cartera original no trae la columna Producto",
                      len(datos["cartera"]), "Se asocia desde la tabla productos_cartera por ID_Cliente")
        return datos

    with bloqueo:
        shutil.rmtree(CACHE, ignore_errors=True)
        f = etl.FUENTE_BD
        etl.exportar(f["id"], f["nombre"], f["descripcion"], URL_BD, cargar, carpeta=CACHE, descargas=descargas,
                     al_procesar=publicar)


def publicar(tablas: dict[str, pd.DataFrame], modelo: dict) -> None:
    """Deja el resultado del ETL en el esquema "lavadero" para consultarlo con SQL (las consultas que copia la web)."""
    sql = sql_postgres(tablas, modelo, ESQUEMA, f"Lavadero de datos · {etl.FUENTE_BD['nombre']}")
    with conectar() as conn:
        conn.execute(sql)


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
        reprocesar()
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
            reprocesar(descargas=True)
        elif not destino.exists():
            reprocesar()
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
