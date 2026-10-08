# Tablas Pollet · Lavadero de datos

Proceso ETL que toma el Excel `Actividad 1 - Lavadero de datos.xlsx`, limpia y normaliza sus hojas
(clientes, cartera financiera, productos, sucursales y ventas) y las exporta a CSV relacionados.
Una web estática muestra las tablas, el modelo entidad-relación y el reporte de calidad de datos.

El mismo proceso se aplica a dos fuentes, y la web permite alternar entre ellas y compararlas:

- **Excel trabajado**: hojas del Excel después de la limpieza manual.
- **CSV originales**: archivos de `web/data/dataoriginal`, antes de cualquier limpieza.

## Estructura

```
etl/procesar_excel.py    Proceso ETL para ambas fuentes -> CSV + modelo.json
web/                     Sitio estático (index.html, app.js, styles.css)
web/data/dataoriginal/   CSV originales sin limpiar (entrada)
web/data/excel/          Resultado del ETL sobre el Excel
web/data/original/       Resultado del ETL sobre los CSV originales
web/data/fuentes.json    Listado de fuentes disponibles en la web
render.yaml              Configuración de despliegue en Render
```

## Regenerar los CSV

```bash
pip install -r requirements.txt
python etl/procesar_excel.py
```

## Ver la web en local

```bash
python -m http.server 8000 -d web
```

Abrir `http://localhost:8000`.

## Despliegue en Render

Render detecta `render.yaml` (New > Blueprint) y publica la carpeta `web` como sitio estático.
Si se crea manualmente como *Static Site*: Build Command vacío y Publish Directory `web`.
Al cambiar el Excel, ejecutar el ETL en local y subir los CSV actualizados.
