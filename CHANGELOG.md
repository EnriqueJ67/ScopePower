# Historial de cambios

Formato: una entrada por versión, con lo nuevo, lo cambiado y lo arreglado. Las fechas son del
día de la release. El número de versión vive en `version.py`.

## 1.2.3 · sin fecha (en preparación)

- Nuevo: Número de Wolf de las fotos del Sol. En la galería, el interruptor «Anotaciones» sobre una
  foto del Sol (suelta o apilada de un vídeo) localiza el disco, aplana el oscurecimiento del limbo
  sobre el crudo lineal y cuenta las umbras (f) y los grupos (g, por separación angular sobre la
  esfera, sin necesitar saber dónde está el norte) → R = k·(10·g + f). El conteo se corrige
  tocando la foto (quitar o añadir manchas, unir o separar grupos), se guarda en el JSON de la
  foto y en la serie `datos/numero_wolf.csv`, que se ve en una gráfica con el filtro Sol. Con la
  orientación de la cámara conocida se dan las coordenadas heliográficas de cada grupo; P, B0 y
  L0 salen de las fórmulas de Meeus sin Internet. El factor k del observador va en `[Sol]
  factor_k` (`config.example.ini`). Módulo `manchas_solares.py`, capa
  `static/js/manchas_solares.js`, prueba `herramientas/probar_manchas_solares.py`. Probado con
  Soles sintéticos y simuladores; pendiente afinar los umbrales con fotos reales.
- Arreglado: la descripción hablada del objeto al llegar con GoTo casi nunca sonaba en el campo. La
  pantalla mandaba el nombre con el tipo entre paréntesis («M 31 (Galaxia)»), el catálogo propio de
  172 descripciones sin Internet no lo reconocía y la app acababa en Wikipedia, que sin red no
  responde, y además leía en voz alta el aviso de «no se encontró información». Ahora el nombre va
  limpio, solo se sale a Wikipedia si hay red, sin descripción se calla, y la lectura automática se
  apaga en Configuración > Avisos por voz (`[Voz] describir_objeto`); el botón «Hablar info» sigue
  disponible a mano. También se evita que la llegada del GoTo se tratase dos veces (dos lecturas).
- Enfoque de día (Sol y Luna) en vivo más estable: medido con los vídeos reales de la Luna, el
  limbo de cada fotograma varía un 6-7 % por el seeing, así que el número es ahora el percentil 25
  de los últimos 2,5 s (los fotogramas con mejor seeing enseñan el desenfoque real) con histéresis,
  en vez de la mediana de 5 fotogramas que saltaba un 8 % entre lecturas. Debajo del recorte va la
  gráfica del último minuto con el mejor de la sesión, el recorte lleva su nombre («limbo» o
  «terminador»; el número es siempre el limbo) y el PC dice por voz «mejora», «empeora» o «igual»
  al girar el enfocador. Arreglado: con la Luna cortada por el borde de la imagen el círculo se
  ajustaba al terminador y el limbo salía falso y más fino, estropeando el «mejor» de la sesión;
  ahora se exigen 30 ángulos de limbo, el radio de la sesión y el disco quieto (con el pad o la
  mano en el tubo la exposición emborrona el limbo). Prueba `herramientas/probar_enfoque_dia.py`.
- El guiado por el disco del Sol o la Luna corrige moviendo los ejes a velocidad baja
  (`correccion_ejes.py`, MoveAxis en bucle cerrado con los codificadores) en vez de con un GoTo:
  con la Luna real del 21-oct, cada GoTo de 2′ hacía que la AZ GoTo se pasara unos 25′, parase y
  volviera (13 s de bandazo que sacaba el disco del encuadre del vivo). El sentido de cada eje se
  aprende solo y, si la montura no responde a MoveAxis, se vuelve al GoTo. Mientras se graba un
  vídeo la corrección espera (hasta 3 veces el umbral). Nueva clave `[FotoDia]
  guiado_velocidad_deg_s` (0 = GoTo como antes) y prueba `herramientas/probar_guiado_disco.py`.
- Humor: frases para los niveles bajos (5-25 %), que hasta ahora no tenían ninguna, en español y en
  inglés; «Probar» ya no menciona ningún fichero cuando no hay frase.
- Revelado de día nuevo (`revelado_dia.py`) para el apilado de los vídeos y las fotos del Sol y la
  Luna: el blanco va a lo más brillante de verdad con el balance ya aplicado (antes el 1,5 % más
  brillante del disco de la Luna salía blanco puro, sin detalle), curva con rodilla, realce del
  relieve por escalas solo dentro del disco, deconvolución por el limbo y saturación ajustable: con
  3–5 aparecen los colores de la Luna (mares azules, tierras altas ocres). La foto de día y el modo
  en vivo también ponen el blanco en el 99,99 % en vez del 99,9 %.
- Editor de día en la galería (`editor_dia.py`, «Editar» en las fotos con su crudo): cinco
  controles (exposición, contraste, detalle, nitidez, color) con vista previa automática en un
  segundo, firma, restaurar el original y, en las que vienen de un vídeo que sigue en el mini PC,
  volver a apilar con más o menos fotogramas y la rotación de campo elegida. El panel Vídeo de
  Cámara se queda solo con grabar y apilar: los ajustes se tocan en la galería.
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
  su nombre, los contornos de los mares, el limbo y el terminador. La libración y la iluminación salen de la fecha y el lugar de
  la foto (modelo de rotación de la UAI, comprobado con JPL Horizons) y el giro y el espejo de la
  propia foto, comparándola con un mapa de albedo; no hace falta Internet ni plate solving. Con la
  foto pequeña solo se nombra lo grande y ningún nombre pisa otro. Nombres del nomenclátor de la UAI,
  mapa y límites de los mares del LROC (dominio público) en `luna/`; prueba
  `herramientas/probar_luna_anotada.py`.
- Luna anotada también en Cámara: en el modo día, con la Luna elegida, el interruptor «Nombres de
  la Luna encima de la foto» pinta los mismos nombres y contornos sobre la foto que se ve. Con fotos
  sueltas, el continuo o el timelapse se anota cada foto nueva; en vivo, un fotograma cada 8 s (la
  Luna apenas se mueve y así no se carga el mini PC). El dibujo es común a la galería y a Cámara
  (`static/js/luna_anotada.js`).
- Luna anotada con un trozo de la Luna: con el disco cortado por el borde de la foto basta un arco
  del limbo iluminado de unos 40°; sin limbo a la vista (con una Barlow o un recorte del interior)
  se busca el trozo en la Luna que se debería ver, probando escala, giro y espejo. Si la foto encaja
  igual de bien en más de un sitio (poco limbo, o una zona lisa de un mar o de las tierras altas),
  no se anota y se dice por qué, en vez de poner los nombres en otro sitio. En vivo, el trozo se
  sigue desde el fotograma anterior (0,3 s en vez de la búsqueda completa, de 2 a 8 s). El mapa de
  albedo pasa a 2048×1024 (unos 5 km por píxel).
- Luna anotada: cuando no se puede anotar, el motivo y el tiempo quedan en el log (en Cámara, el
  mismo motivo una vez por minuto, para no llenarlo en vivo). Una foto sin Luna (negra, con la tapa
  puesta) se rechaza al momento, sin buscarla como trozo del interior.
- Asistente de puesta en estación para monturas ecuatoriales (botón en Alineación cuando la montura
  se declara ecuatorial): tres fotos resueltas con ASTAP girando solo el eje de AR miden el error del
  eje polar en altura y acimut, y después un bucle de fotos dice en grande, con flechas y por voz,
  cuánto girar cada tornillo hasta bajar de la tolerancia (2′ de serie). Si el tubo está en la
  posición de inicio apuntando al polo, la app lo aparta sola; con montura alemana gira hacia el lado
  que no cruce el meridiano; la referencia del ajuste descuenta la deriva propia de una montura mal
  puesta en estación. Sección `[PuestaEnEstacion]` nueva en `config.example.ini` y prueba
  `herramientas/probar_puesta_estacion.py` (montura falsa con error conocido, sin ASTAP).
- La cabecera dice junto al icono de la montura si está en modo altacimutal («AltAz») o ecuatorial
  («Eq»), también en el móvil; el chip del Inicio lo añade tras el sistema de coordenadas. Es lo que
  declara el driver por ASCOM (AlignmentMode) al conectar; si no lo dice, no aparece nada.

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
