"""
Verifica que la base "tablaspollet" contenga exactamente los datos de los CSV originales.

Lee cada CSV de web/data/dataoriginal tal cual (texto, sin limpiar) y lo compara con su tabla
en PostgreSQL: cantidad de filas, columnas y cada celda, incluidos espacios y mayúsculas.
La tabla productos_cartera se compara con la hoja Dataset_limpio_A2 del Excel, que es su origen.

Uso:
    python bd/verificar_bd.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "etl"))

from crear_bd import BASE, CONEXION, TABLAS_BD  # noqa: E402
from procesar_excel import (  # noqa: E402
    ARCHIVOS_ORIGINALES, CARPETA_ORIGINAL, COLUMNAS_FUENTE, EXCEL, HOJAS_EXCEL, a_texto_crudo,
)


def leer_origen(clave: str) -> tuple[str, pd.DataFrame]:
    if clave == "producto_cartera":
        df = pd.read_excel(EXCEL, sheet_name=HOJAS_EXCEL[clave], dtype=object).dropna(how="all")
        df.columns = [str(c).strip() for c in df.columns]
        return f"{EXCEL.name} › {HOJAS_EXCEL[clave]}", df.map(a_texto_crudo)
    archivo = sorted(CARPETA_ORIGINAL.glob(ARCHIVOS_ORIGINALES[clave]))[0]
    return archivo.name, pd.read_csv(archivo, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def ordenar(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(list(df.columns), kind="stable").reset_index(drop=True)


def comparar(conn: psycopg.Connection, tabla: str, clave: str) -> bool:
    nombre, origen = leer_origen(clave)
    columnas = COLUMNAS_FUENTE[clave]
    extra = [c for c in origen.columns if c not in columnas]
    filas_bd = conn.execute(f"SELECT {', '.join(c.lower() for c in columnas)} FROM {tabla}").fetchall()
    bd = pd.DataFrame(filas_bd, columns=columnas, dtype=object).fillna("")

    print(f"\n{tabla}  <-  {nombre}")
    print(f"  filas: origen {len(origen)} · base {len(bd)}")
    if extra:
        print(f"  columnas del origen que no se cargan (el ETL no las usa): {extra}")

    ok = len(origen) == len(bd)
    if ok:
        a, b = ordenar(origen[columnas]), ordenar(bd)
        distintas = (a != b)
        n = int(distintas.to_numpy().sum())
        if n:
            ok = False
            print(f"  {n} celdas distintas. Ejemplos:")
            for fila, col in list(zip(*distintas.to_numpy().nonzero()))[:5]:
                print(f"    {columnas[col]}: origen {a.iat[fila, col]!r} · base {b.iat[fila, col]!r}")
        con_espacios = int(a.map(lambda v: v != v.strip()).to_numpy().sum())
        print(f"  {len(columnas)} columnas · {a.size} celdas idénticas · {con_espacios} con espacios sobrantes conservados")
    print("  OK" if ok else "  DIFERENTE")
    return ok


def main() -> None:
    with psycopg.connect(**CONEXION, dbname=BASE, connect_timeout=5) as conn:
        resultados = [comparar(conn, tabla, clave) for tabla, (clave, _) in TABLAS_BD.items()]
    print("\nResultado:", "la base coincide con los datos originales." if all(resultados)
          else "hay diferencias con los datos originales.")
    sys.exit(0 if all(resultados) else 1)


if __name__ == "__main__":
    main()
