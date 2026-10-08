# Tablas Pollet · Lavadero de datos

Proceso ETL que toma el Excel `Actividad 1 - Lavadero de datos.xlsx`, limpia y normaliza sus hojas
(clientes, cartera financiera, productos, sucursales y ventas) y las exporta a CSV relacionados.
Una web estática muestra las tablas, el modelo entidad-relación y el reporte de calidad de datos.

El mismo proceso se aplica a tres fuentes, y la web permite alternar entre ellas y compararlas:

- **Excel trabajado**: hojas del Excel después de la limpieza manual.
- **CSV originales**: archivos de `web/data/dataoriginal`, antes de cualquier limpieza.
- **PostgreSQL local**: base `tablaspollet` con el mismo modelo de datos de la web, cargada desde los CSV originales.

## Estructura

```
etl/procesar_excel.py    Proceso ETL para todas las fuentes -> CSV + modelo.json
bd/crear_bd.py           Crea la base tablaspollet con el modelo de la web y los datos de los CSV originales
bd/verificar_bd.py       Comprueba la estructura y que los datos coincidan con los CSV originales
api/servidor.py          API FastAPI: lee las tablas de PostgreSQL y sirve la web
web/                     Sitio estático (index.html, app.js, styles.css)
web/data/dataoriginal/   CSV originales sin limpiar (entrada)
web/data/excel/          Resultado del ETL sobre el Excel
web/data/original/       Resultado del ETL sobre los CSV originales
web/data/*/export/       Descargas por fuente: CSV (.zip), Excel (.xlsx) y PostgreSQL (.sql)
web/data/fuentes.json    Listado de fuentes disponibles en la web
render.yaml              Configuración de despliegue en Render
```

## Regenerar los CSV

```bash
pip install -r requirements.txt
python etl/procesar_excel.py
```

## Exportar datos

El botón **Exportar datos** de la web descarga todas las tablas de la fuente activa. El ETL genera los archivos
(`etl/exportar.py`):

- **CSV (.zip)**: un CSV por tabla, los datos de origen en `origen/` y `modelo.json`.
- **Excel (.xlsx)**: una hoja por tabla, con índice, hoja de calidad, filtros y formatos de número y fecha.
- **PostgreSQL (.sql)**: crea el esquema `lavadero_<fuente>` con tablas, PK, FK, comentarios e inserts.
  Cargar con `psql -d <base> -f lavadero_<fuente>_postgres.sql`.

## Base de datos PostgreSQL local

Conexión por defecto: `localhost:5432`, base `tablaspollet`, usuario `postgres`, clave `123456`
(se puede cambiar con `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` y `PGDATABASE`).

```bash
python bd/crear_bd.py      # borra la estructura anterior y crea el modelo de la web (también deja bd/tablaspollet.sql)
python bd/verificar_bd.py  # opcional: comprueba estructura y datos contra los CSV originales
python api/servidor.py     # API + web en http://localhost:8000
```

La base sigue el mismo modelo que muestra la web, en el esquema `public`:

- **15 tablas normalizadas** (geografía, cartera financiera y comercial) con PK, FK, tipos y comentarios,
  cargadas con el resultado del ETL sobre los CSV originales.
- **5 tablas `origen_*`** con los CSV originales tal cual (texto, sin limpiar) y `nro_fila` como clave.
- **`hallazgos_calidad`**: los problemas que detectó el ETL y la acción aplicada.

Endpoints:

| Método y ruta | Descripción |
|---|---|
| `GET /api/estado` | Estado de la conexión y filas por tabla |
| `GET /api/fuentes/bd/modelo.json` | Lee las tablas de la base y devuelve modelo, filas y calidad |
| `GET /api/fuentes/bd/{tabla}.csv` | Tabla leída de la base |
| `GET /api/fuentes/bd/export/{archivo}` | Descargas CSV (.zip), Excel (.xlsx) y SQL |

Con la fuente **PostgreSQL local** activa, la web muestra botones **SQL** junto a los KPIs, la comparación,
las tarjetas de tablas, la tabla abierta (con su filtro, búsqueda y orden), el modelo, la calidad y cada resultado
de la guía: copian al portapapeles la consulta que devuelve ese mismo valor, lista para ejecutar en psql o pgAdmin.
Las consultas están en `web/consultas.js`.

La web consulta `/api/estado` cada 10 segundos. Si la API o la base no responden, la opción
**PostgreSQL local** aparece *offline* y no se puede seleccionar; si la conexión se pierde mientras está
activa, la web vuelve a los CSV originales. Con la base en línea, el botón **Recargar** vuelve a leer las tablas.

## Ver la web en local

```bash
python api/servidor.py
```

Abrir `http://localhost:8000`. Sin la API también funciona con `python -m http.server 8000 -d web`,
pero la fuente PostgreSQL aparecerá offline.

## Despliegue en Render

Render detecta `render.yaml` (New > Blueprint) y publica la carpeta `web` como sitio estático.
Si se crea manualmente como *Static Site*: Build Command vacío y Publish Directory `web`.
Al cambiar el Excel, ejecutar el ETL en local y subir los CSV actualizados.
En el sitio publicado, la fuente PostgreSQL solo aparece en línea si en el mismo equipo está corriendo
`python api/servidor.py` (la web prueba `http://localhost:8000/api`).
