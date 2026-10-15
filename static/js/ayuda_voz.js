// static/js/ayuda_voz.js — ventana de ayuda cuando el reconocimiento de voz falla.
// Necesita el bloque {% include '_modal_voz.html' %} en la página.
//
//   explicarFalloVoz(error)   error = event.error de SpeechRecognition; abre la ventana
//                             si el fallo tiene arreglo por parte del usuario (devuelve true)
//   mostrarModalVoz(motivo)   "http" | "permiso" | "red"

function explicarFalloVoz(error) {
    if (error === "not-allowed" || error === "service-not-allowed") { mostrarModalVoz(window.isSecureContext ? "permiso" : "http"); return true; }
    if (error === "network") { mostrarModalVoz("red"); return true; }
    return false;
}

//   http    : la página va por http:// y Chrome bloquea el micrófono (lo normal en la red local)
//   permiso : ya es https/localhost pero el permiso del micrófono está denegado
//   red     : el móvil no tiene salida a Internet y Chrome no puede reconocer la voz
function mostrarModalVoz(motivo) {
    const cab = document.getElementById("modal-voz-cabecera");
    const txt = document.getElementById("modal-voz-texto");
    const pasos = document.getElementById("modal-voz-pasos-http");
    const nota = document.getElementById("modal-voz-nota");
    document.getElementById("modal-voz-origen").textContent = origenesParaChrome();
    pasos.hidden = motivo !== "http";
    nota.hidden = motivo !== "http";
    if (motivo === "http") {
        cab.textContent = _("La voz no funciona todavía en este móvil");
        txt.textContent = _("No es un fallo de ScopePower: Chrome solo permite el micrófono en direcciones https, y esta va por http. Se arregla en un minuto diciéndole a Chrome que trate esta dirección como segura:");
    } else if (motivo === "ubicacion") {
        pasos.hidden = false; nota.hidden = false;
        cab.textContent = _("La ubicación del móvil no está disponible");
        txt.textContent = _("Chrome solo da la posición GPS en direcciones https, y esta va por http. Se arregla igual que la voz, diciéndole a Chrome que trate esta dirección como segura (y aceptando el permiso de ubicación cuando lo pida):");
    } else if (motivo === "red") {
        cab.textContent = _("La voz necesita Internet en el móvil");
        txt.textContent = _("Chrome reconoce la voz en los servidores de Google. El móvil está conectado a la wifi del telescopio pero sin salida a Internet: activa los datos móviles (Chrome los usará para la voz) o conéctate a una wifi con Internet.");
    } else {
        cab.textContent = _("El micrófono está bloqueado");
        txt.textContent = _("Chrome tiene denegado el permiso del micrófono para esta dirección. Toca el candado o el icono de ajustes junto a la dirección, entra en Permisos y permite el Micrófono; después recarga la página.");
    }
    document.getElementById("modal-voz").classList.add("abierto");
}
function cerrarModalVoz() { document.getElementById("modal-voz").classList.remove("abierto"); }
function copiarTexto(id, boton) {
    const texto = document.getElementById(id).textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(texto) : Promise.reject()).then(() => {
        boton.textContent = _("Copiado"); setTimeout(() => boton.textContent = _("Copiar"), 1500);
    }).catch(() => { window.prompt(_("Copia esta dirección:"), texto); });
}
document.addEventListener("keydown", e => { if (e.key === "Escape") cerrarModalVoz(); });

// Dirección (o direcciones) que hay que declarar seguras en chrome://flags. En la wifi del telescopio
// (192.168.4.x, el SynScan es .1) el mini PC recibe .2 o .3 según quién se conecte antes: el log del
// mini PC muestra la tablet en .3 unos días y en .2 otros. La opción de Chrome admite varias separadas
// por comas, así vale siempre.
function origenesParaChrome() {
    if (!/^192\.168\.4\.\d+$/.test(location.hostname)) return location.origin;
    const puerto = location.port ? ":" + location.port : "";
    return [2, 3, 4, 5].map(n => `http://192.168.4.${n}${puerto}`).join(",");
}
