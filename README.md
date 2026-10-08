# Tablas Pollet · Lavadero de datos

Proceso ETL que toma el Excel `Actividad 1 - Lavadero de datos.xlsx`, limpia y normaliza sus hojas
(clientes, cartera financiera, productos, sucursales y ventas) y las exporta a CSV relacionados.
Una web estática muestra las tablas, el modelo entidad-relación y el reporte de calidad de datos.

## Estructura

```
etl/procesar_excel.py   Proceso Excel -> CSV + modelo.json
web/                    Sitio estático (index.html, app.js, styles.css)
web/data/               CSV generados y metadatos del modelo
render.yaml             Configuración de despliegue en Render
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
