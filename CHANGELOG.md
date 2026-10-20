# Historial de cambios

Formato: una entrada por versión, con lo nuevo, lo cambiado y lo arreglado. Las fechas son del
día de la release. El número de versión vive en `version.py`.

## 1.2.3 · sin fecha (en preparación)

- Humor: frases para los niveles bajos (5-25 %), que hasta ahora no tenían ninguna, en español y en
  inglés; «Probar» ya no menciona ningún fichero cuando no hay frase.
- Arreglado el guiado por el disco del Sol o la Luna con la cámara girada en Cámara (giro de 180°):
  el desplazamiento del disco salía con el signo cambiado y cada corrección alejaba el disco del
  centro hasta sacarlo del campo. Afectaba también a «Sincronizar con el disco».
- «Sincronizar con el disco» borra antes el modelo de apuntado anterior de SynScan Pro
  (ClearSyncTo), para que la app no pregunte en su ventana qué hacer con la muestra nueva, y
  comprueba después que la montura se ha quedado con la posición enviada; si no, lo dice.
- Vídeo del Sol y la Luna y apilado de los mejores fotogramas («lucky imaging»): con el modo en
  vivo en marcha, «Grabar vídeo» guarda un SER en crudo (RAW16, binning 1, el recorte que se ve)
  en el disco del mini PC; el panel Vídeo de Cámara mide la nitidez de cada fotograma, se queda
  con los mejores (el 25 % de serie), los alinea al subpíxel, los apila, los afina y deja el
  resultado en la galería como una foto de día más (JPG firmado y FITS en color). Sección
  `[Video]` nueva en `config.example.ini` y prueba `herramientas/probar_apilado_video.py`.
- El afinado del apilado del vídeo es una deconvolución del disco (Richardson-Lucy) con el
  desenfoque medido en su propio limbo, 4 iteraciones por punto del deslizador; sin disco
  (paisaje) queda la máscara de desenfoque. El apilado lineal se guarda junto al SER y
  «Reafinar» lo revela otra vez con otro afinado en segundos, sin repetir el apilado. La
  «Nitidez» del editor de la galería no sirve para estas fotos: mide estrellas.
- El apilado del vídeo corrige la rotación de campo de la montura altacimutal: cada fotograma se
  gira alrededor del centro del disco según la hora de ese fotograma, la ubicación y la
  orientación de la cámara del último plate solving (hasta 1,5° en 2 minutos con el Sol a 75°
  de altura). La ubicación y la orientación se guardan con el vídeo al grabarlo.
- La rotación de campo del vídeo solo se corrige con montura altacimutal: al grabar se pregunta
  a la montura por ASCOM (AlignmentMode) y con una ecuatorial no se gira nada. La opción
  «Rotación de campo» del panel Vídeo (`[Video] rotacion_campo`: automatico, siempre, nunca)
  permite elegirlo a mano cuando la montura no lo dice bien, por ejemplo una altacimutal sobre
  cuña.
- Arreglado el alineado del apilado del vídeo, también sin giro: `cv2.phaseCorrelate` con
  ventana multiplicaba por ella la imagen de referencia en cada fotograma y la iba borrando, y
  el desplazamiento medido en el verde a media resolución fallaba hasta 0,8 px. Ahora se mide
  en el verde a resolución completa (0,1–0,2 px).
- Luna anotada: en las fotos de la Luna de la galería (las sueltas de Cámara y los apilados de
  vídeo), el interruptor «Anotaciones» pinta los mares, cráteres, montes, rimas y alunizajes con
  su nombre, el limbo y el terminador. La libración y la iluminación salen de la fecha y el lugar de
  la foto (modelo de rotación de la UAI, comprobado con JPL Horizons) y el giro y el espejo de la
  propia foto, comparándola con un mapa de albedo; no hace falta Internet ni plate solving. Con la
  foto pequeña solo se nombra lo grande. Nombres del nomenclátor de la UAI y mapa del LROC (dominio
  público) en `luna/`; prueba `herramientas/probar_luna_anotada.py`.

## 1.2.2 · 2026-10-16

- Una sola versión para instalar: «Actualizar desde GitHub» (en Configuración y en
  `actualizar_desde_github.bat`) solo ofrece la última release. Desaparecen el botón «Actualizar a
  master» y la opción `/master`, que instalaban lo último en desarrollo sin publicar. Se mantiene
  «Volver a la anterior» para deshacer una actualización.

## 1.2.1 · 2026-10-16

- Librerías al día por seguridad: Flask 3.1.3, Werkzeug 3.1.9, Jinja2 3.1.6, Requests 2.33.0 y
  Pillow 12.3.0. Cierran los 31 avisos que GitHub encontró al publicar el proyecto; 12 son graves:
  11 de Pillow (al abrir imágenes, PDF o fuentes manipuladas) y 1 de Werkzeug. En el mini PC,
  `actualizar_desde_github.bat` reinstala las librerías solo.
- Nueva prueba `herramientas/probar_servidor.py` en la comprobación de cada cambio: arranca el
  servidor web sin equipo y pide las pantallas principales en español e inglés. Detecta versiones de
  librerías que no encajan entre sí, que antes pasaban la comprobación en verde.
- El actualizador acepta etiquetas de versión movidas en GitHub (`git fetch --force --tags`). Un mini
  PC que ya fuera clon antes del 16-oct-2026 necesita una vez `git fetch --force --tags origin`
  (ver INSTALACION.md).

## 1.2.0 · 2026-10-16

- Editor de la galería: el color (brillo, temperatura, SCNR, saturación), la firma y unas **curvas por
  puntos de control** nuevas (RGB y por canal, como las del Seestar) se aplican al instante en el
  propio navegador, sobre una base que el mini PC calcula una vez; así se puede retocar una foto
  mientras la app apila, hace un mosaico o un cometa, sin esperar a cada vista previa. Al guardar,
  el servidor aplica los mismos ajustes en coma flotante a resolución completa y las curvas quedan
  guardadas con la foto. El mismo panel de curvas está en «Procesado de la vista previa» del apilado
  (con el histograma de la vista previa de fondo): se aplican al apilado en vivo y al guardar en la
  galería, van en los presets y en `[Procesado] curvas` de config.ini.
- Mosaico terminado: el panel enseña la miniatura del mosaico con enlaces para verlo en el visor, en la
  galería y en el editor (también tras reiniciar la app), y el botón grande de guardar guarda el mosaico
  cuando es la vista elegida en vez de avisar de que no hay apilado.
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
- Configuración: la espera de estabilización tras mover la montura pasa a «Equipo y captura» y vale para
  cualquier montura ASCOM; desaparece la tarjeta «Montura SynScan» (la Alineación Automática sigue
  borrando las sincronizaciones al empezar). El desplegable de la cámara se llama «Driver de la cámara»,
  sin «(ASCOM)», porque también lista las cámaras por SDK nativo.
- Fuera la subida por FTP de la vista previa del apilado (y la sección `[FTP]` de config.ini): la
  publicación de fotos volverá más adelante con una API.
- El recorrido guiado por constelaciones sale del menú de inicio (la página sigue en `/constelacion`).

## 1.1.0 · 2026-10-15

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
