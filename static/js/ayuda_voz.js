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
    document.getElementById("modal-voz-origen").textContent = location.origin;
    pasos.hidden = motivo !== "http";
    nota.hidden = motivo !== "http";
    if (motivo === "http") {
        cab.textContent = "La voz no funciona todavía en este móvil";
        txt.textContent = "No es un fallo de ScopePower: Chrome solo permite el micrófono en direcciones https, y esta va por http. Se arregla en un minuto diciéndole a Chrome que trate esta dirección como segura:";
    } else if (motivo === "red") {
        cab.textContent = "La voz necesita Internet en el móvil";
        txt.textContent = "Chrome reconoce la voz en los servidores de Google. El móvil está conectado a la wifi del telescopio pero sin salida a Internet: activa los datos móviles (Chrome los usará para la voz) o conéctate a una wifi con Internet.";
    } else {
        cab.textContent = "El micrófono está bloqueado";
        txt.textContent = "Chrome tiene denegado el permiso del micrófono para esta dirección. Toca el candado o el icono de ajustes junto a la dirección, entra en Permisos y permite el Micrófono; después recarga la página.";
    }
    document.getElementById("modal-voz").classList.add("abierto");
}
function cerrarModalVoz() { document.getElementById("modal-voz").classList.remove("abierto"); }
function copiarTexto(id, boton) {
    const texto = document.getElementById(id).textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(texto) : Promise.reject()).then(() => {
        boton.textContent = "Copiado"; setTimeout(() => boton.textContent = "Copiar", 1500);
    }).catch(() => { window.prompt("Copia esta dirección:", texto); });
}
document.addEventListener("keydown", e => { if (e.key === "Escape") cerrarModalVoz(); });
