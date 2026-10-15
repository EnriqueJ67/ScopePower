# Historial de cambios

Formato: una entrada por versión, con lo nuevo, lo cambiado y lo arreglado. Las fechas son del
día de la release. El número de versión vive en `version.py`.

## 1.1.0 · sin fecha (en preparación)

- **ScopePower en inglés.** La app habla español o inglés (pantallas, voz del PC, humor, descripciones
  de objetos y constelaciones). Se elige en el primer paso del asistente de configuración, en la
  página de bienvenida o en Configuración > Idioma, y vale para todos los dispositivos conectados.
  Traducciones en `traducciones/` (gettext); reglas para añadir textos en CONTRIBUTING.md y
  `README_en.md` para quien llega desde fuera. *ScopePower now speaks English: choose it in the
  setup wizard or in Settings > Language.*
- La voz del PC elige sola una voz del idioma de la app y Configuración avisa si no hay ninguna instalada.
- Los resúmenes de Wikipedia vuelven a descargarse: la Wikipedia rechazaba la identificación por
  defecto de la librería `wikipedia`.
- Humor de la voz escondido de serie: se habilita con la casilla «Comentarios de a bordo» de Avisos por voz
  en Configuración (arranca al 50 %); hasta entonces el botón «Humor» no aparece y no se dice ninguna frase.
- Actualizar desde GitHub: `actualizar_desde_github.bat` en el mini PC (última release o `/master`, 
  `/volver` a la versión anterior) y botón «Actualizar desde GitHub» en Configuración, que comprueba la 
  última release, enseña lo que entra y reinicia la app. `copiar_a_campo.bat` sigue valiendo sin Internet.

## 1.0.0 · 2026-10-15

Primera versión pública. Resume lo que hay tras un año de uso en el campo:

- GoTo por nombre o por voz con catálogo local y centrado automático por plate solving (ASTAP).
- Apilado en tiempo real con rechazo de fotos malas, darks y flats de biblioteca, detección de
  rastros de aviones y satélites, estirado multiescala y reducción de ruido por escalas.
- Galería con editor: revelado, encuadre, deconvolución, calibración de color con las estrellas,
  anotaciones NGC/IC y composición de cometas.
- Planetario sin Internet (Celestia Atlas), «Esta noche» y plan de la noche automático.
- Modos mosaico, cometa, seguimiento de objetos en movimiento, foto y guiado de día (Sol y Luna),
  timelapse.
- Cámaras por ASCOM o por SDK nativo (Player One, ZWO, QHY, familia ToupTek, webcams).
- Montura SynScan por ASCOM o por UDP directo, pad de velocidades, alineación, aparcado y
  vigilante del seguimiento sidéreo.
- Avisos por voz con nivel de humor, anuncio de la IP, aviso de espacio en disco, punto de acceso
  wifi, asistente de configuración inicial e instalador para un PC sin Python.
- Eliminado el sistema de activación de las versiones anteriores: la app es libre (GPL-3.0).
