// static/js/darks.js
//
// Textos de la temperatura del sensor y del dark elegido por ella (darks.py), comunes a las
// páginas de darks, calibración, Apilado y Cámara.

// Tras el dark que se usaría ahora (ultimo de /darks/estado): la última lectura y el aviso si queda lejos
function textoSensorDark(u) {
    if (!u || u.temperatura_camara === null || u.temperatura_camara === undefined) return "";
    let s = " · " + _("última lectura del sensor: {t} °C a las {hora}", { t: fmtNum(u.temperatura_camara, 1), hora: u.temperatura_hora });
    if (u.lejos) s += " " + _("⚠️ Es el dark más cercano, pero está a {d} °C de diferencia: conviene hacer uno a esta temperatura.", { d: fmtNum(u.diferencia, 1) });
    return s;
}

// Tras una foto ya hecha: a qué temperatura estaba el sensor y la del dark que se le restó
function textoTemperaturaFoto(d) {
    if (!d || d.temperatura === null || d.temperatura === undefined) return "";
    let s = " · " + _("sensor a {t} °C", { t: fmtNum(d.temperatura, 1) });
    if (d.dark && d.dark_temperatura !== null && d.dark_temperatura !== undefined) s += " · " + _("dark de {t} °C", { t: fmtNum(d.dark_temperatura, 1) });
    if (d.dark_lejos) s += " ⚠️ " + _("el dark está a {d} °C de diferencia: conviene hacer uno a esta temperatura", { d: fmtNum(d.dark_diferencia, 1) });
    return s;
}

// La temperatura de un dark de la biblioteca, para las listas
function etiquetaTemperaturaDark(d) {
    return (d.temperatura === null || d.temperatura === undefined) ? "" : ` · ${fmtNum(d.temperatura, 1)} °C`;
}
