// Consultas SQL equivalentes a lo que muestra la web, para ejecutarlas en PostgreSQL (fuente "PostgreSQL local").
// La base tablaspollet tiene el mismo modelo que la web (bd/crear_bd.py); las tablas origen_* traen nro_fila.

const p = (e) => (e && e !== "public" ? `${e}.` : "");
const esOrigen = (tabla) => tabla.startsWith("origen_");
const literal = (v) => `'${String(v).replace(/'/g, "''")}'`;
const lista = (valores) => valores.map(literal).join(", ");

function cuentasDetalle(e, estados = []) {
  return `FROM ${p(e)}cuentas c
JOIN ${p(e)}cartera_clientes cl ON cl.id_cliente = c.id_cliente
JOIN ${p(e)}comunas co ON co.id_comuna = cl.id_comuna
JOIN ${p(e)}ciudades ci ON ci.id_ciudad = co.id_ciudad
JOIN ${p(e)}regiones r ON r.id_region = ci.id_region
JOIN ${p(e)}estados_cuenta es ON es.id_estado = c.id_estado
LEFT JOIN ${p(e)}productos_financieros p ON p.id_producto_financiero = c.id_producto_financiero` +
    (estados.length ? `\nWHERE es.nombre IN (${lista(estados)})` : "");
}

const filtroEstados = (estados) => estados.length ? ` · Estado_Cuenta: ${estados.join(", ")}` : "";

export const SQL = {
  kpiClientes: (e) => `-- Clientes de la cartera financiera
SELECT count(*) AS clientes_cartera
FROM ${p(e)}cartera_clientes;`,

  kpiFacturado: (e) => `-- Monto facturado de la cartera
SELECT sum(monto_facturado_clp) AS monto_facturado_clp
FROM ${p(e)}cuentas;`,

  kpiRiesgoAlto: (e) => `-- Cuentas en riesgo alto (más de 60 días de mora y estado Activo)
SELECT count(*) AS cuentas_riesgo_alto
FROM ${p(e)}cuentas
WHERE nivel_riesgo = 'Riesgo Alto';`,

  kpiClientesComerciales: (e) => `-- Clientes comerciales
SELECT count(*) AS clientes_comerciales
FROM ${p(e)}clientes;`,

  kpiVentas: (e) => `-- Ventas válidas y su monto
SELECT count(*) AS ventas_validas, sum(monto_total_clp) AS monto_total_clp
FROM ${p(e)}ventas;`,

  kpiRechazadas: (e) => `-- Ventas rechazadas por claves foráneas inexistentes
SELECT id_transaccion, id_cliente, id_producto, id_sucursal, motivo
FROM ${p(e)}ventas_rechazadas
ORDER BY id_transaccion;`,

  comparacion: (e) => `-- Indicadores de la comparación entre fuentes (columna PostgreSQL local)
SELECT
  (SELECT count(*) FROM ${p(e)}origen_cartera)                     AS filas_cartera_fuente,
  (SELECT count(*) FROM ${p(e)}cartera_clientes)                   AS clientes_tras_etl,
  sum(monto_facturado_clp)                                       AS monto_facturado_clp,
  round(avg(monto_facturado_clp))                                AS ticket_promedio_clp,
  round(100.0 * count(*) FILTER (WHERE dias_morosidad > 60) / count(*), 1) AS tasa_morosidad_critica_pct,
  count(*) FILTER (WHERE id_producto_financiero IS NULL)         AS clientes_sin_producto,
  (SELECT count(*) FROM ${p(e)}ventas)                             AS ventas_validas,
  (SELECT sum(monto_total_clp) FROM ${p(e)}ventas)                 AS monto_ventas_clp,
  (SELECT count(*) FROM ${p(e)}ventas_rechazadas)                  AS ventas_rechazadas
FROM ${p(e)}cuentas;`,

  tablaCompleta: (e, tabla) => esOrigen(tabla)
    ? `-- ${tabla}: datos del CSV original sin limpiar, en el orden del archivo
SELECT *
FROM ${p(e)}${tabla}
ORDER BY nro_fila;`
    : `-- Tabla ${tabla} del modelo normalizado
SELECT *
FROM ${p(e)}${tabla};`,

  tablaVista: (e, tabla, { filtro, busqueda, orden }) => {
    const desde = `${p(e)}${tabla}`;
    const porDefecto = esOrigen(tabla) ? "\nORDER BY t.nro_fila" : "";
    const condiciones = [];
    if (filtro) condiciones.push(`t.${filtro.columna.toLowerCase()}::text = ${literal(filtro.valor)}`);
    if (busqueda?.trim()) condiciones.push(`t::text ILIKE ${literal(`%${busqueda.trim()}%`)}`);
    return `-- ${tabla}${filtro ? ` filtrada por ${filtro.columna} = ${filtro.valor}` : ""}${busqueda?.trim() ? ` · búsqueda "${busqueda.trim()}"` : ""}
SELECT t.*
FROM ${desde} AS t` +
      (condiciones.length ? `\nWHERE ${condiciones.join("\n  AND ")}` : "") +
      (orden?.columna ? `\nORDER BY t.${orden.columna.toLowerCase()} ${orden.asc ? "ASC" : "DESC"}` : porDefecto) + ";";
  },

  diccionario: (e, tabla) => `-- Columnas, tipos y descripciones de ${tabla}
SELECT c.column_name AS columna, c.data_type AS tipo,
       col_description('${p(e)}${tabla}'::regclass, c.ordinal_position) AS descripcion
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

  calidad: (e) => `-- Hallazgos y correcciones del ETL (misma tabla que muestra la web)
SELECT tabla, columna, problema, filas_afectadas, accion
FROM ${p(e)}hallazgos_calidad
ORDER BY id_hallazgo;

-- Validaciones que se pueden recalcular sobre las tablas del modelo
SELECT 'cartera_clientes' AS tabla, 'rut' AS columna, 'RUT con dígito verificador inválido' AS problema,
       count(*) FILTER (WHERE NOT rut_dv_valido) AS filas FROM ${p(e)}cartera_clientes
UNION ALL
SELECT 'cartera_clientes', 'email', 'Email con formato inválido',
       count(*) FILTER (WHERE NOT email_valido) FROM ${p(e)}cartera_clientes
UNION ALL
SELECT 'clientes', 'rut', 'RUT con dígito verificador inválido',
       count(*) FILTER (WHERE NOT rut_dv_valido) FROM ${p(e)}clientes
UNION ALL
SELECT 'cuentas', 'id_producto_financiero', 'Cuenta sin producto financiero',
       count(*) FILTER (WHERE id_producto_financiero IS NULL) FROM ${p(e)}cuentas
UNION ALL
SELECT 'ventas_rechazadas', 'motivo', motivo, count(*) FROM ${p(e)}ventas_rechazadas GROUP BY motivo
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
 FROM ${p(e)}ventas v
 LEFT JOIN ${p(e)}sucursales s ON s.id_sucursal = v.id_sucursal
 LEFT JOIN ${p(e)}zonas_comerciales z ON z.id_zona = s.id_zona
 ORDER BY v.fecha_venta, v.id_transaccion
 LIMIT 6)
UNION ALL
-- Ventas con producto inexistente: quedan con precio y total 0
SELECT r.id_transaccion, r.id_sucursal, coalesce(z.nombre, 'Sin sucursal'),
       r.id_producto, r.cantidad, 0, 0
FROM ${p(e)}ventas_rechazadas r
LEFT JOIN ${p(e)}sucursales s ON s.id_sucursal = r.id_sucursal
LEFT JOIN ${p(e)}zonas_comerciales z ON z.id_zona = s.id_zona
WHERE r.id_producto = 'PROD-999';`,

  guiaZonas: (e) => `-- Paso 4 · Total facturado por zona comercial
SELECT CASE WHEN GROUPING(z.nombre) = 1 THEN 'Total general'
            ELSE coalesce(z.nombre, 'Sin sucursal') END AS zona_comercial,
       count(*)              AS n_ventas,
       sum(v.monto_total_clp) AS suma_total_facturado_clp
FROM ${p(e)}ventas v
LEFT JOIN ${p(e)}sucursales s ON s.id_sucursal = v.id_sucursal
LEFT JOIN ${p(e)}zonas_comerciales z ON z.id_zona = s.id_zona
GROUP BY ROLLUP (z.nombre)
ORDER BY GROUPING(z.nombre), suma_total_facturado_clp DESC;`,
};
