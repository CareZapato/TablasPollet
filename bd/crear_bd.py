"""
Crea la base PostgreSQL "tablaspollet" con el mismo modelo de datos que muestra la web.

Borra la estructura anterior y crea, en el esquema public:
  - Las 15 tablas normalizadas (geografía, cartera financiera y comercial) con PK, FK, tipos y comentarios.
  - Las 5 tablas origen_* con los datos de los CSV originales tal cual (texto, sin limpiar).
  - hallazgos_calidad, con los problemas que detectó el ETL y la acción aplicada.

Los datos salen de los CSV originales (web/data/dataoriginal) procesados por el mismo ETL de la web
(etl/procesar_excel.py), así que las consultas sobre la base devuelven lo mismo que la fuente "CSV originales".

Genera además bd/tablaspollet.sql, que se puede cargar a mano con psql o pgAdmin.

Uso:
    python bd/crear_bd.py
Variables de entorno opcionales: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE.
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))

import procesar_excel as etl  # noqa: E402
from exportar import _literal, sql_postgres  # noqa: E402

CONEXION = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5432")),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "123456"),
}
BASE = os.getenv("PGDATABASE", "tablaspollet")
ESQUEMA = "public"
SCRIPT_SQL = Path(__file__).with_name("tablaspollet.sql")
TABLA_HALLAZGOS = "hallazgos_calidad"
COLUMNAS_HALLAZGOS = ["tabla", "columna", "problema", "filas_afectadas", "accion"]
COLUMNA_FILA = "nro_fila"
ORDEN_LECTURA = {"ventas": "fecha_venta, id_transaccion"}  # mismo orden que entrega el ETL


def modelo_web() -> dict:
    """Modelo que muestra la web: tablas normalizadas más las tablas origen_* (columnas tal como vienen)."""
    modelo = copy.deepcopy(etl.MODELO)
    for clave, descripcion in etl.DESCRIPCION_ORIGEN.items():
        modelo[f"origen_{clave}"] = {
            "dominio": "Datos de origen", "origen": True, "descripcion": descripcion,
            "columnas": [etl.col(c, "texto", "Valor sin limpiar") for c in etl.COLUMNAS_FUENTE[clave]],
        }
    return modelo


TABLAS_BD = [*modelo_web(), TABLA_HALLAZGOS]


def procesar_originales() -> tuple[dict[str, pd.DataFrame], dict, list[dict]]:
    etl.calidad.clear()
    fuente = etl.cargar_originales(etl.CARPETA_ORIGINAL, etl.EXCEL)
    tablas, modelo = etl.procesar(fuente)
    hallazgos = [q for q in etl.calidad if q["filas_afectadas"] > 0]
    assert list(modelo) == list(modelo_web()), "El modelo del ETL no coincide con el de la web"
    return tablas, modelo, hallazgos


def sql_hallazgos(hallazgos: list[dict]) -> str:
    filas = ",\n".join(
        f"    ({i}, " + ", ".join(_literal(q[c]) for c in COLUMNAS_HALLAZGOS) + ")"
        for i, q in enumerate(hallazgos, start=1))
    return f"""CREATE TABLE {TABLA_HALLAZGOS} (
    id_hallazgo INTEGER PRIMARY KEY,
    tabla TEXT,
    columna TEXT,
    problema TEXT,
    filas_afectadas INTEGER,
    accion TEXT
);
COMMENT ON TABLE {TABLA_HALLAZGOS} IS 'Problemas de calidad detectados por el ETL en los CSV originales y la acción aplicada.';
INSERT INTO {TABLA_HALLAZGOS} (id_hallazgo, {', '.join(COLUMNAS_HALLAZGOS)}) VALUES
{filas};

"""


def con_nro_fila(tablas: dict[str, pd.DataFrame], modelo: dict) -> tuple[dict[str, pd.DataFrame], dict]:
    """Agrega nro_fila (PK) a las tablas origen_*: PostgreSQL no garantiza el orden físico de las filas."""
    tablas, modelo = dict(tablas), copy.deepcopy(modelo)
    for nombre, meta in modelo.items():
        if meta.get("origen"):
            meta["columnas"].insert(0, etl.col(COLUMNA_FILA, "entero", "Número de fila en el CSV original", pk=True))
            tablas[nombre] = tablas[nombre].copy()
            tablas[nombre].insert(0, COLUMNA_FILA, range(1, len(tablas[nombre]) + 1))
    return tablas, modelo


def generar_sql(tablas: dict[str, pd.DataFrame], modelo: dict, hallazgos: list[dict]) -> str:
    tablas, modelo = con_nro_fila(tablas, modelo)
    sql = sql_postgres(tablas, modelo, ESQUEMA, "Base tablaspollet · modelo de la web con los datos de los CSV originales",
                       SCRIPT_SQL.name)
    sql = sql.replace("BEGIN;\n", "BEGIN;\n\nDROP SCHEMA IF EXISTS lavadero CASCADE;\n", 1)
    cuerpo, fin = sql.rsplit("COMMIT;", 1)
    return cuerpo + sql_hallazgos(hallazgos) + "COMMIT;" + fin


def valor_csv(valor) -> str | None:
    """Mismo formato que los CSV del ETL: booleanos True/False, fechas ISO y nulos vacíos."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "True" if valor else "False"
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)


def leer_modelo(conn: psycopg.Connection) -> tuple[dict[str, pd.DataFrame], dict, list[dict]]:
    """Lee de la base todas las tablas del modelo de la web (en el orden de carga) y los hallazgos de calidad."""
    modelo = modelo_web()
    tablas = {}
    for nombre, meta in modelo.items():
        columnas = [c["nombre"] for c in meta["columnas"]]
        orden = COLUMNA_FILA if meta.get("origen") else ORDEN_LECTURA.get(nombre, "1")
        filas = conn.execute(f"SELECT {', '.join(c.lower() for c in columnas)} FROM {nombre} ORDER BY {orden}")
        tablas[nombre] = pd.DataFrame([[valor_csv(v) for v in f] for f in filas], columns=columnas, dtype=object)
    hallazgos = [dict(zip(COLUMNAS_HALLAZGOS, f)) for f in conn.execute(
        f"SELECT {', '.join(COLUMNAS_HALLAZGOS)} FROM {TABLA_HALLAZGOS} ORDER BY id_hallazgo")]
    return tablas, modelo, hallazgos


def crear_base() -> None:
    with psycopg.connect(**CONEXION, dbname="postgres", autocommit=True, connect_timeout=5) as conn:
        existe = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (BASE,)).fetchone()
        if not existe:
            conn.execute(f'CREATE DATABASE "{BASE}" ENCODING \'UTF8\' TEMPLATE template0')
            print(f"Base {BASE} creada.")
        else:
            print(f"Base {BASE} ya existe: se reemplaza su estructura.")


def main() -> None:
    tablas, modelo, hallazgos = procesar_originales()
    sql = generar_sql(tablas, modelo, hallazgos)
    SCRIPT_SQL.write_text(sql, encoding="utf-8")
    print(f"Script generado: {SCRIPT_SQL.name}")

    crear_base()
    with psycopg.connect(**CONEXION, dbname=BASE, connect_timeout=5) as conn:
        conn.execute(sql)
        for tabla in TABLAS_BD:
            filas = conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
            print(f"  {tabla:<24} {filas:>5} filas")
    print(f"Listo: postgresql://{CONEXION['user']}@{CONEXION['host']}:{CONEXION['port']}/{BASE}")


if __name__ == "__main__":
    main()
