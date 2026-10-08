// Consultas SQL equivalentes a lo que muestra la web, para ejecutarlas en PostgreSQL (fuente "PostgreSQL local").
// El ETL publica sus tablas en el esquema `esquema` de la base; las tablas origen_* apuntan a las tablas crudas.

export const TABLA_CRUDA = {
  origen_cartera: "clientes_cartera",
  origen_clientes: "maestro_clientes",
  origen_productos: "maestro_productos",
  origen_sucursales: "maestro_sucursales",
  origen_ventas: "ventas_transacciones",
};

const literal = (v) => `'${String(v).replace(/'/g, "''")}'`;
const lista = (valores) => valores.map(literal).join(", ");

function cuentasDetalle(e, estados = []) {
  return `FROM ${e}.cuentas c
JOIN ${e}.cartera_clientes cl ON cl.id_cliente = c.id_cliente
JOIN ${e}.comunas co ON co.id_comuna = cl.id_comuna
JOIN ${e}.ciudades ci ON ci.id_ciudad = co.id_ciudad
JOIN ${e}.regiones r ON r.id_region = ci.id_region
JOIN ${e}.estados_cuenta es ON es.id_estado = c.id_estado
LEFT JOIN ${e}.productos_financieros p ON p.id_producto_financiero = c.id_producto_financiero` +
    (estados.length ? `\nWHERE es.nombre IN (${lista(estados)})` : "");
}

const filtroEstados = (estados) => estados.length ? ` · Estado_Cuenta: ${estados.join(", ")}` : "";

export const SQL = {
  kpiClientes: (e) => `-- Clientes de la cartera financiera
SELECT count(*) AS clientes_cartera
FROM ${e}.cartera_clientes;`,

  kpiFacturado: (e) => `-- Monto facturado de la cartera
SELECT sum(monto_facturado_clp) AS monto_facturado_clp
FROM ${e}.cuentas;`,

  kpiRiesgoAlto: (e) => `-- Cuentas en riesgo alto (más de 60 días de mora y estado Activo)
SELECT count(*) AS cuentas_riesgo_alto
FROM ${e}.cuentas
WHERE nivel_riesgo = 'Riesgo Alto';`,

  kpiClientesComerciales: (e) => `-- Clientes comerciales
SELECT count(*) AS clientes_comerciales
FROM ${e}.clientes;`,

  kpiVentas: (e) => `-- Ventas válidas y su monto
SELECT count(*) AS ventas_validas, sum(monto_total_clp) AS monto_total_clp
FROM ${e}.ventas;`,

  kpiRechazadas: (e) => `-- Ventas rechazadas por claves foráneas inexistentes
SELECT id_transaccion, id_cliente, id_producto, id_sucursal, motivo
FROM ${e}.ventas_rechazadas
ORDER BY id_transaccion;`,

  comparacion: (e) => `-- Indicadores de la comparación entre fuentes (columna PostgreSQL local)
SELECT
  (SELECT count(*) FROM ${e}.origen_cartera)                     AS filas_cartera_fuente,
  (SELECT count(*) FROM ${e}.cartera_clientes)                   AS clientes_tras_etl,
  sum(monto_facturado_clp)                                       AS monto_facturado_clp,
  round(avg(monto_facturado_clp))                                AS ticket_promedio_clp,
  round(100.0 * count(*) FILTER (WHERE dias_morosidad > 60) / count(*), 1) AS tasa_morosidad_critica_pct,
  count(*) FILTER (WHERE id_producto_financiero IS NULL)         AS clientes_sin_producto,
  (SELECT count(*) FROM ${e}.ventas)                             AS ventas_validas,
  (SELECT sum(monto_total_clp) FROM ${e}.ventas)                 AS monto_ventas_clp,
  (SELECT count(*) FROM ${e}.ventas_rechazadas)                  AS ventas_rechazadas
FROM ${e}.cuentas;`,

  tablaCompleta: (e, tabla) => TABLA_CRUDA[tabla]
    ? `-- Datos originales sin limpiar (tabla cruda de la base)
SELECT *
FROM public.${TABLA_CRUDA[tabla]};`
    : `-- Tabla ${tabla} procesada por el ETL
SELECT *
FROM ${e}.${tabla};`,

  tablaVista: (e, tabla, { filtro, busqueda, orden }) => {
    const desde = TABLA_CRUDA[tabla] ? `public.${TABLA_CRUDA[tabla]}` : `${e}.${tabla}`;
    const condiciones = [];
    if (filtro) condiciones.push(`t.${filtro.columna.toLowerCase()}::text = ${literal(filtro.valor)}`);
    if (busqueda?.trim()) condiciones.push(`t::text ILIKE ${literal(`%${busqueda.trim()}%`)}`);
    return `-- ${tabla}${filtro ? ` filtrada por ${filtro.columna} = ${filtro.valor}` : ""}${busqueda?.trim() ? ` · búsqueda "${busqueda.trim()}"` : ""}
SELECT t.*
FROM ${desde} AS t` +
      (condiciones.length ? `\nWHERE ${condiciones.join("\n  AND ")}` : "") +
      (orden?.columna ? `\nORDER BY t.${orden.columna.toLowerCase()} ${orden.asc ? "ASC" : "DESC"}` : "") + ";";
  },

  diccionario: (e, tabla) => `-- Columnas, tipos y descripciones de ${tabla}
SELECT c.column_name AS columna, c.data_type AS tipo,
       col_description('${e}.${tabla}'::regclass, c.ordinal_position) AS descripcion
FROM information_schema.columns c
WHERE c.table_schema = '${e}' AND c.table_name = '${tabla}'
ORDER BY c.ordinal_position;`,

  relaciones: (e) => `-- Relaciones del modelo: cada clave foránea y la tabla que referencia
SELECT tc.table_name  AS tabla,
       kcu.column_name AS columna_fk,
       ccu.table_name  AS tabla_referenciada,
       ccu.column_name AS columna_pk
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_schema = tc.constraint_schema AND kcu.constraint_name = tc.constraint_name
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_schema = tc.constraint_schema AND ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = '${e}'
ORDER BY tabla, columna_fk;`,

  calidad: (e) => `-- Validaciones de calidad sobre los datos ya procesados
SELECT 'cartera_clientes' AS tabla, 'rut' AS columna, 'RUT con dígito verificador inválido' AS problema,
       count(*) FILTER (WHERE NOT rut_dv_valido) AS filas FROM ${e}.cartera_clientes
UNION ALL
SELECT 'cartera_clientes', 'email', 'Email con formato inválido',
       count(*) FILTER (WHERE NOT email_valido) FROM ${e}.cartera_clientes
UNION ALL
SELECT 'clientes', 'rut', 'RUT con dígito verificador inválido',
       count(*) FILTER (WHERE NOT rut_dv_valido) FROM ${e}.clientes
UNION ALL
SELECT 'cuentas', 'id_producto_financiero', 'Cuenta sin producto financiero',
       count(*) FILTER (WHERE id_producto_financiero IS NULL) FROM ${e}.cuentas
UNION ALL
SELECT 'ventas_rechazadas', 'motivo', motivo, count(*) FROM ${e}.ventas_rechazadas GROUP BY motivo
ORDER BY tabla, columna;`,

  guiaProducto: (e, estados = []) => `-- Paso 1 · Tabla dinámica por producto${filtroEstados(estados)}
SELECT CASE WHEN GROUPING(p.nombre) = 1 THEN 'Total general'
            ELSE coalesce(p.nombre, '(Sin producto)') END AS producto,
       sum(c.monto_facturado_clp)          AS suma_monto_facturado_clp,
       round(avg(c.dias_morosidad), 1)     AS promedio_dias_morosidad
${cuentasDetalle(e, estados)}
GROUP BY ROLLUP (p.nombre)
ORDER BY GROUPING(p.nombre), producto;`,

  guiaRegionComuna: (e, estados = []) => `-- Paso 1 · Tabla dinámica por región y comuna${filtroEstados(estados)}
SELECT CASE WHEN GROUPING(r.nombre) = 1 THEN 'Total general'
            WHEN GROUPING(co.nombre) = 1 THEN r.nombre
            ELSE '    ' || co.nombre END     AS region_comuna,
       sum(c.monto_facturado_clp)          AS suma_monto_facturado_clp,
       count(c.id_cliente)                 AS cuenta_id_cliente
${cuentasDetalle(e, estados)}
GROUP BY ROLLUP (r.nombre, co.nombre)
ORDER BY GROUPING(r.nombre), r.nombre, GROUPING(co.nombre) DESC, co.nombre;`,

  guiaIngresos: (e, estados = []) => `-- Paso 2 · Ingresos totales facturados${filtroEstados(estados)}
SELECT sum(c.monto_facturado_clp) AS ingresos_totales_clp, count(*) AS clientes
${cuentasDetalle(e, estados)};`,

  guiaTicket: (e, estados = []) => `-- Paso 2 · Ticket promedio por cliente (ingresos ÷ N° clientes)${filtroEstados(estados)}
SELECT round(avg(c.monto_facturado_clp)) AS ticket_promedio_clp
${cuentasDetalle(e, estados)};`,

  guiaTasaCritica: (e, estados = []) => `-- Paso 2 · Tasa de morosidad crítica (más de 60 días)${filtroEstados(estados)}
SELECT count(*) FILTER (WHERE c.dias_morosidad > 60) AS clientes_criticos,
       round(100.0 * count(*) FILTER (WHERE c.dias_morosidad > 60) / count(*), 1) AS tasa_critica_pct
${cuentasDetalle(e, estados)};`,

  guiaRegiones: (e, estados = []) => `-- Paso 3 · Total facturado por región (gráfico)${filtroEstados(estados)}
SELECT regexp_replace(r.nombre, '^Región (del |de la |de )?', '') AS region,
       sum(c.monto_facturado_clp) AS total_facturado_clp
${cuentasDetalle(e, estados)}
GROUP BY r.nombre
ORDER BY total_facturado_clp DESC;`,

  guiaCruce: (e) => `-- Paso 4 · Cruce de ventas con la zona comercial de la sucursal
(SELECT v.id_transaccion, v.id_sucursal, coalesce(z.nombre, 'Sin sucursal') AS zona_comercial,
        v.id_producto, v.cantidad, v.precio_unitario_clp, v.monto_total_clp
 FROM ${e}.ventas v
 LEFT JOIN ${e}.sucursales s ON s.id_sucursal = v.id_sucursal
 LEFT JOIN ${e}.zonas_comerciales z ON z.id_zona = s.id_zona
 ORDER BY v.id_transaccion
 LIMIT 6)
UNION ALL
-- Ventas con producto inexistente: quedan con precio y total 0
SELECT r.id_transaccion, r.id_sucursal, coalesce(z.nombre, 'Sin sucursal'),
       r.id_producto, r.cantidad, 0, 0
FROM ${e}.ventas_rechazadas r
LEFT JOIN ${e}.sucursales s ON s.id_sucursal = r.id_sucursal
LEFT JOIN ${e}.zonas_comerciales z ON z.id_zona = s.id_zona
WHERE r.id_producto = 'PROD-999';`,

  guiaZonas: (e) => `-- Paso 4 · Total facturado por zona comercial
SELECT CASE WHEN GROUPING(z.nombre) = 1 THEN 'Total general'
            ELSE coalesce(z.nombre, 'Sin sucursal') END AS zona_comercial,
       count(*)              AS n_ventas,
       sum(v.monto_total_clp) AS suma_total_facturado_clp
FROM ${e}.ventas v
LEFT JOIN ${e}.sucursales s ON s.id_sucursal = v.id_sucursal
LEFT JOIN ${e}.zonas_comerciales z ON z.id_zona = s.id_zona
GROUP BY ROLLUP (z.nombre)
ORDER BY GROUPING(z.nombre), suma_total_facturado_clp DESC;`,
};
