const DATA_DIR = "data";
const POR_PAGINA = 25;
const COLOR_DOMINIO = {
  "Geografía": "var(--geo)",
  "Cartera financiera": "var(--fin)",
  "Comercial": "var(--com)",
  "Control de calidad": "var(--qa)",
};
const TIPO_MERMAID = { texto: "string", entero: "int", booleano: "bool", fecha: "date" };

const estado = {
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

async function cargar() {
  const resp = await fetch(`${DATA_DIR}/modelo.json`);
  if (!resp.ok) throw new Error(`No se pudo leer ${DATA_DIR}/modelo.json`);
  estado.modelo = await resp.json();
  await Promise.all(Object.keys(estado.modelo.tablas).map(async (nombre) => {
    const r = await fetch(`${DATA_DIR}/${nombre}.csv`);
    estado.datos[nombre] = parseCSV(await r.text());
  }));
}

function tablasPorDominio() {
  const grupos = {};
  for (const [nombre, meta] of Object.entries(estado.modelo.tablas)) {
    (grupos[meta.dominio] ??= []).push(nombre);
  }
  return grupos;
}

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

  $("#tarjetas").innerHTML = Object.entries(estado.modelo.tablas).map(([nombre, m]) => `
    <div class="tarjeta clickable" data-tabla="${nombre}" style="--dom:${COLOR_DOMINIO[m.dominio]}">
      <div class="cab"><h3>${nombre}</h3><span class="dominio-tag">${m.dominio}</span></div>
      <p class="muted">${m.descripcion}</p>
      <p><b>${fmtNum.format(m.filas)}</b> filas · ${m.columnas.length} columnas</p>
    </div>`).join("");
  document.querySelectorAll("#tarjetas .tarjeta").forEach((el) =>
    el.addEventListener("click", () => abrirTabla(el.dataset.tabla)));
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

const cacheNombres = {};
function indiceNombres(tabla, columna) {
  return (cacheNombres[`${tabla}.${columna}`] ??= new Map(
    estado.datos[tabla].filter((f) => f.nombre).map((f) => [f[columna], f.nombre])));
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
  $("#descargar").href = `${DATA_DIR}/${estado.tabla}.csv`;

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
    ? pagina.map((f) => `<tr>${m.columnas.map((c) => celda(c, f[c.nombre])).join("")}</tr>`).join("")
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

function definicionMermaid(tablas) {
  const lineas = ["erDiagram"];
  for (const t of tablas) {
    lineas.push(`  ${t} {`);
    for (const c of meta(t).columnas) {
      const llave = c.pk ? " PK" : c.fk ? " FK" : "";
      lineas.push(`    ${TIPO_MERMAID[c.tipo] ?? "string"} ${c.nombre}${llave}`);
    }
    lineas.push("  }");
  }
  for (const t of tablas) {
    for (const c of meta(t).columnas) {
      if (!c.fk) continue;
      const ref = c.fk.split(".")[0];
      if (tablas.includes(ref)) lineas.push(`  ${ref} ||--o{ ${t} : "${c.nombre}"`);
    }
  }
  return lineas.join("\n");
}

function tablasDelDominio(dominio) {
  const todas = Object.keys(estado.modelo.tablas);
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

let mermaid = null;
async function renderDiagrama() {
  const dominio = $("#dominio-er").value;
  const cont = $("#diagrama");
  try {
    mermaid ??= (await import("https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs")).default;
    mermaid.initialize({ startOnLoad: false, theme: "neutral", er: { useMaxWidth: false } });
    const { svg } = await mermaid.render(`er-${Date.now()}`, definicionMermaid(tablasDelDominio(dominio)));
    cont.innerHTML = svg;
  } catch (e) {
    cont.innerHTML = `<div class="error">No se pudo dibujar el diagrama (requiere conexión para cargar Mermaid).
      El diccionario de datos de abajo describe las mismas relaciones.<br><small>${e.message}</small></div>`;
  }
}

function renderModelo() {
  const dominios = ["Todos", ...Object.keys(tablasPorDominio()).filter((d) => d !== "Geografía")];
  $("#dominio-er").innerHTML = dominios.map((d) => `<option>${d}</option>`).join("");
  $("#dominio-er").onchange = renderDiagrama;

  $("#diccionario").innerHTML = Object.entries(estado.modelo.tablas).map(([nombre, m]) => `
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

// ------------------------------------------------------------------ Navegación

let diagramaDibujado = false;
function mostrarVista(vista) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.vista === vista));
  document.querySelectorAll(".vista").forEach((s) => s.classList.toggle("active", s.id === `vista-${vista}`));
  history.replaceState(null, "", `#${vista}`);
  if (vista === "tablas" && !estado.tabla) abrirTabla("cartera_clientes");
  if (vista === "modelo" && !diagramaDibujado) { diagramaDibujado = true; renderDiagrama(); }
}

async function iniciar() {
  try {
    await cargar();
  } catch (e) {
    $("main").innerHTML = `<div class="error"><b>No se pudieron cargar los datos.</b><br>
      Abre la página mediante un servidor local, por ejemplo: <code>python -m http.server 8000 -d web</code>
      y luego visita <code>http://localhost:8000</code>.<br><small>${e.message}</small></div>`;
    return;
  }
  const generado = new Date(estado.modelo.generado).toLocaleString("es-CL");
  $("#fuente").textContent = `Fuente: ${estado.modelo.fuente} · generado ${generado}`;

  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => mostrarVista(b.dataset.vista)));
  $("#buscar").addEventListener("input", (e) => { estado.busqueda = e.target.value; estado.pagina = 0; renderTabla(); });
  $("#pag-ant").addEventListener("click", () => { estado.pagina--; renderTabla(); });
  $("#pag-sig").addEventListener("click", () => { estado.pagina++; renderTabla(); });

  renderResumen();
  renderModelo();
  renderCalidad();
  const inicial = location.hash.slice(1);
  if (document.getElementById(`vista-${inicial}`)) mostrarVista(inicial);
}

iniciar();
