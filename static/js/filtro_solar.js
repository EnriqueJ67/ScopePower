// ——— Aviso del filtro solar ———
// Cuando /goto responde {confirmar_filtro_solar: {...}} la montura NO se ha movido: el objeto
// es el Sol (o está a pocos grados de él con el Sol en el cielo) y sin filtro solar el sensor
// de la cámara se quema en segundos. Esta ventana ocupa toda la pantalla y solo el botón
// "SÍ" repite la petición con filtro_solar=si. Devuelve una promesa con true/false.
//
//   const r = await respuestaGoto;
//   if (r.confirmar_filtro_solar) {
//       if (await confirmarFiltroSolar(r.confirmar_filtro_solar)) reenviar con &filtro_solar=si
//   }
(function () {
    let modal = null, resolver = null;

    function crear() {
        modal = document.createElement("div");
        modal.id = "modal-sol";
        modal.className = "modal modal-sol";
        modal.innerHTML = `
            <div class="modal-caja modal-sol-caja" role="alertdialog" aria-modal="true" aria-labelledby="sol-titulo" aria-describedby="sol-texto">
                <div class="modal-sol-icono" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="4.5"/>
                        <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>
                    </svg>
                </div>
                <div class="modal-sol-titulo" id="sol-titulo"></div>
                <p class="modal-sol-texto" id="sol-texto"></p>
                <p class="modal-sol-detalle"></p>
                <div class="modal-sol-pregunta"></div>
                <div class="modal-sol-acciones">
                    <button type="button" class="btn modal-sol-no" id="sol-no"></button>
                    <button type="button" class="btn modal-sol-si" id="sol-si"></button>
                </div>
            </div>`;
        modal.querySelector("#sol-titulo").textContent = _("¡ATENCIÓN! ¿FILTRO SOLAR PUESTO?");
        modal.querySelector(".modal-sol-detalle").innerHTML = _("Sin filtro solar delante del telescopio, el Sol <b>quema el sensor de la cámara en segundos</b> (y los ojos si mirases por el ocular). Comprueba que el filtro está puesto y bien sujeto <b>antes</b> de continuar.");
        modal.querySelector(".modal-sol-pregunta").textContent = _("¿Has puesto ya el filtro solar?");
        modal.querySelector("#sol-no").textContent = _("NO · Cancelar");
        modal.querySelector("#sol-si").textContent = _("SÍ, el filtro está puesto · Ir al Sol");
        document.body.appendChild(modal);
        modal.querySelector("#sol-no").onclick = () => cerrar(false);
        modal.querySelector("#sol-si").onclick = () => cerrar(true);
        // Fuera de la caja o Escape = cancelar; nunca se confirma por accidente
        modal.addEventListener("click", e => { if (e.target === modal) cerrar(false); });
        document.addEventListener("keydown", e => {
            if (modal.classList.contains("abierto") && e.key === "Escape") cerrar(false);
        });
    }

    function cerrar(ok) {
        modal.classList.remove("abierto");
        const r = resolver; resolver = null;
        if (r) r(ok);
    }

    window.confirmarFiltroSolar = function (aviso) {
        if (!modal) crear();
        aviso = aviso || {};
        const objeto = aviso.objeto || _("el Sol");
        modal.querySelector("#sol-texto").textContent = aviso.texto ||
            (aviso.es_sol === false ? _("{objeto} está a solo {grados}° del Sol.", { objeto: objeto, grados: fmtNum(aviso.separacion_deg) })
                                    : _("Vas a apuntar el telescopio al Sol."));
        modal.querySelector("#sol-si").textContent = aviso.es_sol === false
            ? _("SÍ, el filtro está puesto · Ir a {objeto}", { objeto: objeto })
            : _("SÍ, el filtro está puesto · Ir al Sol");
        if (resolver) resolver(false);
        try { if (navigator.vibrate) navigator.vibrate([200, 100, 200, 100, 400]); } catch (e) {}
        return new Promise(resolve => {
            resolver = resolve;
            modal.classList.add("abierto");
            modal.querySelector("#sol-no").focus();   // Enter/espacio por defecto = cancelar
        });
    };
})();
