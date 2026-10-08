"""
Crea la base PostgreSQL "tablaspollet" y carga los datos ORIGINALES (sin limpiar).

Las tablas guardan cada columna como TEXT para conservar los valores tal como vienen
(espacios sobrantes, RUT en formatos mixtos, estados con mayúsculas inconsistentes).
La API (api/servidor.py) lee estas tablas y les aplica el mismo ETL que a las otras fuentes.

Genera además bd/tablaspollet_original.sql, que se puede cargar a mano con psql o pgAdmin.

Uso:
    python bd/crear_bd.py
Variables de entorno opcionales: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import psycopg

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))

from procesar_excel import (  # noqa: E402
    ARCHIVOS_ORIGINALES, CARPETA_ORIGINAL, COLUMNAS_FUENTE, EXCEL, HOJAS_EXCEL, a_texto_crudo,
)

CONEXION = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5432")),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "123456"),
}
BASE = os.getenv("PGDATABASE", "tablaspollet")
SCRIPT_SQL = Path(__file__).with_name("tablaspollet_original.sql")

# Tabla en PostgreSQL -> (clave de COLUMNAS_FUENTE, descripción)
TABLAS_BD = {
    "clientes_cartera": ("cartera", "Cartera financiera original (Dataset_1000_Clientes_Chile)."),
    "maestro_clientes": ("clientes", "Maestro de clientes comerciales original (Tabla2)."),
    "maestro_productos": ("productos", "Maestro de productos original (Tabla3)."),
    "maestro_sucursales": ("sucursales", "Maestro de sucursales original (Tabla4)."),
    "ventas_transacciones": ("ventas", "Transacciones de venta originales (Tabla1)."),
    "productos_cartera": ("producto_cartera",
                          "Producto financiero por cliente. Complemento tomado de Dataset_limpio_A2, "
                          "porque los CSV originales no traen esta columna."),
}


def leer_fuentes() -> dict[str, pd.DataFrame]:
    datos = {}
    for clave, patron in ARCHIVOS_ORIGINALES.items():
        archivo = sorted(CARPETA_ORIGINAL.glob(patron))[0]
        datos[clave] = pd.read_csv(archivo, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    a2 = pd.read_excel(EXCEL, sheet_name=HOJAS_EXCEL["producto_cartera"], dtype=object)
    a2.columns = [str(c).strip() for c in a2.columns]
    datos["producto_cartera"] = a2[COLUMNAS_FUENTE["producto_cartera"]].dropna(how="all")
    return datos


def literal(valor) -> str:
    return "'" + a_texto_crudo(valor).replace("'", "''") + "'"


def generar_sql(datos: dict[str, pd.DataFrame]) -> str:
    partes = ["-- Base tablaspollet: datos originales sin limpiar.", "SET client_encoding = 'UTF8';", "BEGIN;"]
    for tabla, (clave, descripcion) in TABLAS_BD.items():
        columnas = [c.lower() for c in COLUMNAS_FUENTE[clave]]
        partes.append(f"DROP TABLE IF EXISTS {tabla};")
        partes.append(f"CREATE TABLE {tabla} (\n" + ",\n".join(f"    {c} TEXT" for c in columnas) + "\n);")
        partes.append(f"COMMENT ON TABLE {tabla} IS {literal(descripcion)};")
        df = datos[clave][COLUMNAS_FUENTE[clave]]
        valores = ",\n".join("    (" + ", ".join(literal(v) for v in fila) + ")"
                             for fila in df.itertuples(index=False, name=None))
        partes.append(f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES\n{valores};")
    partes.append("COMMIT;")
    return "\n\n".join(partes) + "\n"


def crear_base() -> None:
    with psycopg.connect(**CONEXION, dbname="postgres", autocommit=True, connect_timeout=5) as conn:
        existe = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (BASE,)).fetchone()
        if not existe:
            conn.execute(f'CREATE DATABASE "{BASE}" ENCODING \'UTF8\' TEMPLATE template0')
            print(f"Base {BASE} creada.")
        else:
            print(f"Base {BASE} ya existe: se recrean sus tablas.")


def main() -> None:
    datos = leer_fuentes()
    sql = generar_sql(datos)
    SCRIPT_SQL.write_text(sql, encoding="utf-8")
    print(f"Script generado: {SCRIPT_SQL.name}")

    crear_base()
    with psycopg.connect(**CONEXION, dbname=BASE, connect_timeout=5) as conn:
        conn.execute(sql)
        for tabla in TABLAS_BD:
            filas = conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
            print(f"  {tabla:<22} {filas:>5} filas")
    print(f"Listo: postgresql://{CONEXION['user']}@{CONEXION['host']}:{CONEXION['port']}/{BASE}")


if __name__ == "__main__":
    main()
