// static/js/ubicacion_movil.js — posición GPS del móvil para la ubicación del observatorio.
// La usan Configuración (Ubicación y hora) y el asistente de configuración inicial.
//
//   pedirUbicacionMovil({ alEstado(tipo, texto), alPosicion(coords) })
//     tipo "girando": esperando (texto puede llevar <b>); "error": no se pudo (texto plano)
//     coords: las de navigator.geolocation (latitude, longitude, altitude, accuracy)
//
// Chrome solo da el GPS en https (o en una dirección http declarada segura en chrome://flags). El límite
// de 25 s de getCurrentPosition NO cuenta mientras Chrome espera a que se permita la ubicación: si no
// enseña la pregunta, la petición se queda colgada para siempre (pasó el 27-sep-2026). Por eso hay un
// vigilante propio y cada paso se anota en el log del PC (/observatorio/diagnostico).
(function () {
    let vigilanteUbicacion = null;
    const ESPERA_AVISO_S = 10, ESPERA_MAXIMA_S = 90;
    // Instalada como app (manifest display: fullscreen) no hay barra de direcciones ni su icono ⓘ: el permiso
    // se cambia abriendo la misma dirección en una pestaña de Chrome (los permisos son de la dirección)
    const EN_APP = window.matchMedia("(display-mode: fullscreen), (display-mode: standalone)").matches;
    const PASOS_PERMISO = (EN_APP
        ? `Estás en la app instalada, que no tiene barra de direcciones: abre ${location.origin} en una pestaña de Chrome, `
          + "toca el icono de la izquierda de la dirección (ⓘ o candado) → Permisos → Ubicación → Permitir, y vuelve a la app. "
        : "Toca el icono de la izquierda de la dirección (ⓘ o candado) → Permisos → Ubicación → Permitir. ")
        + "Si ahí ya está permitida, mira en Android: Ajustes → Aplicaciones → Chrome → Permisos → Ubicación → Permitir.";
    function anotarUbicacion(evento) {
        fetch("/observatorio/diagnostico", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ evento, origen: location.origin, segura: window.isSecureContext, app: EN_APP }) }).catch(() => {});
    }
    async function permisoUbicacion() {
        try { return (await navigator.permissions.query({ name: "geolocation" })).state; }   // granted | prompt | denied
        catch (e) { return "desconocido"; }
    }
    async function pedirUbicacionMovil({ alEstado, alPosicion }) {
        const girando = t => alEstado("girando", t);
        const fallo = t => alEstado("error", t);
        if (!navigator.geolocation) { fallo("Este navegador no da la ubicación."); anotarUbicacion("navegador sin geolocalización"); return; }
        if (!window.isSecureContext) {
            // Por http Chrome ni pregunta: se explica cómo declarar la dirección como segura
            fallo("El navegador no da la ubicación en esta dirección (http).");
            if (typeof mostrarModalVoz === "function") mostrarModalVoz("ubicacion");
            anotarUbicacion("dirección http no declarada segura en Chrome: no se pide");
            return;
        }
        const permiso = await permisoUbicacion();
        anotarUbicacion("se pide la posición; permiso de Chrome: " + permiso);
        if (permiso === "denied") { fallo("Chrome tiene bloqueada la ubicación para esta dirección. " + PASOS_PERMISO); return; }
        girando("Pidiendo la posición al móvil (puede tardar unos segundos al aire libre)..."
            + (permiso === "prompt" ? " Si Chrome pregunta, elige <b>Permitir</b>." : ""));
        let terminado = false;
        const inicio = Date.now();
        const acabar = () => { terminado = true; clearInterval(vigilanteUbicacion); vigilanteUbicacion = null; };
        clearInterval(vigilanteUbicacion);
        vigilanteUbicacion = setInterval(async () => {
            if (terminado) return;
            const s = (Date.now() - inicio) / 1000, p = await permisoUbicacion();
            if (s >= ESPERA_MAXIMA_S) {
                acabar();
                fallo(`Chrome no ha dado la posición en ${ESPERA_MAXIMA_S} s (permiso: ${p}). ` + (p === "granted"
                    ? "El GPS no encuentra señal: sal al aire libre y comprueba que la ubicación de Android está activada."
                    : PASOS_PERMISO));
                anotarUbicacion(`sin respuesta en ${ESPERA_MAXIMA_S} s; permiso de Chrome: ${p}`);
            } else if (s >= ESPERA_AVISO_S && p === "prompt") {
                girando("Chrome está esperando a que permitas la ubicación, pero no ha enseñado la pregunta. " + PASOS_PERMISO);
            }
        }, 2000);
        navigator.geolocation.getCurrentPosition(pos => {
            if (terminado) return;
            acabar();
            const c = pos.coords;
            anotarUbicacion(`posición recibida (precisión ${Math.round(c.accuracy || 0)} m, en ${Math.round((Date.now() - inicio) / 1000)} s)`);
            alPosicion(c);
        }, err => {
            if (terminado) return;
            acabar();
            anotarUbicacion(`error ${err.code} (${err.message || "sin mensaje"})`);
            if (err.code === err.PERMISSION_DENIED) fallo("Chrome no ha dado permiso para la ubicación. " + PASOS_PERMISO);
            else if (err.code === err.TIMEOUT) fallo("El móvil no ha encontrado la posición a tiempo. Prueba al aire libre, con la ubicación de Android activada.");
            else fallo("No se pudo obtener la posición: " + err.message);
        }, { enableHighAccuracy: true, timeout: 25000, maximumAge: 60000 });
    }
    window.pedirUbicacionMovil = pedirUbicacionMovil;
})();
