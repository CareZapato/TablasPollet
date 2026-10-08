"""
Proceso ETL: Excel "Actividad 1 - Lavadero de datos.xlsx" -> CSV normalizados.

Fuentes utilizadas (solo columnas de datos crudos, se ignoran columnas de fórmulas auxiliares):
  - "28.9 Dataset_limpio_A1"  : cartera de clientes financieros (base principal).
  - "Dataset_limpio_A2"       : misma cartera + columna Producto (se toma solo el producto).
  - "2.10 Clientes"           : clientes del módulo comercial.
  - "2.10_Productos"          : catálogo de productos.
  - "2.10_A3_Sucursales"      : sucursales.
  - "2.10_A3_Ventas"          : transacciones de venta.

Hojas ignoradas: tablas dinámicas, "Hoja 10", "Consulta_Dinamica" (son vistas derivadas)
y "Bitácora Higiene" (notas).

Uso:
    python etl/procesar_excel.py [ruta_excel] [carpeta_salida]
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

RAIZ = Path(__file__).resolve().parent.parent
EXCEL_DEFECTO = RAIZ / "Actividad 1 - Lavadero de datos.xlsx"
SALIDA_DEFECTO = RAIZ / "web" / "data"

HOJA_A1 = "28.9 Dataset_limpio_A1"
HOJA_A2 = "Dataset_limpio_A2"
HOJA_CLIENTES = "2.10 Clientes"
HOJA_PRODUCTOS = "2.10_Productos"
HOJA_SUCURSALES = "2.10_A3_Sucursales"
HOJA_VENTAS = "2.10_A3_Ventas"

# Regiones de ciudades que solo aparecen en Sucursales (el Excel no trae la región).
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
    if pd.isna(valor):
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
    if pd.isna(valor):
        return None, False
    limpio = re.sub(r"[^0-9kK]", "", str(valor)).upper()
    if len(limpio) < 2:
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
    if pd.isna(valor):
        return None
    return pd.to_datetime(valor, dayfirst=True).strftime("%Y-%m-%d")


def nivel_riesgo(dias_morosidad: int, estado: str) -> str:
    """Misma regla de negocio que la fórmula IFS de la hoja Dataset_limpio_A2."""
    if dias_morosidad is None or estado is None:
        return "Sin Dato"
    if dias_morosidad > 60 and estado == "Activo":
        return "Riesgo Alto"
    if dias_morosidad > 30 or estado == "En Revisión":
        return "Riesgo Medio"
    return "Riesgo Bajo"


def contar_cambios(antes: pd.Series, despues: pd.Series) -> int:
    return int((antes.astype("string").fillna("") != despues.astype("string").fillna("")).sum())


def leer_hoja(excel: Path, hoja: str, columnas: list[str]) -> pd.DataFrame:
    df = pd.read_excel(excel, sheet_name=hoja, dtype=object)
    df.columns = [str(c).strip() for c in df.columns]
    df = df[columnas].dropna(how="all").reset_index(drop=True)
    return df


def catalogo(valores: pd.Series, prefijo: str, col_id: str, col_nombre: str) -> pd.DataFrame:
    unicos = sorted(v for v in valores.dropna().unique())
    return pd.DataFrame({col_id: [f"{prefijo}-{i:02d}" for i in range(1, len(unicos) + 1)],
                         col_nombre: unicos})


def deduplicar(df: pd.DataFrame, claves: list[str], tabla: str) -> pd.DataFrame:
    duplicados = df.duplicated(subset=claves, keep="first")
    registrar(tabla, ", ".join(claves), "Registros duplicados", duplicados.sum(),
              "Se conserva la primera aparición")
    return df[~duplicados].reset_index(drop=True)


# ---------------------------------------------------------------- etapas del proceso

def limpiar_cartera(excel: Path) -> pd.DataFrame:
    t = "cartera_clientes"
    cols = ["ID_Cliente", "RUT_Cliente", "Nombre_Completo", "Email_Contacto", "Comuna", "Ciudad",
            "Region", "Monto_Facturado_CLP", "Dias_Morosidad", "Estado_Cuenta", "Fecha_Registro"]
    a1 = leer_hoja(excel, HOJA_A1, cols)
    a2 = leer_hoja(excel, HOJA_A2, ["ID_Cliente", "Producto"])

    df = pd.DataFrame()
    df["id_cliente"] = a1["ID_Cliente"].map(limpiar_espacios).str.upper()

    rut = a1["RUT_Cliente"].map(normalizar_rut)
    df["rut"] = rut.str[0]
    df["rut_dv_valido"] = rut.str[1]
    registrar(t, "rut", "RUT en formatos mixtos (con y sin puntos)",
              contar_cambios(a1["RUT_Cliente"], df["rut"]), "Formato único 12.345.678-9")
    registrar(t, "rut", "Dígito verificador no coincide con el cálculo módulo 11",
              (~df["rut_dv_valido"]).sum(), "Se conserva y se marca rut_dv_valido = False")

    df["nombre"] = a1["Nombre_Completo"].map(nombre_propio)
    registrar(t, "nombre", "Nombres en MAYÚSCULAS / minúsculas / espacios",
              contar_cambios(a1["Nombre_Completo"], df["nombre"]), "Nombre propio y TRIM")

    email = a1["Email_Contacto"].map(normalizar_email)
    df["email"] = email.str[0]
    df["email_valido"] = email.str[1]
    registrar(t, "email", "Emails con tildes o ñ (no válidos en la parte local)",
              contar_cambios(a1["Email_Contacto"], df["email"]), "Se eliminan tildes y ñ -> n")

    df["comuna"] = a1["Comuna"].map(nombre_propio)
    df["ciudad"] = a1["Ciudad"].map(nombre_propio)
    df["region"] = a1["Region"].map(limpiar_espacios)
    df["monto_facturado_clp"] = pd.to_numeric(a1["Monto_Facturado_CLP"], errors="coerce").round().astype("Int64")
    df["dias_morosidad"] = pd.to_numeric(a1["Dias_Morosidad"], errors="coerce").astype("Int64")

    df["estado_cuenta"] = a1["Estado_Cuenta"].map(normalizar_estado)
    registrar(t, "estado_cuenta", "Estados con mayúsculas inconsistentes (activo, EN REVISIÓN)",
              contar_cambios(a1["Estado_Cuenta"], df["estado_cuenta"]), "Catálogo de 3 estados")

    df["fecha_registro"] = a1["Fecha_Registro"].map(fecha_iso)

    productos = a2.assign(ID_Cliente=a2["ID_Cliente"].map(limpiar_espacios).str.upper())
    productos = productos.drop_duplicates("ID_Cliente").set_index("ID_Cliente")["Producto"].map(limpiar_espacios)
    df["producto_financiero"] = df["id_cliente"].map(productos)
    registrar(t, "producto_financiero", "Cliente presente en A1 pero ausente en A2 (sin producto)",
              df["producto_financiero"].isna().sum(), "Se deja la FK vacía")

    nulos = df[["id_cliente", "rut", "nombre"]].isna().any(axis=1)
    registrar(t, "id_cliente, rut, nombre", "Campos obligatorios vacíos", nulos.sum(), "Fila descartada")
    df = df[~nulos]
    df = deduplicar(df, ["rut"], t)
    return df


def limpiar_comercial(excel: Path) -> tuple[pd.DataFrame, ...]:
    c = leer_hoja(excel, HOJA_CLIENTES,
                  ["ID_Cliente", "RUT_Cliente", "Nombre_Cliente", "Segmento", "Comuna", "Ciudad", "Region"])
    clientes = pd.DataFrame()
    clientes["id_cliente"] = c["ID_Cliente"].map(limpiar_espacios).str.upper()
    rut = c["RUT_Cliente"].map(normalizar_rut)
    clientes["rut"] = rut.str[0]
    clientes["rut_dv_valido"] = rut.str[1]
    registrar("clientes", "rut", "Dígito verificador no coincide con el cálculo módulo 11",
              (~clientes["rut_dv_valido"]).sum(), "Se conserva y se marca rut_dv_valido = False")
    clientes["nombre"] = c["Nombre_Cliente"].map(nombre_propio)
    clientes["segmento"] = c["Segmento"].map(limpiar_espacios)
    clientes["comuna"] = c["Comuna"].map(nombre_propio)
    clientes["ciudad"] = c["Ciudad"].map(nombre_propio)
    clientes["region"] = c["Region"].map(limpiar_espacios)
    clientes = deduplicar(clientes, ["id_cliente"], "clientes")

    p = leer_hoja(excel, HOJA_PRODUCTOS, ["ID_Producto", "Nombre_Producto", "Categoria", "Precio_Unitario_CLP"])
    productos = pd.DataFrame({
        "id_producto": p["ID_Producto"].map(limpiar_espacios).str.upper(),
        "nombre": p["Nombre_Producto"].map(limpiar_espacios),
        "categoria": p["Categoria"].map(nombre_propio),
        "precio_unitario_clp": pd.to_numeric(p["Precio_Unitario_CLP"], errors="coerce").round().astype("Int64"),
    })
    productos = deduplicar(productos, ["id_producto"], "productos")

    s = leer_hoja(excel, HOJA_SUCURSALES, ["ID_Sucursal", "Nombre_Sucursal", "Comuna", "Ciudad", "Zona_Comercial"])
    sucursales = pd.DataFrame({
        "id_sucursal": s["ID_Sucursal"].map(limpiar_espacios).str.upper(),
        "nombre": s["Nombre_Sucursal"].map(limpiar_espacios),
        "comuna": s["Comuna"].map(nombre_propio),
        "ciudad": s["Ciudad"].map(nombre_propio),
        "zona_comercial": s["Zona_Comercial"].map(limpiar_espacios),
    })
    sucursales = deduplicar(sucursales, ["id_sucursal"], "sucursales")

    v = leer_hoja(excel, HOJA_VENTAS,
                  ["ID_Transaccion", "Fecha_Venta", "ID_Cliente", "ID_Producto", "ID_Sucursal", "Cantidad"])
    ventas = pd.DataFrame({
        "id_transaccion": v["ID_Transaccion"].map(limpiar_espacios).str.upper(),
        "fecha_venta": v["Fecha_Venta"].map(fecha_iso),
        "id_cliente": v["ID_Cliente"].map(limpiar_espacios).str.upper(),
        "id_producto": v["ID_Producto"].map(limpiar_espacios).str.upper(),
        "id_sucursal": v["ID_Sucursal"].map(limpiar_espacios).str.upper(),
        "cantidad": pd.to_numeric(v["Cantidad"], errors="coerce").astype("Int64"),
    })
    ventas = deduplicar(ventas, ["id_transaccion"], "ventas")
    return clientes, productos, sucursales, ventas


def construir_geografia(*fuentes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    geo = pd.concat([f[[c for c in ("comuna", "ciudad", "region") if c in f]] for f in fuentes], ignore_index=True)
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
    "cartera_clientes": {"dominio": "Cartera financiera", "descripcion": "Clientes de la cartera (hojas Dataset_limpio A1/A2).", "columnas": [
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
    "clientes": {"dominio": "Comercial", "descripcion": "Clientes del módulo de ventas (hoja 2.10 Clientes).", "columnas": [
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


# ---------------------------------------------------------------- orquestación

def ejecutar(excel: Path, salida: Path) -> None:
    print(f"Leyendo {excel.name} ...")
    cartera = limpiar_cartera(excel)
    clientes, productos, sucursales, ventas = limpiar_comercial(excel)

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

    salida.mkdir(parents=True, exist_ok=True)
    for nombre, df in tablas.items():
        esperadas = [c["nombre"] for c in MODELO[nombre]["columnas"]]
        assert list(df.columns) == esperadas, f"{nombre}: columnas {list(df.columns)} != {esperadas}"
        df.to_csv(salida / f"{nombre}.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
        MODELO[nombre]["filas"] = len(df)
        print(f"  {nombre + '.csv':<28} {len(df):>5} filas")

    for nombre, meta in MODELO.items():
        for c in meta["columnas"]:
            if c["fk"]:
                tabla_ref, col_ref = c["fk"].split(".")
                huerfanas = ~tablas[nombre][c["nombre"]].dropna().isin(set(tablas[tabla_ref][col_ref]))
                assert not huerfanas.any(), f"Integridad referencial rota en {nombre}.{c['nombre']}"

    metadatos = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "fuente": excel.name,
        "tablas": MODELO,
        "calidad": [q for q in calidad if q["filas_afectadas"] > 0],
    }
    (salida / "modelo.json").write_text(json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  modelo.json  ({len(metadatos['calidad'])} hallazgos de calidad)")
    print(f"Listo. Archivos en {salida}")


if __name__ == "__main__":
    excel = Path(sys.argv[1]) if len(sys.argv) > 1 else EXCEL_DEFECTO
    salida = Path(sys.argv[2]) if len(sys.argv) > 2 else SALIDA_DEFECTO
    ejecutar(excel, salida)
