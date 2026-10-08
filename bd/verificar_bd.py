"""
Verifica que la base "tablaspollet" tenga el modelo de la web con los datos de los CSV originales.

  1. Estructura: en public solo están las tablas del modelo (más hallazgos_calidad) y no quedan esquemas extra.
  2. Tablas origen_*: cada celda coincide con el CSV original de web/data/dataoriginal, tal cual (sin limpiar).
  3. Tablas normalizadas y hallazgos: coinciden exactamente con lo que produce el ETL sobre esos CSV.

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

import procesar_excel as etl  # noqa: E402
from crear_bd import BASE, CONEXION, TABLAS_BD, leer_modelo, procesar_originales  # noqa: E402


def como_csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False, lineterminator="\n")


def verificar_estructura(conn: psycopg.Connection) -> bool:
    tablas = {f[0] for f in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    esquemas = [f[0] for f in conn.execute(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT IN ('public', 'information_schema') AND schema_name NOT LIKE 'pg\\_%'")]
    faltan, sobran = set(TABLAS_BD) - tablas, tablas - set(TABLAS_BD)
    fks = conn.execute("SELECT count(*) FROM information_schema.table_constraints "
                       "WHERE table_schema = 'public' AND constraint_type = 'FOREIGN KEY'").fetchone()[0]
    print(f"Estructura: {len(tablas)} tablas en public · {fks} claves foráneas")
    for texto, lista in [("faltan", faltan), ("sobran", sobran), ("esquemas extra", esquemas)]:
        if lista:
            print(f"  {texto}: {sorted(lista)}")
    return not (faltan or sobran or esquemas)


def verificar_origen(bd: dict[str, pd.DataFrame]) -> bool:
    ok = True
    print("\nTablas origen_* contra los CSV originales (celda por celda, sin limpiar):")
    for clave, patron in etl.ARCHIVOS_ORIGINALES.items():
        archivo = sorted(etl.CARPETA_ORIGINAL.glob(patron))[0]
        csv = pd.read_csv(archivo, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        csv.columns = [c.strip() for c in csv.columns]
        columnas = etl.COLUMNAS_FUENTE[clave]
        tabla = bd[f"origen_{clave}"].fillna("")
        distintas = len(csv) != len(tabla) or (csv[columnas].to_numpy(dtype=object) != tabla[columnas].to_numpy()).any()
        espacios = int(tabla.map(lambda v: v != v.strip()).to_numpy().sum())
        extra = [c for c in csv.columns if c not in columnas]
        print(f"  origen_{clave:<12} {len(tabla):>5} filas · {tabla.size:>6} celdas · "
              f"{espacios} con espacios sobrantes conservados · {'DIFERENTE' if distintas else 'OK'}  <- {archivo.name}"
              + (f" (columnas no usadas: {extra})" if extra else ""))
        ok &= not distintas
    return ok


def verificar_modelo(bd: dict[str, pd.DataFrame], hallazgos_bd: list[dict]) -> bool:
    tablas, modelo, hallazgos = procesar_originales()
    ok = True
    print("\nTablas del modelo contra el ETL aplicado a los CSV originales:")
    for nombre, meta in modelo.items():
        if meta.get("origen"):
            continue
        igual = como_csv(bd[nombre]) == como_csv(tablas[nombre])
        print(f"  {nombre:<22} {len(bd[nombre]):>5} filas · {'OK' if igual else 'DIFERENTE'}")
        ok &= igual
    igual = hallazgos_bd == hallazgos
    print(f"  hallazgos_calidad      {len(hallazgos_bd):>5} filas · {'OK' if igual else 'DIFERENTE'}")
    return ok and igual


def main() -> None:
    with psycopg.connect(**CONEXION, dbname=BASE, connect_timeout=5) as conn:
        estructura = verificar_estructura(conn)
        bd, _, hallazgos = leer_modelo(conn)
    resultados = [estructura, verificar_origen(bd), verificar_modelo(bd, hallazgos)]
    print("\nResultado:", "la base tiene el modelo de la web con los datos de los CSV originales."
          if all(resultados) else "hay diferencias.")
    sys.exit(0 if all(resultados) else 1)


if __name__ == "__main__":
    main()
