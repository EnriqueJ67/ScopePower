// static/js/i18n.js — traducción de los textos de las páginas (ver idioma.py).
//
// base.html pone antes window.IDIOMA (código, lang, depurar) y, si el idioma no es el español,
// carga static/i18n/<código>.js con window.I18N_CATALOGO (lo genera herramientas/traducciones.py
// a partir del .po). Se carga en <head>, así que _() ya existe en cualquier <script> de la página.
//
//   _("Guardando...")                              texto traducido
//   _("Quedan {n} fotos", { n: 3 })                con marcadores {nombre}
//   ngettext("{n} foto", "{n} fotos", total)       singular o plural ({n} se rellena solo)
//   _(tipo)                                        dato que viene del servidor y está en el catálogo
//   const TIPOS = { gx: N_("Galaxia") }            tabla cuyo valor sigue en español (clave, comparaciones);
//                                                  se traduce al pintarlo: _(TIPOS[c])
//   fmtNum(3.25, 2)  fmtHora(fecha)  fmtFecha(fecha)  IDIOMA.lang ("es-ES" | "en-US")
//
// Los textos van entre comillas normales dentro de _() (nada de `plantillas` ni concatenaciones):
// así los encuentra la extracción del catálogo.
(function () {
    "use strict";
    var I = window.IDIOMA || { codigo: "es", lang: "es-ES", locale: "es-ES", depurar: false };
    window.IDIOMA = I;
    var CAT = window.I18N_CATALOGO || {};
    var marcar = I.depurar && I.codigo !== "es";

    function formatear(texto, valores) {
        if (!valores) return texto;
        return texto.replace(/\{(\w+)\}/g, function (m, clave) {
            return Object.prototype.hasOwnProperty.call(valores, clave) && valores[clave] != null
                ? String(valores[clave]) : m;
        });
    }

    window._ = function (id, valores) {
        if (id == null) return "";
        id = String(id);
        var t = CAT[id];
        if (Array.isArray(t)) t = t[0];
        if (typeof t !== "string" || !t) t = marcar ? "¿¿" + id + "??" : id;
        return formatear(t, valores);
    };

    window.N_ = function (id) { return id; };     // solo marca el texto para el catálogo

    window.ngettext = function (singular, plural, n, valores) {
        var v = Object.assign({ n: n }, valores || {});
        var t = CAT[singular];
        var uno = Number(n) === 1;            // inglés y español: singular solo para 1
        var texto;
        if (Array.isArray(t) && t.length >= 2) texto = uno ? t[0] : t[1];
        else texto = marcar ? "¿¿" + (uno ? singular : plural) + "??" : (uno ? singular : plural);
        return formatear(texto, v);
    };

    window.fmtNum = function (x, dec) {
        var n = Number(x);
        if (!isFinite(n)) return String(x);
        var o = dec == null ? { maximumFractionDigits: 6 } : { minimumFractionDigits: dec, maximumFractionDigits: dec };
        o.useGrouping = false;
        return n.toLocaleString(I.locale, o);
    };

    function dos(n) { return (n < 10 ? "0" : "") + n; }

    window.fmtHora = function (d, conSegundos) {
        d = d instanceof Date ? d : new Date(d);
        var t = dos(d.getHours()) + ":" + dos(d.getMinutes());
        return conSegundos === false ? t : t + ":" + dos(d.getSeconds());
    };

    // 15/10/2026 21:30 en español, 2026-10-15 21:30 en inglés (ISO, sin dudas entre día y mes)
    window.fmtFecha = function (d, conHora) {
        d = d instanceof Date ? d : new Date(d);
        var f = I.codigo === "es"
            ? dos(d.getDate()) + "/" + dos(d.getMonth() + 1) + "/" + d.getFullYear()
            : d.getFullYear() + "-" + dos(d.getMonth() + 1) + "-" + dos(d.getDate());
        return conHora === false ? f : f + " " + fmtHora(d, false);
    };
})();
