"""
Proceso ETL: aplica la misma limpieza y normalización a dos fuentes de datos y las exporta a CSV.

Fuentes:
  - excel    : "Actividad 1 - Lavadero de datos.xlsx" (datos ya trabajados a mano).
                 "28.9 Dataset_limpio_A1"  cartera de clientes financieros.
                 "Dataset_limpio_A2"       solo la columna Producto.
                 "2.10 Clientes", "2.10_Productos", "2.10_A3_Sucursales", "2.10_A3_Ventas".
  - original : CSV originales, antes de cualquier limpieza (web/data/dataoriginal).
                 Dataset_1000_Clientes_*.csv, Tabla1_Ventas_*, Tabla2_*Clientes*, Tabla3_*Productos*,
                 Tabla4_*Sucursales*. No trae Producto: se asocia desde Dataset_limpio_A2 por ID_Cliente.

Hojas ignoradas del Excel: tablas dinámicas, "Hoja 10", "Consulta_Dinamica" y "Bitácora Higiene".

Salida: web/data/<fuente>/*.csv + modelo.json, y web/data/fuentes.json con el listado de fuentes.

Uso:
    python etl/procesar_excel.py
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
import warnings
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

RAIZ = Path(__file__).resolve().parent.parent
EXCEL = RAIZ / "Actividad 1 - Lavadero de datos.xlsx"
CARPETA_ORIGINAL = RAIZ / "web" / "data" / "dataoriginal"
SALIDA = RAIZ / "web" / "data"

COLUMNAS_FUENTE = {
    "cartera": ["ID_Cliente", "RUT_Cliente", "Nombre_Completo", "Email_Contacto", "Comuna", "Ciudad",
                "Region", "Monto_Facturado_CLP", "Dias_Morosidad", "Estado_Cuenta", "Fecha_Registro"],
    "producto_cartera": ["ID_Cliente", "Producto"],
    "clientes": ["ID_Cliente", "RUT_Cliente", "Nombre_Cliente", "Segmento", "Comuna", "Ciudad", "Region"],
    "productos": ["ID_Producto", "Nombre_Producto", "Categoria", "Precio_Unitario_CLP"],
    "sucursales": ["ID_Sucursal", "Nombre_Sucursal", "Comuna", "Ciudad", "Zona_Comercial"],
    "ventas": ["ID_Transaccion", "Fecha_Venta", "ID_Cliente", "ID_Producto", "ID_Sucursal", "Cantidad"],
}

HOJAS_EXCEL = {
    "cartera": "28.9 Dataset_limpio_A1",
    "producto_cartera": "Dataset_limpio_A2",
    "clientes": "2.10 Clientes",
    "productos": "2.10_Productos",
    "sucursales": "2.10_A3_Sucursales",
    "ventas": "2.10_A3_Ventas",
}

ARCHIVOS_ORIGINALES = {
    "cartera": "Dataset_*Clientes*.csv",
    "clientes": "Tabla2_*Clientes*.csv",
    "productos": "Tabla3_*Productos*.csv",
    "sucursales": "Tabla4_*Sucursales*.csv",
    "ventas": "Tabla1_*Ventas*.csv",
}

# Regiones de ciudades que solo aparecen en Sucursales (las fuentes no traen la región).
REGION_POR_CIUDAD_EXTRA = {
    "Rancagua": "Región del Libertador General Bernardo O'Higgins",
    "Iquique": "Región de Tarapacá",
}

ESTADOS_CANONICOS = {"activo": "Activo", "en revision": "En Revisión", "inactivo": "Inactivo"}

PARTICULAS_MINUSCULA = {"de", "del", "la", "las", "los", "y"}

calidad: list[dict] = []


def registrar(tabla: str, columna: str, problema: str, afectados: int, accion: str) -> None:
    calidad.append(
        {"tabla": tabla, "columna": columna, "problema": problema,
         "filas_afectadas": int(afectados), "accion": accion}
    )


# ---------------------------------------------------------------- utilidades de limpieza

def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def limpiar_espacios(valor) -> str | None:
    if valor is None or pd.isna(valor):
        return None
    texto = re.sub(r"\s+", " ", str(valor)).strip()
    return texto or None


def nombre_propio(valor) -> str | None:
    texto = limpiar_espacios(valor)
    if texto is None:
        return None
    palabras = texto.lower().split(" ")
    return " ".join(
        p if (i > 0 and p in PARTICULAS_MINUSCULA) else p[:1].upper() + p[1:]
        for i, p in enumerate(palabras)
    )


def digito_verificador(cuerpo: str) -> str:
    suma = sum(int(d) * f for d, f in zip(reversed(cuerpo), [2, 3, 4, 5, 6, 7] * 3))
    resto = 11 - suma % 11
    return {11: "0", 10: "K"}.get(resto, str(resto))


def normalizar_rut(valor) -> tuple[str | None, bool]:
    """Devuelve (RUT con formato 12.345.678-9, dígito verificador válido)."""
    texto = limpiar_espacios(valor)
    if texto is None:
        return None, False
    limpio = re.sub(r"[^0-9kK]", "", texto).upper()
    if len(limpio) < 2 or not limpio[:-1].isdigit():
        return None, False
    cuerpo, dv = limpio[:-1], limpio[-1]
    formateado = f"{int(cuerpo):,}".replace(",", ".") + "-" + dv
    return formateado, digito_verificador(cuerpo) == dv


def normalizar_email(valor) -> tuple[str | None, bool]:
    """Devuelve (email en minúsculas y sin tildes, si cumple formato)."""
    texto = limpiar_espacios(valor)
    if texto is None:
        return None, False
    email = sin_tildes(texto.lower()).replace("ñ", "n")
    return email, bool(re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", email))


def normalizar_estado(valor) -> str | None:
    texto = limpiar_espacios(valor)
    if texto is None:
        return None
    return ESTADOS_CANONICOS.get(sin_tildes(texto.lower()), nombre_propio(texto))


def fecha_iso(valor) -> str | None:
    if isinstance(valor, (datetime, pd.Timestamp)):
        return valor.strftime("%Y-%m-%d")
    texto = limpiar_espacios(valor)
    if texto is None:
        return None
    fecha = pd.to_datetime(texto, errors="coerce", dayfirst=not re.match(r"\d{4}-", texto))
    return None if pd.isna(fecha) else fecha.strftime("%Y-%m-%d")


def a_entero(serie: pd.Series) -> pd.Series:
    return pd.to_numeric(serie.map(limpiar_espacios), errors="coerce").round().astype("Int64")


def a_texto_crudo(valor) -> str:
    """Representación de un valor de origen sin limpiarlo (solo legibilidad de fechas y números)."""
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return ""
    if isinstance(valor, (datetime, pd.Timestamp)):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def nivel_riesgo(dias_morosidad, estado) -> str:
    """Misma regla de negocio que la fórmula IFS de la hoja Dataset_limpio_A2."""
    if pd.isna(dias_morosidad) or estado is None:
        return "Sin Dato"
    if dias_morosidad > 60 and estado == "Activo":
        return "Riesgo Alto"
    if dias_morosidad > 30 or estado == "En Revisión":
        return "Riesgo Medio"
    return "Riesgo Bajo"


def contar_cambios(antes: pd.Series, despues: pd.Series) -> int:
    return int((antes.map(a_texto_crudo) != despues.astype("string").fillna("")).sum())


def catalogo(valores: pd.Series, prefijo: str, col_id: str, col_nombre: str) -> pd.DataFrame:
    unicos = sorted(v for v in valores.dropna().unique())
    return pd.DataFrame({col_id: [f"{prefijo}-{i:02d}" for i in range(1, len(unicos) + 1)],
                         col_nombre: unicos})


def deduplicar(df: pd.DataFrame, claves: list[str], tabla: str) -> pd.DataFrame:
    duplicados = df.duplicated(subset=claves, keep="first")
    registrar(tabla, ", ".join(claves), "Registros duplicados", duplicados.sum(),
              "Se conserva la primera aparición")
    return df[~duplicados].reset_index(drop=True)


# ---------------------------------------------------------------- carga de fuentes

def _seleccionar(df: pd.DataFrame, clave: str, origen: str) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    faltantes = set(COLUMNAS_FUENTE[clave]) - set(df.columns)
    if faltantes:
        raise ValueError(f"{origen}: faltan columnas {sorted(faltantes)}")
    df = df[COLUMNAS_FUENTE[clave]]
    vacias = df.map(lambda v: a_texto_crudo(v).strip() == "").all(axis=1)
    return df[~vacias].reset_index(drop=True)


def cargar_excel(excel: Path) -> dict[str, pd.DataFrame]:
    return {clave: _seleccionar(pd.read_excel(excel, sheet_name=hoja, dtype=object), clave, hoja)
            for clave, hoja in HOJAS_EXCEL.items()}


def cargar_originales(carpeta: Path, excel: Path) -> dict[str, pd.DataFrame]:
    datos = {}
    for clave, patron in ARCHIVOS_ORIGINALES.items():
        archivos = sorted(carpeta.glob(patron))
        if not archivos:
            raise FileNotFoundError(f"No se encontró {patron} en {carpeta}")
        df = pd.read_csv(archivos[0], dtype=str, keep_default_na=False, encoding="utf-8-sig")
        datos[clave] = _seleccionar(df, clave, archivos[0].name)
    datos["producto_cartera"] = _seleccionar(
        pd.read_excel(excel, sheet_name=HOJAS_EXCEL["producto_cartera"], dtype=object), "producto_cartera",
        HOJAS_EXCEL["producto_cartera"])
    registrar("cartera_clientes", "producto_financiero",
              "La fuente original no trae la columna Producto",
              len(datos["cartera"]), "Se asocia desde Dataset_limpio_A2 por ID_Cliente")
    return datos


# ---------------------------------------------------------------- etapas del proceso

def limpiar_cartera(fuente: dict[str, pd.DataFrame]) -> pd.DataFrame:
    t = "cartera_clientes"
    a1 = fuente["cartera"]

    df = pd.DataFrame()
    df["id_cliente"] = a1["ID_Cliente"].map(limpiar_espacios).str.upper()

    rut = a1["RUT_Cliente"].map(normalizar_rut)
    df["rut"] = rut.str[0]
    df["rut_dv_valido"] = rut.str[1]
    registrar(t, "rut", "RUT en formatos mixtos (con/sin puntos o con espacios)",
              contar_cambios(a1["RUT_Cliente"], df["rut"]), "Formato único 12.345.678-9")
    registrar(t, "rut", "Dígito verificador no coincide con el cálculo módulo 11",
              (~df["rut_dv_valido"]).sum(), "Se conserva y se marca rut_dv_valido = False")

    df["nombre"] = a1["Nombre_Completo"].map(nombre_propio)
    registrar(t, "nombre", "Nombres en MAYÚSCULAS / minúsculas / con espacios sobrantes",
              contar_cambios(a1["Nombre_Completo"], df["nombre"]), "Nombre propio y TRIM")

    email = a1["Email_Contacto"].map(normalizar_email)
    df["email"] = email.str[0]
    df["email_valido"] = email.str[1]
    registrar(t, "email", "Emails con tildes o ñ (no válidos en la parte local)",
              contar_cambios(a1["Email_Contacto"], df["email"]), "Se eliminan tildes y ñ -> n")

    df["comuna"] = a1["Comuna"].map(nombre_propio)
    df["ciudad"] = a1["Ciudad"].map(nombre_propio)
    df["region"] = a1["Region"].map(limpiar_espacios)
    df["monto_facturado_clp"] = a_entero(a1["Monto_Facturado_CLP"])
    df["dias_morosidad"] = a_entero(a1["Dias_Morosidad"])

    df["estado_cuenta"] = a1["Estado_Cuenta"].map(normalizar_estado)
    registrar(t, "estado_cuenta", "Estados con mayúsculas inconsistentes (activo, EN REVISIÓN)",
              contar_cambios(a1["Estado_Cuenta"], df["estado_cuenta"]), "Catálogo de 3 estados")

    df["fecha_registro"] = a1["Fecha_Registro"].map(fecha_iso)

    a2 = fuente["producto_cartera"]
    productos = (a2.assign(ID_Cliente=a2["ID_Cliente"].map(limpiar_espacios).str.upper())
                 .drop_duplicates("ID_Cliente").set_index("ID_Cliente")["Producto"].map(limpiar_espacios))
    df["producto_financiero"] = df["id_cliente"].map(productos)
    registrar(t, "producto_financiero", "Cliente sin producto asociado en Dataset_limpio_A2",
              df["producto_financiero"].isna().sum(), "Se deja la FK vacía (\"Sin producto\")")

    nulos = df[["id_cliente", "rut", "nombre"]].isna().any(axis=1)
    registrar(t, "id_cliente, rut, nombre", "Campos obligatorios vacíos", nulos.sum(), "Fila descartada")
    df = df[~nulos]
    df = deduplicar(df, ["rut"], t)
    registrar(t, "nombre", "Nombres repetidos con RUT distinto (homónimos)",
              df["nombre"].duplicated(keep=False).sum(), "Informativo, no son duplicados: se conservan")
    return df


def limpiar_comercial(fuente: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, ...]:
    c = fuente["clientes"]
    clientes = pd.DataFrame()
    clientes["id_cliente"] = c["ID_Cliente"].map(limpiar_espacios).str.upper()
    rut = c["RUT_Cliente"].map(normalizar_rut)
    clientes["rut"] = rut.str[0]
    clientes["rut_dv_valido"] = rut.str[1]
    registrar("clientes", "rut", "Dígito verificador no coincide con el cálculo módulo 11",
              (~clientes["rut_dv_valido"]).sum(), "Se conserva y se marca rut_dv_valido = False")
    clientes["nombre"] = c["Nombre_Cliente"].map(nombre_propio)
    registrar("clientes", "nombre", "Nombres en MAYÚSCULAS / minúsculas / con espacios sobrantes",
              contar_cambios(c["Nombre_Cliente"], clientes["nombre"]), "Nombre propio y TRIM")
    clientes["segmento"] = c["Segmento"].map(limpiar_espacios)
    clientes["comuna"] = c["Comuna"].map(nombre_propio)
    clientes["ciudad"] = c["Ciudad"].map(nombre_propio)
    clientes["region"] = c["Region"].map(limpiar_espacios)
    clientes = deduplicar(clientes, ["id_cliente"], "clientes")

    p = fuente["productos"]
    productos = pd.DataFrame({
        "id_producto": p["ID_Producto"].map(limpiar_espacios).str.upper(),
        "nombre": p["Nombre_Producto"].map(limpiar_espacios),
        "categoria": p["Categoria"].map(nombre_propio),
        "precio_unitario_clp": a_entero(p["Precio_Unitario_CLP"]),
    })
    productos = deduplicar(productos, ["id_producto"], "productos")

    s = fuente["sucursales"]
    sucursales = pd.DataFrame({
        "id_sucursal": s["ID_Sucursal"].map(limpiar_espacios).str.upper(),
        "nombre": s["Nombre_Sucursal"].map(limpiar_espacios),
        "comuna": s["Comuna"].map(nombre_propio),
        "ciudad": s["Ciudad"].map(nombre_propio),
        "zona_comercial": s["Zona_Comercial"].map(limpiar_espacios),
    })
    sucursales = deduplicar(sucursales, ["id_sucursal"], "sucursales")

    v = fuente["ventas"]
    ventas = pd.DataFrame({
        "id_transaccion": v["ID_Transaccion"].map(limpiar_espacios).str.upper(),
        "fecha_venta": v["Fecha_Venta"].map(fecha_iso),
        "id_cliente": v["ID_Cliente"].map(limpiar_espacios).str.upper(),
        "id_producto": v["ID_Producto"].map(limpiar_espacios).str.upper(),
        "id_sucursal": v["ID_Sucursal"].map(limpiar_espacios).str.upper(),
        "cantidad": a_entero(v["Cantidad"]),
    })
    ventas = deduplicar(ventas, ["id_transaccion"], "ventas")
    return clientes, productos, sucursales, ventas


def construir_geografia(*fuentes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    geo = pd.concat([f.reindex(columns=["comuna", "ciudad", "region"]) for f in fuentes], ignore_index=True)
    region_por_ciudad = geo.dropna(subset=["region"]).drop_duplicates("ciudad").set_index("ciudad")["region"].to_dict()
    region_por_ciudad = {**REGION_POR_CIUDAD_EXTRA, **region_por_ciudad}
    sin_region = geo["region"].isna()
    geo.loc[sin_region, "region"] = geo.loc[sin_region, "ciudad"].map(region_por_ciudad)
    geo = geo.drop_duplicates().dropna()

    inconsistentes = geo.groupby("comuna")["ciudad"].nunique()
    registrar("comunas", "comuna", "Comuna asociada a más de una ciudad",
              (inconsistentes > 1).sum(), "Se usa la primera asociación")
    geo = geo.drop_duplicates("comuna")

    regiones = catalogo(geo["region"], "REG", "id_region", "nombre")
    ciudades = geo[["ciudad", "region"]].drop_duplicates("ciudad").sort_values("ciudad").reset_index(drop=True)
    ciudades.insert(0, "id_ciudad", [f"CIU-{i:02d}" for i in range(1, len(ciudades) + 1)])
    ciudades["id_region"] = ciudades["region"].map(regiones.set_index("nombre")["id_region"])
    ciudades = ciudades.rename(columns={"ciudad": "nombre"})[["id_ciudad", "nombre", "id_region"]]

    comunas = geo[["comuna", "ciudad"]].sort_values("comuna").reset_index(drop=True)
    comunas.insert(0, "id_comuna", [f"COM-{i:02d}" for i in range(1, len(comunas) + 1)])
    comunas["id_ciudad"] = comunas["ciudad"].map(ciudades.set_index("nombre")["id_ciudad"])
    comunas = comunas.rename(columns={"comuna": "nombre"})[["id_comuna", "nombre", "id_ciudad"]]
    return regiones, ciudades, comunas


def validar_fk(df: pd.DataFrame, col: str, referencia: pd.Series, tabla: str, tabla_ref: str) -> pd.Series:
    huerfanas = df[col].notna() & ~df[col].isin(set(referencia))
    if huerfanas.any():
        valores = ", ".join(sorted(df.loc[huerfanas, col].unique()))
        registrar(tabla, col, f"Clave foránea sin registro en {tabla_ref} ({valores})",
                  huerfanas.sum(), "Fila movida a ventas_rechazadas")
    return huerfanas


# ---------------------------------------------------------------- modelo de datos (metadatos)

def col(nombre, tipo, descripcion="", pk=False, fk=None):
    return {"nombre": nombre, "tipo": tipo, "descripcion": descripcion, "pk": pk, "fk": fk}


MODELO = {
    "regiones": {"dominio": "Geografía", "descripcion": "Regiones de Chile.", "columnas": [
        col("id_region", "texto", "Identificador de región", pk=True),
        col("nombre", "texto", "Nombre oficial de la región")]},
    "ciudades": {"dominio": "Geografía", "descripcion": "Ciudades agrupadas por región.", "columnas": [
        col("id_ciudad", "texto", "Identificador de ciudad", pk=True),
        col("nombre", "texto", "Nombre de la ciudad"),
        col("id_region", "texto", "Región a la que pertenece", fk="regiones.id_region")]},
    "comunas": {"dominio": "Geografía", "descripcion": "Comunas agrupadas por ciudad.", "columnas": [
        col("id_comuna", "texto", "Identificador de comuna", pk=True),
        col("nombre", "texto", "Nombre de la comuna"),
        col("id_ciudad", "texto", "Ciudad a la que pertenece", fk="ciudades.id_ciudad")]},

    "estados_cuenta": {"dominio": "Cartera financiera", "descripcion": "Catálogo de estados de cuenta.", "columnas": [
        col("id_estado", "texto", "Identificador de estado", pk=True),
        col("nombre", "texto", "Activo / En Revisión / Inactivo")]},
    "productos_financieros": {"dominio": "Cartera financiera", "descripcion": "Productos financieros contratados.", "columnas": [
        col("id_producto_financiero", "texto", "Identificador del producto", pk=True),
        col("nombre", "texto", "Nombre del producto")]},
    "cartera_clientes": {"dominio": "Cartera financiera", "descripcion": "Clientes de la cartera financiera.", "columnas": [
        col("id_cliente", "texto", "Identificador del cliente (CLI-0000)", pk=True),
        col("rut", "texto", "RUT normalizado 12.345.678-9"),
        col("rut_dv_valido", "booleano", "Dígito verificador correcto según módulo 11"),
        col("nombre", "texto", "Nombre completo en formato nombre propio"),
        col("email", "texto", "Email en minúsculas y sin tildes"),
        col("email_valido", "booleano", "Cumple formato de email"),
        col("id_comuna", "texto", "Comuna de residencia", fk="comunas.id_comuna"),
        col("fecha_registro", "fecha", "Fecha de registro (AAAA-MM-DD)")]},
    "cuentas": {"dominio": "Cartera financiera", "descripcion": "Situación financiera de cada cliente.", "columnas": [
        col("id_cuenta", "texto", "Identificador de cuenta", pk=True),
        col("id_cliente", "texto", "Titular de la cuenta", fk="cartera_clientes.id_cliente"),
        col("id_producto_financiero", "texto", "Producto contratado", fk="productos_financieros.id_producto_financiero"),
        col("id_estado", "texto", "Estado de la cuenta", fk="estados_cuenta.id_estado"),
        col("monto_facturado_clp", "entero", "Monto facturado en pesos chilenos"),
        col("dias_morosidad", "entero", "Días de morosidad"),
        col("nivel_riesgo", "texto", "Derivado: Alto (>60 días y Activo), Medio (>30 días o En Revisión), Bajo")]},

    "segmentos": {"dominio": "Comercial", "descripcion": "Segmentos de cliente.", "columnas": [
        col("id_segmento", "texto", "Identificador de segmento", pk=True),
        col("nombre", "texto", "VIP / Corporativo / Pyme / Persona")]},
    "clientes": {"dominio": "Comercial", "descripcion": "Clientes del módulo de ventas.", "columnas": [
        col("id_cliente", "texto", "Identificador del cliente (CLI-000)", pk=True),
        col("rut", "texto", "RUT normalizado 12.345.678-9"),
        col("rut_dv_valido", "booleano", "Dígito verificador correcto según módulo 11"),
        col("nombre", "texto", "Nombre completo"),
        col("id_segmento", "texto", "Segmento comercial", fk="segmentos.id_segmento"),
        col("id_comuna", "texto", "Comuna del cliente", fk="comunas.id_comuna")]},
    "categorias": {"dominio": "Comercial", "descripcion": "Categorías de producto.", "columnas": [
        col("id_categoria", "texto", "Identificador de categoría", pk=True),
        col("nombre", "texto", "Nombre de la categoría")]},
    "productos": {"dominio": "Comercial", "descripcion": "Catálogo de productos y servicios.", "columnas": [
        col("id_producto", "texto", "Identificador del producto", pk=True),
        col("nombre", "texto", "Nombre del producto"),
        col("id_categoria", "texto", "Categoría", fk="categorias.id_categoria"),
        col("precio_unitario_clp", "entero", "Precio de lista en CLP")]},
    "zonas_comerciales": {"dominio": "Comercial", "descripcion": "Zonas comerciales de las sucursales.", "columnas": [
        col("id_zona", "texto", "Identificador de zona", pk=True),
        col("nombre", "texto", "Nombre de la zona")]},
    "sucursales": {"dominio": "Comercial", "descripcion": "Sucursales de venta.", "columnas": [
        col("id_sucursal", "texto", "Identificador de sucursal", pk=True),
        col("nombre", "texto", "Nombre de la sucursal"),
        col("id_comuna", "texto", "Comuna de la sucursal", fk="comunas.id_comuna"),
        col("id_zona", "texto", "Zona comercial", fk="zonas_comerciales.id_zona")]},
    "ventas": {"dominio": "Comercial", "descripcion": "Transacciones de venta válidas (tabla de hechos).", "columnas": [
        col("id_transaccion", "texto", "Identificador de la transacción", pk=True),
        col("fecha_venta", "fecha", "Fecha de la venta (AAAA-MM-DD)"),
        col("id_cliente", "texto", "Cliente comprador", fk="clientes.id_cliente"),
        col("id_producto", "texto", "Producto vendido", fk="productos.id_producto"),
        col("id_sucursal", "texto", "Sucursal de la venta", fk="sucursales.id_sucursal"),
        col("cantidad", "entero", "Unidades vendidas"),
        col("precio_unitario_clp", "entero", "Precio unitario al momento de la venta"),
        col("monto_total_clp", "entero", "cantidad x precio_unitario_clp")]},
    "ventas_rechazadas": {"dominio": "Control de calidad", "descripcion": "Ventas con claves foráneas inexistentes (cuarentena).", "columnas": [
        col("id_transaccion", "texto", "Identificador de la transacción", pk=True),
        col("fecha_venta", "fecha", "Fecha de la venta"),
        col("id_cliente", "texto", "Cliente informado"),
        col("id_producto", "texto", "Producto informado"),
        col("id_sucursal", "texto", "Sucursal informada"),
        col("cantidad", "entero", "Unidades"),
        col("motivo", "texto", "Motivo del rechazo")]},
}

DESCRIPCION_ORIGEN = {
    "cartera": "Cartera financiera tal como viene en la fuente, antes del ETL.",
    "clientes": "Clientes comerciales tal como vienen en la fuente.",
    "productos": "Productos tal como vienen en la fuente.",
    "sucursales": "Sucursales tal como vienen en la fuente.",
    "ventas": "Ventas tal como vienen en la fuente.",
}


# ---------------------------------------------------------------- orquestación

def procesar(fuente: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], dict]:
    cartera = limpiar_cartera(fuente)
    clientes, productos, sucursales, ventas = limpiar_comercial(fuente)

    regiones, ciudades, comunas = construir_geografia(cartera, clientes, sucursales)
    id_comuna = comunas.set_index("nombre")["id_comuna"]

    estados = catalogo(cartera["estado_cuenta"], "EST", "id_estado", "nombre")
    prod_fin = catalogo(cartera["producto_financiero"], "PF", "id_producto_financiero", "nombre")

    cuentas = pd.DataFrame({
        "id_cuenta": [f"CTA-{i:04d}" for i in range(1, len(cartera) + 1)],
        "id_cliente": cartera["id_cliente"],
        "id_producto_financiero": cartera["producto_financiero"].map(prod_fin.set_index("nombre")["id_producto_financiero"]),
        "id_estado": cartera["estado_cuenta"].map(estados.set_index("nombre")["id_estado"]),
        "monto_facturado_clp": cartera["monto_facturado_clp"],
        "dias_morosidad": cartera["dias_morosidad"],
        "nivel_riesgo": [nivel_riesgo(d, e) for d, e in zip(cartera["dias_morosidad"], cartera["estado_cuenta"])],
    })
    cartera_clientes = cartera.assign(id_comuna=cartera["comuna"].map(id_comuna))[
        ["id_cliente", "rut", "rut_dv_valido", "nombre", "email", "email_valido", "id_comuna", "fecha_registro"]]

    segmentos = catalogo(clientes["segmento"], "SEG", "id_segmento", "nombre")
    clientes_out = clientes.assign(
        id_segmento=clientes["segmento"].map(segmentos.set_index("nombre")["id_segmento"]),
        id_comuna=clientes["comuna"].map(id_comuna),
    )[["id_cliente", "rut", "rut_dv_valido", "nombre", "id_segmento", "id_comuna"]]

    categorias = catalogo(productos["categoria"], "CAT", "id_categoria", "nombre")
    productos_out = productos.assign(
        id_categoria=productos["categoria"].map(categorias.set_index("nombre")["id_categoria"])
    )[["id_producto", "nombre", "id_categoria", "precio_unitario_clp"]]

    zonas = catalogo(sucursales["zona_comercial"], "ZON", "id_zona", "nombre")
    sucursales_out = sucursales.assign(
        id_comuna=sucursales["comuna"].map(id_comuna),
        id_zona=sucursales["zona_comercial"].map(zonas.set_index("nombre")["id_zona"]),
    )[["id_sucursal", "nombre", "id_comuna", "id_zona"]]

    motivos = pd.Series("", index=ventas.index)
    for columna, ref, nombre_ref in [("id_cliente", clientes_out["id_cliente"], "clientes"),
                                     ("id_producto", productos_out["id_producto"], "productos"),
                                     ("id_sucursal", sucursales_out["id_sucursal"], "sucursales")]:
        mask = validar_fk(ventas, columna, ref, "ventas", nombre_ref)
        motivos[mask] += ventas.loc[mask, columna] + f" no existe en {nombre_ref}; "
    rechazadas = ventas[motivos != ""].assign(motivo=motivos[motivos != ""].str.rstrip("; "))
    ventas_ok = ventas[motivos == ""].copy()
    precio = productos_out.set_index("id_producto")["precio_unitario_clp"]
    ventas_ok["precio_unitario_clp"] = ventas_ok["id_producto"].map(precio).astype("Int64")
    ventas_ok["monto_total_clp"] = (ventas_ok["cantidad"] * ventas_ok["precio_unitario_clp"]).astype("Int64")
    ventas_ok = ventas_ok.sort_values(["fecha_venta", "id_transaccion"])

    sin_ventas = (~clientes_out["id_cliente"].isin(ventas_ok["id_cliente"])).sum()
    registrar("clientes", "id_cliente", "Clientes sin ventas registradas", sin_ventas, "Informativo, se conservan")

    tablas = {
        "regiones": regiones, "ciudades": ciudades, "comunas": comunas,
        "estados_cuenta": estados, "productos_financieros": prod_fin,
        "cartera_clientes": cartera_clientes, "cuentas": cuentas,
        "segmentos": segmentos, "clientes": clientes_out, "categorias": categorias,
        "productos": productos_out, "zonas_comerciales": zonas, "sucursales": sucursales_out,
        "ventas": ventas_ok, "ventas_rechazadas": rechazadas,
    }
    modelo = copy.deepcopy(MODELO)

    for nombre, meta in modelo.items():
        esperadas = [c["nombre"] for c in meta["columnas"]]
        assert list(tablas[nombre].columns) == esperadas, f"{nombre}: {list(tablas[nombre].columns)} != {esperadas}"
        for c in meta["columnas"]:
            if c["fk"]:
                tabla_ref, col_ref = c["fk"].split(".")
                huerfanas = ~tablas[nombre][c["nombre"]].dropna().isin(set(tablas[tabla_ref][col_ref]))
                assert not huerfanas.any(), f"Integridad referencial rota en {nombre}.{c['nombre']}"

    for clave, descripcion in DESCRIPCION_ORIGEN.items():
        nombre = f"origen_{clave}"
        tablas[nombre] = fuente[clave].map(a_texto_crudo)
        modelo[nombre] = {"dominio": "Datos de origen", "origen": True, "descripcion": descripcion,
                          "columnas": [col(c, "texto", "Valor sin limpiar") for c in fuente[clave].columns]}
    return tablas, modelo


def exportar(id_fuente: str, nombre: str, descripcion: str, archivo: str,
             cargar: Callable[[], dict[str, pd.DataFrame]]) -> dict:
    calidad.clear()
    fuente = cargar()
    tablas, modelo = procesar(fuente)
    carpeta = SALIDA / id_fuente
    carpeta.mkdir(parents=True, exist_ok=True)
    print(f"[{id_fuente}] {nombre}")
    for tabla, df in tablas.items():
        df.to_csv(carpeta / f"{tabla}.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
        modelo[tabla]["filas"] = len(df)
        print(f"  {tabla + '.csv':<28} {len(df):>5} filas")
    metadatos = {
        "id": id_fuente, "nombre": nombre, "descripcion": descripcion, "fuente": archivo,
        "generado": datetime.now().isoformat(timespec="seconds"),
        "tablas": modelo,
        "calidad": [q for q in calidad if q["filas_afectadas"] > 0],
    }
    (carpeta / "modelo.json").write_text(json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  modelo.json  ({len(metadatos['calidad'])} hallazgos de calidad)")
    return {"id": id_fuente, "nombre": nombre, "descripcion": descripcion, "carpeta": id_fuente}


def main() -> None:
    fuentes = [
        exportar("excel", "Excel trabajado",
                 "Hojas del Excel después de la limpieza manual (Dataset_limpio_A1/A2 y hojas 2.10).",
                 EXCEL.name, lambda: cargar_excel(EXCEL)),
        exportar("original", "CSV originales",
                 "Archivos originales antes de cualquier limpieza (carpeta dataoriginal).",
                 f"{CARPETA_ORIGINAL.name}/*.csv", lambda: cargar_originales(CARPETA_ORIGINAL, EXCEL)),
    ]
    (SALIDA / "fuentes.json").write_text(json.dumps(fuentes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Listo. fuentes.json con {len(fuentes)} fuentes en {SALIDA}")


if __name__ == "__main__":
    main()
