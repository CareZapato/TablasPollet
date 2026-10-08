const DATA_DIR = "data";
const POR_PAGINA = 25;
const COLOR_DOMINIO = {
  "Geografía": "var(--geo)",
  "Cartera financiera": "var(--fin)",
  "Comercial": "var(--com)",
  "Control de calidad": "var(--qa)",
  "Datos de origen": "var(--origen)",
};
const ESTILO_FUENTE = {
  excel: { color: "var(--primary)", detalle: "Después de la limpieza manual" },
  original: { color: "#e07a1f", detalle: "Sin limpiar, tal como llegaron" },
  bd: { color: "#336791", detalle: "Base de datos local" },
};

const fuentes = {};
let catalogo = [];

const estado = {
  fuente: null,
  modelo: null,
  datos: {},
  tabla: null,
  busqueda: "",
  filtro: null,
  orden: { columna: null, asc: true },
  pagina: 0,
};

const $ = (sel) => document.querySelector(sel);
const fmtNum = new Intl.NumberFormat("es-CL");
const fmtCLP = new Intl.NumberFormat("es-CL", { style: "currency", currency: "CLP", maximumFractionDigits: 0 });

function parseCSV(texto) {
  texto = texto.replace(/^\uFEFF/, "");
  const filas = [];
  let fila = [], campo = "", comillas = false;
  for (let i = 0; i < texto.length; i++) {
    const c = texto[i];
    if (comillas) {
      if (c === '"' && texto[i + 1] === '"') { campo += '"'; i++; }
      else if (c === '"') comillas = false;
      else campo += c;
    } else if (c === '"') comillas = true;
    else if (c === ",") { fila.push(campo); campo = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && texto[i + 1] === "\n") i++;
      fila.push(campo); filas.push(fila); fila = []; campo = "";
    } else campo += c;
  }
  if (campo || fila.length) { fila.push(campo); filas.push(fila); }
  const [cabecera, ...resto] = filas;
  return resto.filter((f) => f.length > 1 || f[0] !== "")
    .map((f) => Object.fromEntries(cabecera.map((h, i) => [h, f[i] ?? ""])));
}

async function leerJSON(ruta) {
  const resp = await fetch(ruta);
  if (!resp.ok) throw new Error(`No se pudo leer ${ruta}`);
  return resp.json();
}

async function cargarFuente(f, dir) {
  const modelo = await leerJSON(`${dir}/modelo.json`);
  const datos = {};
  await Promise.all(Object.keys(modelo.tablas).map(async (nombre) => {
    const r = await fetch(`${dir}/${nombre}.csv`, { cache: "no-store" });
    if (!r.ok) throw new Error(`No se pudo leer ${nombre}.csv`);
    datos[nombre] = parseCSV(await r.text());
  }));
  return { ...f, dir, modelo, datos };
}

async function cargar() {
  catalogo = await leerJSON(`${DATA_DIR}/fuentes.json`);
  const estaticas = catalogo.filter((f) => f.tipo !== "api");
  const cargadas = await Promise.all(estaticas.map((f) => cargarFuente(f, `${DATA_DIR}/${f.carpeta}`)));
  for (const f of cargadas) fuentes[f.id] = f;
}

const idsCargados = () => catalogo.map((f) => f.id).filter((id) => fuentes[id]);

// ------------------------------------------------------------------ API local (PostgreSQL)

const API_CANDIDATAS = [...new Set(["api", "http://localhost:8000/api"])];
const INTERVALO_ESTADO = 10000;
const api = { base: null, estado: null, cargando: false, aviso: "" };

async function consultarEstado() {
  const candidatas = api.base ? [api.base, ...API_CANDIDATAS.filter((c) => c !== api.base)] : API_CANDIDATAS;
  for (const base of candidatas) {
    try {
      const control = new AbortController();
      const plazo = setTimeout(() => control.abort(), 8000);
      const r = await fetch(`${base}/estado`, { cache: "no-store", signal: control.signal });
      clearTimeout(plazo);
      if (!r.ok) continue;
      const datos = await r.json();
      if (datos?.ok) { api.base = base; api.estado = datos; return; }
    } catch { /* se prueba la siguiente dirección */ }
  }
  api.base = null;
  api.estado = null;
}

const bdConectada = () => Boolean(api.estado?.conectado);

function textoEstadoBD() {
  if (api.cargando) return "Cargando datos…";
  if (bdConectada()) return `En línea · ${api.estado.base}@${api.estado.host}`;
  if (api.estado) return "Offline · sin conexión a la base";
  return "Offline · API no disponible";
}

async function vigilarBD() {
  await consultarEstado();
  if (!bdConectada() && fuentes.bd) {
    delete fuentes.bd;
    if (estado.fuente === "bd") {
      api.aviso = "Se perdió la conexión con la base de datos. Se volvió a CSV originales.";
      usarFuente(fuentes.original ? "original" : idsCargados()[0]);
    } else {
      renderComparacion();
    }
  }
  renderSelector();
}

async function seleccionarFuente(id) {
  const f = catalogo.find((x) => x.id === id);
  if (f?.tipo !== "api") { usarFuente(id); return; }
  if (!bdConectada() || api.cargando) return;
  api.cargando = true;
  api.aviso = "";
  renderSelector();
  try {
    fuentes[id] = await cargarFuente(f, `${api.base}/${f.ruta}`);
    usarFuente(id);
  } catch (e) {
    api.aviso = `No se pudieron leer los datos de la base: ${e.message}`;
    await consultarEstado();
    if (estado.fuente) usarFuente(estado.fuente);
  } finally {
    api.cargando = false;
    renderSelector();
  }
}

function renderSelector() {
  const cont = $("#selector-fuente");
  cont.innerHTML = catalogo.map((f) => {
    const esApi = f.tipo === "api";
    const offline = esApi && !bdConectada();
    const detalle = esApi ? textoEstadoBD() : ESTILO_FUENTE[f.id]?.detalle ?? f.carpeta;
    const ayuda = esApi
      ? (offline ? `Sin conexión. ${api.estado?.error ?? "Inicia la API con: python api/servidor.py"}` : f.descripcion)
      : f.descripcion;
    const clases = [f.id === estado.fuente ? "active" : "", offline ? "offline" : "", esApi && api.cargando ? "cargando" : ""];
    return `<button role="radio" data-fuente="${f.id}" class="${clases.join(" ")}" ${offline ? "disabled" : ""}
        aria-checked="${f.id === estado.fuente}" title="${escapar(ayuda)}" style="--c:${ESTILO_FUENTE[f.id]?.color ?? "var(--primary)"}">
      <span class="punto ${esApi ? (offline ? "rojo" : "verde") : ""}"></span>
      <span><b>${f.nombre}</b><small>${escapar(detalle)}</small></span>
    </button>`;
  }).join("");
  cont.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => seleccionarFuente(b.dataset.fuente)));
}

function tablasPorDominio(incluirOrigen = true) {
  const grupos = {};
  for (const [nombre, meta] of Object.entries(estado.modelo.tablas)) {
    if (meta.origen && !incluirOrigen) continue;
    (grupos[meta.dominio] ??= []).push(nombre);
  }
  return grupos;
}

const tablasModelo = () => Object.keys(estado.modelo.tablas).filter((t) => !meta(t).origen);
const meta = (tabla) => estado.modelo.tablas[tabla];
const colDef = (tabla, columna) => meta(tabla).columnas.find((c) => c.nombre === columna);

// ------------------------------------------------------------------ Resumen

function renderResumen() {
  const d = estado.datos;
  const totalFacturado = d.cuentas.reduce((s, r) => s + Number(r.monto_facturado_clp || 0), 0);
  const totalVentas = d.ventas.reduce((s, r) => s + Number(r.monto_total_clp || 0), 0);
  const riesgoAlto = d.cuentas.filter((r) => r.nivel_riesgo === "Riesgo Alto").length;
  const kpis = [
    ["Clientes cartera financiera", fmtNum.format(d.cartera_clientes.length)],
    ["Monto facturado cartera", fmtCLP.format(totalFacturado)],
    ["Cuentas en riesgo alto", fmtNum.format(riesgoAlto)],
    ["Clientes comerciales", fmtNum.format(d.clientes.length)],
    ["Ventas válidas", `${fmtNum.format(d.ventas.length)} · ${fmtCLP.format(totalVentas)}`],
    ["Ventas rechazadas", fmtNum.format(d.ventas_rechazadas.length)],
  ];
  $("#kpis").innerHTML = kpis.map(([t, v]) =>
    `<div class="kpi"><div class="muted">${t}</div><div class="valor">${v}</div></div>`).join("");

  renderComparacion();

  $("#tarjetas").innerHTML = Object.entries(estado.modelo.tablas).filter(([, m]) => !m.origen).map(([nombre, m]) => `
    <div class="tarjeta clickable" data-tabla="${nombre}" style="--dom:${COLOR_DOMINIO[m.dominio]}">
      <div class="cab"><h3>${nombre}</h3><span class="dominio-tag">${m.dominio}</span></div>
      <p class="muted">${m.descripcion}</p>
      <p><b>${fmtNum.format(m.filas)}</b> filas · ${m.columnas.length} columnas</p>
    </div>`).join("");
  document.querySelectorAll("#tarjetas .tarjeta").forEach((el) =>
    el.addEventListener("click", () => abrirTabla(el.dataset.tabla)));
}

function metricas(f) {
  const d = f.datos;
  const total = d.cuentas.reduce((s, r) => s + Number(r.monto_facturado_clp || 0), 0);
  const criticos = d.cuentas.filter((r) => Number(r.dias_morosidad) > 60).length;
  return {
    filasOrigen: d.origen_cartera.length,
    clientes: d.cartera_clientes.length,
    total,
    ticket: d.cuentas.length ? total / d.cuentas.length : 0,
    tasaCritica: d.cuentas.length ? criticos / d.cuentas.length : 0,
    sinProducto: d.cuentas.filter((r) => !r.id_producto_financiero).length,
    ventas: d.ventas.length,
    montoVentas: d.ventas.reduce((s, r) => s + Number(r.monto_total_clp || 0), 0),
    rechazadas: d.ventas_rechazadas.length,
    correcciones: f.modelo.calidad.reduce((s, q) => s + q.filas_afectadas, 0),
  };
}

function renderComparacion() {
  const ids = idsCargados();
  if (ids.length < 2) { $("#comparacion").hidden = true; return; }
  const m = Object.fromEntries(ids.map((id) => [id, metricas(fuentes[id])]));
  const pct = new Intl.NumberFormat("es-CL", { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const filas = [
    ["Filas de cartera en la fuente", "filasOrigen", fmtNum],
    ["Clientes de cartera tras el ETL", "clientes", fmtNum],
    ["Monto facturado cartera", "total", fmtCLP],
    ["Ticket promedio por cliente", "ticket", fmtCLP],
    ["Tasa de morosidad crítica (> 60 días)", "tasaCritica", pct],
    ["Clientes sin producto financiero", "sinProducto", fmtNum],
    ["Ventas válidas", "ventas", fmtNum],
    ["Monto de ventas válidas", "montoVentas", fmtCLP],
    ["Ventas rechazadas", "rechazadas", fmtNum],
    ["Valores corregidos o marcados por el ETL", "correcciones", fmtNum],
  ];
  // Se compara contra la primera fuente (Excel trabajado): la activa, o la siguiente si la activa es la base.
  const a = ids[0];
  const b = estado.fuente !== a && ids.includes(estado.fuente) ? estado.fuente : ids[1];
  const diferencia = (k, fmt) => {
    const dif = m[b][k] - m[a][k];
    if (Math.abs(dif) < 1e-9) return `<span class="muted">igual</span>`;
    const texto = fmt === pct ? `${(dif * 100).toFixed(1).replace(".", ",")} pp` : fmt.format(dif);
    return `<b class="${dif > 0 ? "dif-mas" : "dif-menos"}">${dif > 0 ? "+" : ""}${texto}</b>`;
  };

  const idsA = new Set(fuentes[a].datos.cartera_clientes.map((r) => r.id_cliente));
  const soloB = fuentes[b].datos.cartera_clientes.filter((r) => !idsA.has(r.id_cliente)).map((r) => r.id_cliente);
  const nota = soloB.length
    ? `<p class="nota-comparacion"><b>${fmtNum.format(soloB.length)} clientes</b> existen en <b>${fuentes[b].nombre}</b> pero no en
        <b>${fuentes[a].nombre}</b> (${soloB[0]} … ${soloB.at(-1)}). En los datos originales ningún RUT está repetido:
        esas filas no eran duplicados, sino las últimas del archivo, que se perdieron al filtrar "duplicados" en el Excel.</p>`
    : "";
  const sinBD = catalogo.some((f) => f.tipo === "api") && !fuentes.bd
    ? `<p class="muted">La fuente <b>PostgreSQL local</b> se agrega a esta comparación al seleccionarla con la base en línea.</p>` : "";

  $("#comparacion").hidden = false;
  $("#comparacion").innerHTML = `
    <div class="panel-header"><div><h2>Comparación entre fuentes</h2>
      <p class="muted">Mismo proceso ETL aplicado a cada fuente. Columna activa resaltada.</p></div></div>
    <div class="tabla-wrap"><table class="tabla-guia tabla-comparacion">
      <thead><tr><th>Indicador</th>${ids.map((id) => `<th class="${id === estado.fuente ? "activa" : ""}">${fuentes[id].nombre}</th>`).join("")}
        <th>Diferencia (${fuentes[b].nombre} − ${fuentes[a].nombre})</th></tr></thead>
      <tbody>${filas.map(([t, k, fmt]) => `<tr><td>${t}</td>
        ${ids.map((id) => `<td class="num ${id === estado.fuente ? "activa" : ""}">${fmt.format(m[id][k])}</td>`).join("")}
        <td class="num">${diferencia(k, fmt)}</td></tr>`).join("")}</tbody>
    </table></div>${nota}${sinBD}`;
}

// ------------------------------------------------------------------ Tablas

function renderSidebar() {
  $("#lista-tablas").innerHTML = Object.entries(tablasPorDominio()).map(([dom, tablas]) => `
    <h4 style="--dom:${COLOR_DOMINIO[dom]}">${dom}</h4>
    ${tablas.map((t) => `<button data-tabla="${t}" class="${t === estado.tabla ? "active" : ""}">
      ${t}<span>${fmtNum.format(meta(t).filas)}</span></button>`).join("")}`).join("");
  document.querySelectorAll("#lista-tablas button").forEach((b) =>
    b.addEventListener("click", () => abrirTabla(b.dataset.tabla)));
}

function abrirTabla(tabla, filtro = null) {
  Object.assign(estado, { tabla, filtro, busqueda: "", pagina: 0, orden: { columna: null, asc: true } });
  $("#buscar").value = "";
  mostrarVista("tablas");
  renderSidebar();
  renderTabla();
}

function filasVisibles() {
  let filas = estado.datos[estado.tabla];
  if (estado.filtro) filas = filas.filter((f) => f[estado.filtro.columna] === estado.filtro.valor);
  if (estado.busqueda) {
    const q = estado.busqueda.toLowerCase();
    filas = filas.filter((f) => Object.values(f).some((v) => v.toLowerCase().includes(q)));
  }
  const { columna, asc } = estado.orden;
  if (columna) {
    const numerico = ["entero"].includes(colDef(estado.tabla, columna).tipo);
    filas = [...filas].sort((a, b) => {
      const r = numerico ? Number(a[columna]) - Number(b[columna]) : a[columna].localeCompare(b[columna], "es");
      return asc ? r : -r;
    });
  }
  return filas;
}

let cacheNombres = {};
function indiceNombres(tabla, columna) {
  return (cacheNombres[`${tabla}.${columna}`] ??= new Map(
    estado.datos[tabla].filter((f) => f.nombre).map((f) => [f[columna], f.nombre])));
}

const escapar = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");

function celdaCruda(valor) {
  if (valor === "") return `<td class="muted">—</td>`;
  const [, ini, medio, fin] = valor.match(/^(\s*)(.*?)(\s*)$/s);
  const marca = (s) => s ? `<span class="espacio" title="Espacios sobrantes">${"·".repeat(s.length)}</span>` : "";
  return `<td>${marca(ini)}${escapar(medio)}${marca(fin)}</td>`;
}

function celda(col, valor) {
  if (valor === "") return `<td class="muted">—</td>`;
  if (col.fk) {
    const [tRef, cRef] = col.fk.split(".");
    const nombre = indiceNombres(tRef, cRef).get(valor);
    return `<td><a data-ref="${tRef}" data-col="${cRef}" data-val="${valor}">${valor}</a>` +
      (nombre ? `<span class="ref-nombre">${nombre}</span>` : "") + `</td>`;
  }
  if (col.tipo === "booleano") {
    return valor === "True" ? `<td class="si">Sí</td>` : `<td class="no">No</td>`;
  }
  if (col.tipo === "entero") {
    const n = Number(valor);
    return `<td class="num">${col.nombre.endsWith("_clp") ? fmtCLP.format(n) : fmtNum.format(n)}</td>`;
  }
  if (col.nombre === "nivel_riesgo") {
    const clase = valor.split(" ")[1]?.toLowerCase() ?? "";
    return `<td><span class="badge ${clase}">${valor}</span></td>`;
  }
  return `<td>${valor}</td>`;
}

function renderTabla() {
  const m = meta(estado.tabla);
  $("#tabla-titulo").textContent = estado.tabla;
  $("#tabla-desc").textContent = `${m.dominio} · ${m.descripcion}`;
  $("#descargar").href = `${fuentes[estado.fuente].dir}/${estado.tabla}.csv`;
  $("#descargar").download = `${estado.fuente}_${estado.tabla}.csv`;

  const filtro = $("#filtro-activo");
  filtro.hidden = !estado.filtro;
  if (estado.filtro) {
    filtro.innerHTML = `Filtrando <b>${estado.filtro.columna} = ${estado.filtro.valor}</b>
      <button title="Quitar filtro">✕</button>`;
    filtro.querySelector("button").onclick = () => { estado.filtro = null; estado.pagina = 0; renderTabla(); };
  }

  const filas = filasVisibles();
  const paginas = Math.max(1, Math.ceil(filas.length / POR_PAGINA));
  estado.pagina = Math.min(estado.pagina, paginas - 1);
  const desde = estado.pagina * POR_PAGINA;
  const pagina = filas.slice(desde, desde + POR_PAGINA);

  const cab = m.columnas.map((c) => {
    const flecha = estado.orden.columna === c.nombre ? `<span class="orden">${estado.orden.asc ? "▲" : "▼"}</span>` : "";
    const llave = c.pk ? `<span class="key pk">PK</span> ` : c.fk ? `<span class="key fk">FK</span> ` : "";
    const ayuda = c.descripcion + (c.fk ? ` → ${c.fk}` : "");
    return `<th data-col="${c.nombre}" title="${ayuda}">${llave}${c.nombre} ${flecha}</th>`;
  }).join("");
  const cuerpo = pagina.length
    ? pagina.map((f) => `<tr>${m.columnas.map((c) => m.origen ? celdaCruda(f[c.nombre]) : celda(c, f[c.nombre])).join("")}</tr>`).join("")
    : `<tr><td colspan="${m.columnas.length}" class="muted">Sin resultados</td></tr>`;
  $("#tabla").innerHTML = `<thead><tr>${cab}</tr></thead><tbody>${cuerpo}</tbody>`;

  $("#pag-info").textContent = filas.length
    ? `Mostrando ${desde + 1}–${desde + pagina.length} de ${fmtNum.format(filas.length)} filas`
    : "0 filas";
  $("#pag-ant").disabled = estado.pagina === 0;
  $("#pag-sig").disabled = estado.pagina >= paginas - 1;

  document.querySelectorAll("#tabla th").forEach((th) => th.addEventListener("click", () => {
    const col = th.dataset.col;
    estado.orden = { columna: col, asc: estado.orden.columna === col ? !estado.orden.asc : true };
    renderTabla();
  }));
  document.querySelectorAll("#tabla a[data-ref]").forEach((a) => a.addEventListener("click", () =>
    abrirTabla(a.dataset.ref, { columna: a.dataset.col, valor: a.dataset.val })));
}

// ------------------------------------------------------------------ Modelo de datos

// Posición [columna, fila] de cada tabla: Cartera a la izquierda, Geografía al centro, Comercial a la derecha.
const LAYOUT_ER = {
  productos_financieros: [0, 0], cuentas: [1, 0], regiones: [2, 0], segmentos: [3, 0], categorias: [4, 0],
  estados_cuenta: [0, 1], cartera_clientes: [1, 1], ciudades: [2, 1], clientes: [3, 1], productos: [4, 1],
  ventas_rechazadas: [0, 2], comunas: [2, 2], sucursales: [3, 2], ventas: [4, 2],
  zonas_comerciales: [3, 3],
};
const ER = { ancho: 200, anchoCompleto: 236, sepX: 56, sepY: 54, cab: 28, fila: 20, margen: 16, zonaPad: 12, zonaTop: 26 };

function columnasER(t, completo) {
  const cols = meta(t).columnas;
  return completo ? cols : cols.filter((c) => c.pk || c.fk);
}

function geometriaER(tablas, completo) {
  let extra = 0;
  const pos = Object.fromEntries(tablas.map((t) => [t, LAYOUT_ER[t] ?? [extra % 5, 4 + Math.floor(extra++ / 5)]]));
  const ocultas = (t) => meta(t).columnas.length - columnasER(t, completo).length;
  const alto = (t) => ER.cab + 6 + (columnasER(t, completo).length + (ocultas(t) ? 1 : 0)) * ER.fila + 4;
  const nFilas = Math.max(...tablas.map((t) => pos[t][1])) + 1;
  const altoFila = Array.from({ length: nFilas }, (_, r) => Math.max(0, ...tablas.filter((t) => pos[t][1] === r).map(alto)));
  const yFila = [];
  let y = ER.margen + ER.zonaTop;
  for (let r = 0; r < nFilas; r++) { yFila[r] = y; y += altoFila[r] + ER.sepY; }
  const nCols = Math.max(...tablas.map((t) => pos[t][0])) + 1;
  const w = completo ? ER.anchoCompleto : ER.ancho;
  const caja = Object.fromEntries(tablas.map((t) => [t, {
    x: ER.margen + ER.zonaPad + pos[t][0] * (w + ER.sepX), y: yFila[pos[t][1]], w, h: alto(t),
  }]));
  return {
    pos, caja, ocultas,
    ancho: 2 * (ER.margen + ER.zonaPad) + nCols * w + (nCols - 1) * ER.sepX,
    alto: y - ER.sepY + ER.zonaPad + ER.margen,
  };
}

function svgTablaER(t, g, completo) {
  const { x, y, w, h } = g.caja[t];
  const m = meta(t);
  const cols = columnasER(t, completo);
  const filas = cols.map((c, i) => {
    const cy = ER.cab + 6 + i * ER.fila;
    const tipo = c.pk ? "pk" : c.fk ? "fk" : "";
    const badge = tipo ? `<rect class="er-badge ${tipo}" x="8" y="${cy + 3}" width="22" height="14" rx="3"/>
      <text class="er-badge-txt ${tipo}" x="19" y="${cy + 13.5}" text-anchor="middle">${tipo.toUpperCase()}</text>` : "";
    return `<g><title>${c.nombre}: ${c.descripcion}${c.fk ? ` → ${c.fk}` : ""}</title>${badge}
      <text class="er-col" x="${tipo ? 36 : 12}" y="${cy + 14}">${c.nombre}</text>
      ${completo ? `<text class="er-tipo" x="${w - 10}" y="${cy + 14}" text-anchor="end">${c.tipo}</text>` : ""}</g>`;
  }).join("");
  const n = g.ocultas(t);
  const mas = n ? `<text class="er-mas" x="12" y="${ER.cab + 6 + cols.length * ER.fila + 14}">+ ${n} columna${n > 1 ? "s" : ""} más</text>` : "";
  return `<g class="er-tabla ${m.dominio === "Control de calidad" ? "cuarentena" : ""}" data-tabla="${t}"
      transform="translate(${x},${y})" style="--dom:${COLOR_DOMINIO[m.dominio]}">
    <title>${t} · ${m.descripcion}</title>
    <rect class="er-caja" width="${w}" height="${h}" rx="8"/>
    <path class="er-cab" d="M0,8 a8,8 0 0 1 8,-8 h${w - 16} a8,8 0 0 1 8,8 v${ER.cab - 8} h-${w} z"/>
    <text class="er-titulo" x="10" y="18.5">${t}</text>
    <text class="er-conteo" x="${w - 10}" y="18.5" text-anchor="end">${fmtNum.format(m.filas)}</text>
    ${filas}${mas}</g>`;
}

function relacionesER(tablas, g, completo) {
  const yCol = (t, nombre) => {
    const i = Math.max(0, columnasER(t, completo).findIndex((c) => c.nombre === nombre));
    return g.caja[t].y + ER.cab + 6 + i * ER.fila + ER.fila / 2;
  };
  const rels = [];
  for (const t of tablas) {
    for (const c of meta(t).columnas) {
      if (!c.fk) continue;
      const [ref, refCol] = c.fk.split(".");
      if (!g.caja[ref]) continue;
      const a = g.caja[t], b = g.caja[ref];
      let d;
      if (g.pos[t][0] === g.pos[ref][0]) {
        const x = a.x + a.w / 2;
        d = a.y > b.y ? `M${x},${a.y} L${x},${b.y + b.h}` : `M${x},${a.y + a.h} L${x},${b.y}`;
      } else {
        const haciaDerecha = g.pos[ref][0] > g.pos[t][0];
        const x1 = haciaDerecha ? a.x + a.w : a.x, y1 = yCol(t, c.nombre);
        const x2 = haciaDerecha ? b.x : b.x + b.w, y2 = yCol(ref, refCol);
        const dx = (x2 - x1) / 2;
        d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
      }
      rels.push(`<path class="er-rel" data-desde="${t}" data-hacia="${ref}" d="${d}"
        marker-start="url(#er-muchos)" marker-end="url(#er-uno)"><title>${t}.${c.nombre} → ${c.fk}</title></path>`);
    }
  }
  return rels.join("");
}

function zonasER(tablas, g) {
  const grupos = {};
  for (const t of tablas) (grupos[meta(t).dominio] ??= []).push(g.caja[t]);
  return Object.entries(grupos).map(([dom, cajas]) => {
    const x = Math.min(...cajas.map((c) => c.x)) - ER.zonaPad;
    const y = Math.min(...cajas.map((c) => c.y)) - ER.zonaTop;
    const x2 = Math.max(...cajas.map((c) => c.x + c.w)) + ER.zonaPad;
    const y2 = Math.max(...cajas.map((c) => c.y + c.h)) + ER.zonaPad;
    return `<g class="er-zona" data-dominio="${dom}" style="--dom:${COLOR_DOMINIO[dom]}">
      <rect x="${x}" y="${y}" width="${x2 - x}" height="${y2 - y}" rx="12"/>
      <text x="${x + 12}" y="${y + 17}">${dom}</text></g>`;
  }).join("");
}

function renderDiagrama() {
  const completo = $("#er-completo").checked;
  const tablas = tablasModelo();
  const g = geometriaER(tablas, completo);
  const svg = `<svg viewBox="0 0 ${g.ancho} ${g.alto}" style="max-width:${g.ancho}px" role="img" aria-label="Diagrama entidad-relación">
    <defs>
      <marker id="er-uno" viewBox="0 0 12 12" refX="12" refY="6" markerWidth="12" markerHeight="12" markerUnits="userSpaceOnUse" orient="auto">
        <path d="M5,1 L5,11 M8.5,1 L8.5,11" stroke="#8f99ad" stroke-width="1.4"/></marker>
      <marker id="er-muchos" viewBox="0 0 12 12" refX="12" refY="6" markerWidth="12" markerHeight="12" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
        <path d="M3,6 L12,1 M3,6 L12,6 M3,6 L12,11" stroke="#8f99ad" stroke-width="1.4" fill="none"/></marker>
    </defs>
    ${zonasER(tablas, g)}
    ${relacionesER(tablas, g, completo)}
    ${tablas.map((t) => svgTablaER(t, g, completo)).join("")}
  </svg>`;
  const cont = $("#diagrama");
  cont.innerHTML = svg;
  const el = cont.querySelector("svg");

  const dominio = $("#dominio-er").value;
  const visibles = new Set(tablasDelDominio(dominio));
  if (dominio !== "Todos") {
    el.querySelectorAll(".er-tabla").forEach((n) => n.classList.toggle("atenuada", !visibles.has(n.dataset.tabla)));
    el.querySelectorAll(".er-rel").forEach((n) =>
      n.classList.toggle("atenuada", !visibles.has(n.dataset.desde) || !visibles.has(n.dataset.hacia)));
    el.querySelectorAll(".er-zona").forEach((n) =>
      n.classList.toggle("atenuada", !tablas.some((t) => visibles.has(t) && meta(t).dominio === n.dataset.dominio)));
  }

  el.querySelectorAll(".er-tabla").forEach((n) => {
    const t = n.dataset.tabla;
    n.addEventListener("mouseenter", () => {
      el.classList.add("enfocado");
      const relacionadas = new Set([t]);
      el.querySelectorAll(".er-rel").forEach((r) => {
        const toca = r.dataset.desde === t || r.dataset.hacia === t;
        r.classList.toggle("rel", toca);
        if (toca) { relacionadas.add(r.dataset.desde); relacionadas.add(r.dataset.hacia); }
      });
      el.querySelectorAll(".er-tabla").forEach((o) => o.classList.toggle("rel", relacionadas.has(o.dataset.tabla)));
    });
    n.addEventListener("mouseleave", () => el.classList.remove("enfocado"));
    n.addEventListener("click", () => abrirTabla(t));
  });
}

function tablasDelDominio(dominio) {
  const todas = tablasModelo();
  if (dominio === "Todos") return todas;
  const set = new Set(todas.filter((t) => meta(t).dominio === dominio));
  const pendientes = [...set];
  while (pendientes.length) {
    for (const c of meta(pendientes.pop()).columnas) {
      const ref = c.fk?.split(".")[0];
      if (ref && !set.has(ref)) { set.add(ref); pendientes.push(ref); }
    }
  }
  return todas.filter((t) => set.has(t));
}

function renderModelo() {
  const dominios = ["Todos", ...Object.keys(tablasPorDominio(false)).filter((d) => d !== "Geografía")];
  const actual = $("#dominio-er").value;
  $("#dominio-er").innerHTML = dominios.map((d) =>
    `<option value="${d}" ${d === actual ? "selected" : ""}>${d === "Todos" ? "Resaltar: todas las áreas" : `Resaltar: ${d}`}</option>`).join("");
  $("#dominio-er").onchange = renderDiagrama;
  $("#er-completo").onchange = renderDiagrama;
  renderDiagrama();

  $("#diccionario").innerHTML = Object.entries(estado.modelo.tablas).filter(([, m]) => !m.origen).map(([nombre, m]) => `
    <div class="tarjeta" style="--dom:${COLOR_DOMINIO[m.dominio]}">
      <div class="cab"><h3>${nombre}</h3><span class="dominio-tag">${m.dominio}</span></div>
      <p class="muted">${m.descripcion}</p>
      <ul>${m.columnas.map((c) => `
        <li title="${c.descripcion}">
          ${c.pk ? `<span class="key pk">PK</span>` : c.fk ? `<span class="key fk">FK</span>` : ""}
          ${c.nombre}${c.fk ? ` <span class="muted">→ ${c.fk}</span>` : ""}
          <span class="tipo">${c.tipo}</span>
        </li>`).join("")}</ul>
    </div>`).join("");
}

// ------------------------------------------------------------------ Calidad

function renderCalidad() {
  const filas = estado.modelo.calidad;
  $("#tabla-calidad").innerHTML = `
    <thead><tr><th>Tabla</th><th>Columna</th><th>Problema detectado</th><th>Filas afectadas</th><th>Acción</th></tr></thead>
    <tbody>${filas.map((q) => `<tr>
      <td><a data-ref="${q.tabla}">${q.tabla}</a></td><td><code>${q.columna}</code></td><td>${q.problema}</td>
      <td class="num">${fmtNum.format(q.filas_afectadas)}</td><td>${q.accion}</td></tr>`).join("")}</tbody>`;
  document.querySelectorAll("#tabla-calidad a[data-ref]").forEach((a) =>
    a.addEventListener("click", () => abrirTabla(a.dataset.ref)));
}

// ------------------------------------------------------------------ Guía paso a paso

const guia = { estados: new Set(), base: [] };

function prepararGuia() {
  const d = estado.datos;
  const porId = (tabla, col) => new Map(d[tabla].map((f) => [f[col], f]));
  const clientes = porId("cartera_clientes", "id_cliente");
  const comunas = porId("comunas", "id_comuna");
  const ciudades = porId("ciudades", "id_ciudad");
  const regiones = porId("regiones", "id_region");
  const estados = porId("estados_cuenta", "id_estado");
  const productos = porId("productos_financieros", "id_producto_financiero");
  guia.base = d.cuentas.map((c) => {
    const comuna = comunas.get(clientes.get(c.id_cliente).id_comuna);
    const region = regiones.get(ciudades.get(comuna.id_ciudad).id_region);
    return {
      producto: productos.get(c.id_producto_financiero)?.nombre ?? "(Sin producto)",
      estado: estados.get(c.id_estado).nombre,
      region: region.nombre,
      comuna: comuna.nombre,
      monto: Number(c.monto_facturado_clp),
      dias: Number(c.dias_morosidad),
    };
  });
}

function agrupar(filas, clave) {
  const grupos = new Map();
  for (const f of filas) {
    const k = clave(f);
    if (!grupos.has(k)) grupos.set(k, []);
    grupos.get(k).push(f);
  }
  return grupos;
}

const suma = (filas) => filas.reduce((s, f) => s + f.monto, 0);
const promedioDias = (filas) => filas.reduce((s, f) => s + f.dias, 0) / (filas.length || 1);
const fmtDec = new Intl.NumberFormat("es-CL", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmtPct = new Intl.NumberFormat("es-CL", { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });

function tablaHTML(cabeceras, filas, total) {
  const tr = (celdas, clase = "") => `<tr class="${clase}">${celdas.map((c, i) =>
    `<td class="${i > 0 && /^[\d$]/.test(String(c).replace(/<[^>]+>/g, "")) ? "num" : ""}">${c}</td>`).join("")}</tr>`;
  return `<div class="tabla-wrap"><table class="tabla-guia">
    <thead><tr>${cabeceras.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${filas.map((f) => Array.isArray(f) ? tr(f) : tr(f.celdas, f.clase)).join("")}
    ${total ? tr(total, "total") : ""}</tbody></table></div>`;
}

function renderGuiaFiltrada() {
  const filas = guia.estados.size ? guia.base.filter((f) => guia.estados.has(f.estado)) : guia.base;

  const porProducto = [...agrupar(filas, (f) => f.producto)].sort((a, b) => a[0].localeCompare(b[0], "es"));
  $("#g-pivot1").innerHTML = tablaHTML(
    ["Producto", "Suma de Monto_Facturado_CLP", "Promedio de Dias_Morosidad"],
    porProducto.map(([p, fs]) => [p, fmtCLP.format(suma(fs)), fmtDec.format(promedioDias(fs))]),
    ["Total general", fmtCLP.format(suma(filas)), fmtDec.format(promedioDias(filas))]);

  const porRegion = [...agrupar(filas, (f) => f.region)].sort((a, b) => a[0].localeCompare(b[0], "es"));
  const filasRegion = porRegion.flatMap(([r, fs]) => [
    { celdas: [r, fmtCLP.format(suma(fs)), fmtNum.format(fs.length)], clase: "subtotal" },
    ...[...agrupar(fs, (f) => f.comuna)].sort((a, b) => a[0].localeCompare(b[0], "es"))
      .map(([c, fc]) => ({ celdas: [`<span class="sangria">${c}</span>`, fmtCLP.format(suma(fc)), fmtNum.format(fc.length)] })),
  ]);
  $("#g-pivot2").innerHTML = tablaHTML(
    ["Región / Comuna", "Suma de Monto_Facturado_CLP", "Cuenta de ID_Cliente"],
    filasRegion, ["Total general", fmtCLP.format(suma(filas)), fmtNum.format(filas.length)]);

  const total = suma(filas);
  const criticos = filas.filter((f) => f.dias > 60).length;
  $("#g-kpis").innerHTML = [
    ["Ingresos totales facturados", fmtCLP.format(total), `${fmtNum.format(filas.length)} clientes`],
    ["Ticket promedio por cliente", fmtCLP.format(filas.length ? total / filas.length : 0), "Ingresos ÷ N° clientes"],
    ["Tasa de morosidad crítica", fmtPct.format(filas.length ? criticos / filas.length : 0), `${fmtNum.format(criticos)} clientes con más de 60 días`],
  ].map(([t, v, s]) => `<div class="kpi"><div class="muted">${t}</div><div class="valor">${v}</div><div class="muted">${s}</div></div>`).join("");

  const barras = porRegion.map(([r, fs]) => [r.replace(/^Región (del |de la |de )?/, ""), suma(fs)]).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...barras.map((b) => b[1]));
  $("#g-chart").innerHTML = `<div class="barras-titulo">Total facturado por región${guia.estados.size ? ` · ${[...guia.estados].join(", ")}` : ""}</div>` +
    barras.map(([r, v]) => `<div class="barra-fila"><span class="barra-etiqueta">${r}</span>
      <div class="barra-pista"><div class="barra" style="width:${(v / max) * 100}%"></div></div>
      <span class="barra-valor">${fmtCLP.format(v)}</span></div>`).join("");

  document.querySelectorAll("#g-slicer button").forEach((b) =>
    b.classList.toggle("active", b.dataset.estado ? guia.estados.has(b.dataset.estado) : guia.estados.size === 0));
}

function renderGuia() {
  prepararGuia();
  const estados = estado.datos.estados_cuenta.map((e) => e.nombre);
  $("#g-slicer").innerHTML = `<span class="slicer-titulo">Estado_Cuenta</span>
    <button data-estado="">Todos</button>${estados.map((e) => `<button data-estado="${e}">${e}</button>`).join("")}
    <span class="muted">Ctrl + clic para elegir varios</span>`;
  document.querySelectorAll("#g-slicer button").forEach((b) => b.addEventListener("click", (ev) => {
    const e = b.dataset.estado;
    if (!e) guia.estados.clear();
    else if (ev.ctrlKey || ev.metaKey) guia.estados.has(e) ? guia.estados.delete(e) : guia.estados.add(e);
    else guia.estados = new Set(guia.estados.size === 1 && guia.estados.has(e) ? [] : [e]);
    renderGuiaFiltrada();
  }));
  renderGuiaFiltrada();

  const d = estado.datos;
  const sucursales = new Map(d.sucursales.map((s) => [s.id_sucursal, s]));
  const zonas = new Map(d.zonas_comerciales.map((z) => [z.id_zona, z.nombre]));
  const zonaDe = (idSuc) => zonas.get(sucursales.get(idSuc)?.id_zona) ?? "Sin sucursal";
  const ejemplos = [
    ...d.ventas.slice(0, 6).map((v) => [v.id_transaccion, v.id_sucursal, `<mark>${zonaDe(v.id_sucursal)}</mark>`, v.id_producto,
      fmtNum.format(v.cantidad), fmtCLP.format(v.precio_unitario_clp), `<b>${fmtCLP.format(v.monto_total_clp)}</b>`]),
    ...d.ventas_rechazadas.filter((v) => v.id_producto === "PROD-999").map((v) => ({
      clase: "alerta",
      celdas: [v.id_transaccion, v.id_sucursal, zonaDe(v.id_sucursal), v.id_producto, fmtNum.format(v.cantidad),
        `${fmtCLP.format(0)} ⚠`, `${fmtCLP.format(0)} · producto inexistente`],
    })),
  ];
  $("#g-cruce").innerHTML = tablaHTML(
    ["ID_Transaccion", "ID_Sucursal", "Zona_Comercial", "ID_Producto", "Cantidad", "Precio_Unitario_CLP", "Total_Facturado_CLP"], ejemplos);

  const porZona = [...agrupar(d.ventas.map((v) => ({ zona: zonaDe(v.id_sucursal), monto: Number(v.monto_total_clp) })), (v) => v.zona)]
    .sort((a, b) => suma(b[1]) - suma(a[1]));
  const totalVentas = suma(d.ventas.map((v) => ({ monto: Number(v.monto_total_clp) })));
  $("#g-zonas").innerHTML = tablaHTML(["Zona_Comercial", "N° ventas", "Suma de Total_Facturado_CLP"],
    porZona.map(([z, vs]) => [z, fmtNum.format(vs.length), fmtCLP.format(suma(vs))]),
    ["Total general", fmtNum.format(d.ventas.length), fmtCLP.format(totalVentas)]);
}

// ------------------------------------------------------------------ Navegación

function mostrarVista(vista) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.vista === vista));
  document.querySelectorAll(".vista").forEach((s) => s.classList.toggle("active", s.id === `vista-${vista}`));
  history.replaceState(null, "", `#${vista}`);
  if (vista === "tablas" && !estado.tabla) abrirTabla("cartera_clientes");
}

const FORMATOS_EXPORTAR = [
  { id: "csv", etiqueta: "CSV", color: "#1a9e5c", titulo: "CSV (.zip)",
    detalle: "Un archivo CSV por tabla, más los datos de origen y modelo.json." },
  { id: "excel", etiqueta: "XLSX", color: "#1d6f42", titulo: "Excel (.xlsx)",
    detalle: "Una hoja por tabla, con índice, hoja de calidad, filtros y formatos." },
  { id: "sql", etiqueta: "SQL", color: "#336791", titulo: "PostgreSQL (.sql)",
    detalle: "Crea el esquema con tablas, PK, FK y comentarios, e inserta los datos." },
];

function renderMenuExportar() {
  const f = fuentes[estado.fuente];
  const disponibles = FORMATOS_EXPORTAR.filter((fmt) => f.exportaciones?.[fmt.id]);
  $("#btn-exportar").disabled = !disponibles.length;
  $("#menu-exportar").innerHTML = `<div class="menu-cab">Descargar todas las tablas de <b>${f.nombre}</b></div>` +
    disponibles.map((fmt) => {
      const ruta = `${f.dir}/${f.exportaciones[fmt.id]}`;
      return `<a role="menuitem" href="${ruta}" download="${ruta.split("/").pop()}">
        <span class="formato" style="--f:${fmt.color}">${fmt.etiqueta}</span>
        <span><b>${fmt.titulo}</b><small>${fmt.detalle}</small></span></a>`;
    }).join("");
}

function alternarMenuExportar(abrir) {
  const menu = $("#menu-exportar");
  const abierto = abrir ?? menu.hidden;
  menu.hidden = !abierto;
  $("#btn-exportar").setAttribute("aria-expanded", abierto);
}

function usarFuente(id) {
  const f = fuentes[id];
  Object.assign(estado, { fuente: id, modelo: f.modelo, datos: f.datos });
  cacheNombres = {};
  localStorage.setItem("fuente", id);
  document.body.dataset.fuente = id;

  const generado = new Date(f.modelo.generado).toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" });
  const info = $("#fuente-info");
  info.style.setProperty("--c", ESTILO_FUENTE[id]?.color ?? "var(--primary)");
  const esApi = f.tipo === "api";
  info.innerHTML = (api.aviso ? `<span class="aviso-fuente">${escapar(api.aviso)}</span>` : "") +
    (esApi ? `<button id="recargar-bd" class="recargar" title="Volver a leer las tablas de la base">↻ Recargar</button>` : "") +
    `<span class="etiqueta-fuente">Viendo: ${f.nombre}</span>${f.descripcion}
    · ${fmtNum.format(f.datos.cartera_clientes.length)} clientes de cartera · ${esApi ? "leído" : "generado"} ${generado}`;
  info.title = `${f.descripcion}\nOrigen: ${f.modelo.fuente}\n${esApi ? "Leído" : "Generado"}: ${generado}`;
  $("#recargar-bd")?.addEventListener("click", () => seleccionarFuente("bd"));
  api.aviso = "";
  renderSelector();
  document.querySelectorAll(".fuente-activa").forEach((el) => { el.textContent = f.nombre; });
  renderMenuExportar();

  renderResumen();
  renderModelo();
  renderCalidad();
  renderGuia();
  if (estado.tabla) {
    if (!estado.datos[estado.tabla]) estado.tabla = "cartera_clientes";
    renderSidebar();
    renderTabla();
  }
}

async function iniciar() {
  try {
    await Promise.all([cargar(), consultarEstado()]);
  } catch (e) {
    $("main").innerHTML = `<div class="error"><b>No se pudieron cargar los datos.</b><br>
      Abre la página mediante el servidor local: <code>python api/servidor.py</code>
      y luego visita <code>http://localhost:8000</code>.<br><small>${e.message}</small></div>`;
    return;
  }
  setInterval(vigilarBD, INTERVALO_ESTADO);

  $("#btn-exportar").addEventListener("click", (ev) => { ev.stopPropagation(); alternarMenuExportar(); });
  $("#menu-exportar").addEventListener("click", (ev) => { if (ev.target.closest("a")) alternarMenuExportar(false); });
  document.addEventListener("click", (ev) => { if (!ev.target.closest(".exportar")) alternarMenuExportar(false); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") alternarMenuExportar(false); });

  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => mostrarVista(b.dataset.vista)));
  $("#buscar").addEventListener("input", (e) => { estado.busqueda = e.target.value; estado.pagina = 0; renderTabla(); });
  $("#pag-ant").addEventListener("click", () => { estado.pagina--; renderTabla(); });
  $("#pag-sig").addEventListener("click", () => { estado.pagina++; renderTabla(); });
  document.querySelectorAll(".indice a").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    document.querySelector(a.getAttribute("href")).scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  const pedida = new URLSearchParams(location.search).get("fuente") ?? localStorage.getItem("fuente");
  usarFuente(fuentes[pedida] ? pedida : idsCargados()[0]);
  if (pedida === "bd" && bdConectada()) await seleccionarFuente("bd");
  const inicial = location.hash.slice(1);
  if (document.getElementById(`vista-${inicial}`)) mostrarVista(inicial);
}

iniciar();
