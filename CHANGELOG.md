# Historial de cambios

Formato: una entrada por versión, con lo nuevo, lo cambiado y lo arreglado. Las fechas son del
día de la release. El número de versión vive en `version.py`.

## 1.2.3 · sin fecha (en preparación)

- Enfoque con máscara de Bahtinov en Cámara (interruptor «Enfoque: solo verde»). Con la máscara
  puesta, el recorte ampliado ya no enseña el verde solo, donde los picos de la máscara salían a
  trozos (la rejilla separa los colores y cada canal tiene su trozo). Ahora junta los tres colores
  y dibuja una cruceta virtual: las dos rayas de la X y, a trazos, por dónde tiene que pasar el pico
  central. Debajo, cuánto se aparta en píxeles, con una barra para ver hacia qué lado. Al centrarlo
  los trazos se ponen verdes y el PC dice «Bahtinov centrada». Al quitar la máscara sin tocar el
  enfoque, ScopePower aprende cuánto más anchos que el verde salen el rojo y el azul en el foco
  bueno, y las veces siguientes, sin máscara, dice «Enfocado» cuando vuelven a esa relación.
  Probado con una estrella simulada; falta probarlo con la cámara y la máscara reales.
  Al tocar el enfocador el tubo vibra y algún fotograma sale movido y sin cruz: eso ya no se toma
  por la máscara quitada (en vivo hacen falta 3 s seguidos sin la cruz) ni sirve para aprender
  (una estrella alargada no cuenta, y el texto dice «estrella movida»). Antes, en foto suelta, una
  sola foto movida tras centrar podía guardar un foco equivocado.
- Arreglado: el apilado descartaba fotos de campos densos de la Vía Láctea que ASTAP no resolvía
  a la primera (una de M 11 el 29-oct), con un aviso rojo ilegible («Command … returned non-zero
  exit status 1») que se quedaba en la página toda la sesión. Ahora el alineado repite con más
  tolerancia, como ya hacía la galería, y acepta la solución solo si cuadra con la escala y la
  posición de la montura. Si aun así no resuelve, dice «ASTAP no resolvió el fotograma», y el
  aviso se borra en cuanto entra la foto siguiente.
- Arreglado: en los dithers pequeños la montura invertía el sentido de un eje y lo volvía a
  invertir en el paso siguiente, y a veces acababa haciendo un GoTo. El cielo se mueve mientras
  la montura mueve los ejes sin seguimiento, y el primer paso a veces no arranca. Ahora se descuenta
  lo que se ha ido el cielo, un paso al revés no basta para cambiar el sentido de un eje y hacen
  falta dos pasos sin moverse para volver al GoTo. Vale también para el guiado por el disco del Sol
  y la Luna. El log apunta cada paso, para ver con la montura real qué hace cada eje.
- Arreglado: el centrado tras el GoTo se rendía («la montura no mejora») cuando la app de la
  montura no aplicaba del todo el Sync de la primera vuelta, como pasó con SynScan Pro justo
  después de una alineación de varios puntos. Ahora comprueba que el Sync se ha tomado y, si la
  vuelta con Sync no mejora, prueba un movimiento corto antes de darse por vencido.
- Nota de cada apilado en la galería, para quedarse con las mejores fotos de cada objeto. Es la
  misma puntuación con que el apilado en vivo decide qué fotos entran (nitidez de las estrellas ×
  raíz del número de estrellas), medida sobre el FITS apilado. Sale en la tarjeta con el puesto
  entre las fotos del mismo objeto («mejor de 3», «3.ª de 3»); el orden «Objeto» pone la mejor
  primero dentro de cada objeto y el nuevo orden «Nota» va de la mejor a la peor. Los apilados
  guardados antes se puntúan con el botón «Puntuar N fotos».
- Planetario: las chinchetas de «Esta noche» ya no se pintan encima del paisaje; se esconden
  cuando el objeto queda detrás de los árboles o las colinas, como hace el motor con lo suyo. Y
  el Sol, la Luna y los planetas salen con su nombre en español (antes Sun, Mercury, Venus…),
  también en el panel y en el buscador, con su tipo (Planeta, Satélite, Planeta enano).
- Planetario con paisaje y brújula. El horizonte se dibuja con el paisaje Guéreins de Stellarium
  (el mismo que trae Touch-N-Stars), que tapa lo que queda por debajo y se tiñe de rojo en el modo
  noche; el interruptor «Paisaje» lo quita y el navegador lo recuerda. Abajo a la izquierda, una
  brújula con la aguja hacia donde mira el centro del mapa, su rumbo («NE 38,1°») y su altura.
  Todo va con la página, sin Internet.
- Vídeos en la galería con un visor fotograma a fotograma. Cada vídeo del Sol o de la Luna sale
  como una tarjeta más (miniatura, duración, tamaño y filtro «Vídeos») y abre un visor con barra,
  ±1/±10 fotogramas, teclado y reproducción a tiempo real, a cámara lenta o de uno en uno. Los
  fotogramas llegan como JPG pequeños, así que se recorre también con el wifi de campo. La primera
  vez se analiza el vídeo en el mini PC: nitidez de cada fotograma y los momentos en que algo cruza
  por delante (pájaros, aviones, nubes), con un círculo donde está. Con el vídeo del Sol del 25-oct
  sale el pájaro (750 veces el ruido) y otro lejano (60) sin falsas alarmas. Cualquier fotograma se
  guarda en la galería como una foto de día más, firmada y editable; desde el visor también se
  apila o se borra el vídeo, y los apilados llevan «Ver el vídeo».
- Enfoque de día: ayuda de la curva en V. Con el Sol del 25-oct se vio que cerca del foco el
  número se queda plano aunque se siga girando: el limbo es √(suelo² + desenfoque²) con un suelo
  de seeing y óptica de ~4,1 px, y entre 0,9 y 1,8 px de desenfoque apenas cambia 0,3 px. Ahora,
  al cruzar esa zona plana y subir claramente, el PC dice «te has pasado del foco, vuelve atrás»,
  la gráfica sombrea la zona y marca su centro (el foco bueno) y al volver dice «estás en la zona
  buena». La gráfica enseña los últimos 30 s con la escala centrada en el mínimo, y el número va
  con dos decimales y sin histéresis cerca del mejor. Probado con barridos simulados (sin falsas
  alarmas quieto en el foco, centro a ±0,2 s) y sobre el vídeo real del Sol del 25-oct.
- Cambiado: con «Separar las estrellas al guardar» (`[Procesado] estrellas_aparte`), el guardado
  del apilado termina con el JPG de siempre y la separación con StarNet2 y el reprocesado van
  después, en una cola propia en segundo plano (`cola_estrellas.py`): de una foto en una, por
  orden, con StarNet2 y el hilo de la cola a prioridad baja para que la captura y el apilado vayan
  primero. Antes el guardado no terminaba hasta el final: el plan de la noche y el mosaico se
  quedaban parados hasta 3 minutos por objeto y no se podía guardar otra foto mientras tanto. Si la
  foto se edita, se renombra o se borra antes de que llegue su turno, no se toca (las estrellas
  separadas quedan para el editor). Nunca hay dos StarNet2 a la vez: si el editor y la cola
  coinciden, uno espera al otro, y con el mismo apilado el segundo usa las capas del primero. Si se
  cierra ScopePower con fotos en la cola, se quedan con el JPG normal.
- Nuevo: tarjeta «Estrellas aparte» en Configuración. Comprueba si StarNet2 está en el PC y si
  arranca de verdad (lo lanza con `--version`, sin diálogos de Windows), y lo dice con su versión
  y su ruta. Con StarNet2 funcionando se puede activar «Separar las estrellas al guardar el apilado
  en la galería» (`[Procesado] estrellas_aparte`, hasta ahora solo en `config.ini`). Si falta o no
  arranca, explica cómo instalarlo y el Visual C++ que necesita, con botones para abrir las dos
  descargas en el navegador del PC donde corre ScopePower, y deja escribir su carpeta
  (`[General] ruta_starnet`). ScopePower no descarga ni instala StarNet2: su licencia no permite
  redistribuirlo y el instalador pide administrador.
- Nuevo: «Anterior» en el editor de la galería, junto a «Original» y «Reprocesada»: la vista
  previa tal como estaba antes del último cambio, para comparar cada cambio con el anterior y no
  solo con la foto del apilado. Arrastrar un deslizador cuenta como un solo cambio (se da por
  terminado a los 0,8 s sin moverlo), y una vista previa del mini PC que llega después, como otro.
- Cambiado: al guardar en la galería, el editor ya no recarga la página. Se sigue editando con la
  misma vista previa y el mismo «Anterior», y el botón queda listo para volver a guardar. Si el
  mini PC deja de contestar mientras guarda (por ejemplo, al reiniciarlo), el botón se libera
  pasados 30 s en vez de quedarse esperando.
- Cambiado: el deslizador «Tamaño de las estrellas» del editor (estrellas aparte) reduce las
  estrellas en vez de apagarlas. Antes multiplicaba la capa por un factor, es decir, una
  transparencia: las mismas estrellas, del mismo tamaño, más tenues. Ahora se hace un catálogo
  de la capa (posición, anchura y color de cada estrella, `procesado.catalogo_estrellas`) y
  cada estrella se vuelve a pintar como una campana de Gauss con su pico y su color y la
  anchura multiplicada por el tamaño, la misma proporción para todas: las brillantes siguen
  siendo mayores que las débiles, y ninguna baja de medio píxel ni desaparece por quedarse
  entre píxeles (suelo suave). Por debajo del 25 % pierden luz todas por igual, hasta
  desaparecer al 0 %; entre el 100 y el 85 % la capa original y la pintada se funden. El
  servidor manda el catálogo con la vista previa (8 bytes por estrella) y el navegador pinta
  igual que el servidor (a ±1 nivel). Antes se probó a encoger el perfil de la propia capa:
  las estrellas brillantes, de cima plana, quedaban como discos con un anillo oscuro dentro
  (M 27 en el mini PC). Arreglado de paso: el guardado con estrellas aparte fallaba en las
  fotos sin la zona útil en la cabecera (apilados anteriores a octubre), porque cada capa se
  recortaba por su cuenta.
- Nuevo: estirado y curvas propios de las estrellas en el panel «Estrellas aparte». Hasta ahora
  las estrellas se estiraban con la misma curva que el objeto. «Intensidad de las estrellas»
  multiplica su luz antes de la curva (×1/4 a ×4: más o menos estrellas y más o menos brillantes
  sin tocar la nebulosa ni el fondo) y «Compresión de los núcleos» sube el blanco de su curva
  hacia el máximo, así los núcleos de las brillantes dejan de cortarse en su tope y conservan el
  perfil y el color hasta el centro (en M 27, la cima plana pasa de 283 a 6 píxeles con 0,7).
  «Curvas de las estrellas» es un segundo editor de curvas (RGB, R, G y B) que actúa solo sobre
  la capa de estrellas antes de juntarla con el objeto, al instante en el navegador; las curvas
  del panel Curvas siguen actuando sobre la foto entera. La saturación propia de las estrellas
  ya existía (panel Procesado). Se guardan con la foto y en los presets.
- Arreglado: si a StarNet2 le falta el Visual C++ de Microsoft (MSVCP140.dll, que su instalador no
  trae; pasó en el mini PC de campo), Windows abría un diálogo de error y la separación de estrellas
  se quedaba parada hasta que alguien pulsara Aceptar, algo imposible con la tablet como única
  pantalla. Ahora StarNet2 se lanza sin diálogos de Windows y el panel «Estrellas aparte» dice qué
  falta y dónde descargarlo (`vc_redist.x64.exe`); también avisa si el procesador no tiene las
  instrucciones que usa.
- Arreglado: borbotones oscuros en el fondo con las estrellas aparte. El contraste local (CLAHE)
  realzaba en el cielo sin estrellas el ruido de escala media y lo dejaba en manchas oscuras de
  10 a 30 píxeles (M 27 con contraste 0,7). Con las estrellas aparte ahora solo actúa donde hay
  señal, a partir de 3 sigmas del cielo: el moteado del fondo baja de 0,0102 a 0,0067 y la
  nebulosa recibe el mismo contraste que antes.
- Arreglado: al reducir las estrellas aparecían estrellas inventadas alrededor de las brillantes
  (M 17: un anillo de ocho bolas naranjas). Eran estrellas débiles reales sobre el halo de la
  brillante, pero el catálogo medía su pico, su anchura y su color con la luz del halo debajo, y
  las pintaba grandes y del color de la brillante. Ahora el catálogo va en dos pasadas: mide las
  estrellas grandes, modela su halo con la mediana por anillos, lo resta y mide las demás en lo
  que queda. Las débiles salen con su anchura (1,5 px frente a 4-6 px) y su color, y el catálogo
  tarda 1,5 s en una foto de 1 470 px.
- Cambiado: al reducir, las estrellas brillantes conservan la cima. El estirado corta su núcleo
  en el tope y por eso tienen el centro claro y el color en el halo; repintadas como una campana
  de un solo color salían como bolas pintadas, apagadas y más anchas. Ahora el catálogo mide de
  cada una la cima cortada: su sobreexposición, la anchura de verdad de la campana y el color de
  los píxeles sin cortar. Se pinta igual que sale del estirado: el color por la sobreexposición,
  recortado a 1 canal a canal, y la cima encoge con la estrella. Las débiles no cambian. El
  catálogo pasa a 10 bytes por estrella en la vista previa (60 KB en M 17), y el navegador pinta
  igual que el servidor (1 nivel como mucho).
- Arreglado: una estrella brillante con huecos en la cima cortada (0,93 entre zonas de 0,99) se
  partía en dos en el catálogo y al reducir salía al lado una estrella inventada, oliva y grande,
  rodeada de otras naranjas (M 17 en la vista previa del mini PC). Ahora todo lo que pasa de 0,9 y
  está conectado es la cima de una sola estrella.
- Arreglado: con las estrellas aparte salía una sombra oscura alrededor del objeto, dentro de un
  resplandor gris, más fuerte cuanto más reducción de ruido (M 27). El suavizado del fondo sin
  estrellas rellena los huecos del cielo comparando con su media a gran escala, y esa media se
  llevaba también la luz y el color de la nebulosa: el cielo de alrededor se levantaba (+6 % a
  120 px con ruido 0,65) y se teñía, y entre ese resplandor y el borde de la nebulosa quedaba la
  sombra. Ahora la media se mide solo con el cielo (lo que no pasa de 3 sigmas del fondo).
- Nuevo: «Halo de las estrellas» (⚡) en el panel «Estrellas aparte»: la forma de las estrellas
  reducidas. Con 0 se pintan como una campana de Gauss, como hasta ahora, y en fotos como M 27, cuyas
  estrellas ya son casi gaussianas y pocas llegan a cortarse en su tope, quedaban como bolitas. Con
  más valor se pintan con un perfil de Moffat de la misma anchura a media altura: núcleo más picudo
  y un halo que se apaga despacio, como las estrellas de un telescopio. Por defecto 0,75 (beta 2:
  al 5 % del pico llegan a 2,9 medias alturas, frente a 2,1 la gaussiana). Al instante en el
  navegador, que pinta igual que el servidor (1 nivel como mucho), y se guarda con la foto y en
  los presets; las fotos y presets anteriores toman el 0,75.
- Cambiado: «Un solo color por estrella» en la capa de estrellas aparte reparte el color en
  1,5 px en vez de 4. Con 4 px, en campos densos como M 17 se mezclaba el color de estrellas
  vecinas y la variedad de colores bajaba un 17 %; ahora, un 6 %.
- Nuevo: estrellas aparte en el editor de la galería (prototipo). Con StarNet2 instalado (el
  programa de Mikita Misiura, https://starnetastro.com/, aparte y con su propia licencia, como
  ASTAP; `[General] ruta_starnet`), el panel «Estrellas aparte» separa el apilado en objeto y
  estrellas (una vez por foto, varios minutos; la pareja queda en `static/fotos/capas/` y sigue a
  la foto al renombrarla o borrarla) y los procesa por separado: el objeto se estira y se limpia
  sin que las estrellas estorben, con el color y los huecos del fondo suavizados a gran escala
  (`procesado.suavizar_fondo_sin_estrellas`: el moteado que aparece al quitar las estrellas), y las
  estrellas se estiran con la misma curva del detalle que les habría dado el multiescala, con su
  color y sin saturar. Se vuelven a juntar con una pantalla (screen) y el deslizador «Tamaño de las
  estrellas» (0 = sin estrellas, 100 = como salieron) se aplica al instante en el navegador, como
  el color y las curvas. Con `[Procesado] estrellas_aparte = true` se hace también al guardar un
  apilado en la galería. Medido con la Pacman real: el moteado de color del fondo a 32 px baja de
  1,06 a 0,51; StarNet2 tarda 222 s la foto completa de la Saturn-C en un PC de sobremesa solo con
  CPU (55 s a la mitad). DeepSNR, del mismo autor, se probó y se descartó (pensado para cámaras
  mono; con la Saturn-C deja manchas). Módulo `estrellas_aparte.py`, prueba
  `herramientas/probar_estrellas_aparte.py` (sin StarNet2: su licencia no permite usar sus salidas
  como datos de prueba).
- Nuevo: el tamaño de cada grupo de manchas del Sol, en kilómetros y en Tierras («134.000 km · 10,5
  Tierras», de punta a punta), en una lista de grupos del visor con sus manchas, sus coordenadas y
  su área; la lista va a la derecha de la foto si cabe y, si no, debajo, y al pasar el ratón por
  una fila se resalta su grupo. Sobre la foto queda solo el número de cada grupo: con los tamaños
  encima, los rótulos de grupos cercanos se pisaban. Sale de la zona oscura entera (umbra y penumbra) sobre la esfera, con el
  escorzo corregido, y del radio del disco (695 700 km), sin necesitar la escala de placa. Se
  recalcula al corregir el conteo a mano y se guarda con él. Por debajo de lo que emborrona el día
  (la anchura del limbo, unos 8000 km en las fotos de octubre) se da como «menos de».
- Nuevo: Número de Wolf de las fotos del Sol. En la galería, el interruptor «Anotaciones» sobre una
  foto del Sol (suelta o apilada de un vídeo) localiza el disco, aplana el oscurecimiento del limbo
  sobre el crudo lineal y cuenta las umbras (f) y los grupos (g, por separación angular sobre la
  esfera, sin necesitar saber dónde está el norte) → R = k·(10·g + f). El conteo se corrige
  tocando la foto (quitar o añadir manchas, unir o separar grupos), se guarda en el JSON de la
  foto y en la serie `datos/numero_wolf.csv`, que se ve en una gráfica con el filtro Sol. Con la
  orientación de la cámara conocida se dan las coordenadas heliográficas de cada grupo; P, B0 y
  L0 salen de las fórmulas de Meeus sin Internet. El factor k del observador va en `[Sol]
  factor_k` (`config.example.ini`). Módulo `manchas_solares.py`, capa
  `static/js/manchas_solares.js`, prueba `herramientas/probar_manchas_solares.py`. Umbrales
  afinados con 14 fotos reales del 27-sep, 10-oct y 17-oct-2026 (fotosfera local por cierre
  morfológico, umbral relativo al ruido, motas y limbo descartados): las nueve del 10-oct dan
  el mismo R. Las fotos sueltas guardan ahora el giro de Cámara en su JSON; las antiguas, sin
  él, se cuentan sobre el JPG.
- Nuevo: el disco del Sol medido sobre el crudo de cada foto (botón «Limbo y diámetro» del
  visor). El oscurecimiento del limbo en rojo, verde y azul (ley cuadrática ajustada al perfil
  radial, con el cielo restado); la temperatura de la fotosfera según la profundidad que se
  deduce de él (aproximación de Eddington-Barbier: en las fotos del 10-oct-2026 los tres colores
  dan 5380 K a profundidad óptica 0,3, el modelo gris de Eddington 5330 K); el radio, como el
  semieje mayor de la elipse ajustada al limbo, porque la refracción achata el vertical (medido
  0,40 % con el Sol a 15°, lo previsto); la distancia Tierra-Sol y el brillo del cielo junto al
  Sol (la calima, que marca la medida como poco fiable por encima del 8 %). En las 12 fotos
  reales con crudo el radio queda a ±0,15 % de la efeméride. Con el filtro Sol, la galería
  pinta el radio de cada foto sobre la curva del año (la órbita de la Tierra) y, con fotos de
  al menos cuatro meses, ajusta la excentricidad y la fecha del perihelio. Módulo
  `fotometria_solar.py` (medidas en `datos/medidas_sol.json`), gráficas
  `static/js/fotometria_solar.js`, prueba `herramientas/probar_fotometria_solar.py`.
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
- Arreglado: el dithering del apilado ya no hace que el encuadre se vaya desplazando durante la
  noche. Antes cada movimiento salía de donde había quedado el anterior y se acumulaban (del orden
  de 100 px en 300 fotos con 15 px de amplitud). Ahora cada movimiento va a un punto al azar a
  hasta `amplitud_px` del punto de partida, y cada punto queda a al menos media amplitud del
  anterior. Si otra cosa mueve la montura (un GoTo, un centrado), ese sitio pasa a ser el punto de
  partida. Se mueve con MoveAxis a velocidad baja, como el guiado por el disco, en vez de con un
  GoTo, que en la AZ GoTo se pasa unos 25′ y vuelve. Clave nueva `[Dithering] velocidad_deg_s`
  (0 = con GoTo). Módulo `dither.py`, prueba `herramientas/probar_dither.py`.
- Arreglado: la corrección por ejes del guiado por el disco ya no vuelve al GoTo cuando, tras un
  primer paso bueno, otro paso no gana nada: es el límite de precisión (el cielo corre unos 15″/s
  mientras el eje se mueve sin seguimiento), no una montura que no responde.
- Arreglado: las anotaciones de la galería, del editor y del apilado en vivo ya no fallan en
  campos muy densos de la Vía Láctea (M 17 del 8-oct: «ASTAP no ha podido resolver la foto»). Con
  la tolerancia de serie (0,007) casi ningún cuadrilátero de estrellas cuadraba con el catálogo;
  si ASTAP no resuelve, ahora se repite con `-t 0.01` y la solución solo se acepta si la escala
  cuadra con FOCALLEN y XPIXSZ (±10 %) y el centro queda a menos de 5° de donde apuntaba la
  montura. El fallo queda además en el log. Prueba `herramientas/probar_tolerancia_astap.py`.
- Arreglado: las marcas de tiempo de los vídeos SER iban una hora antes en invierno (dos en
  verano): `grabador_video.py` pasaba la hora UTC como si fuera local. La rotación de campo del
  apilado se calculaba con el Sol de una hora antes; en los tres vídeos del Sol del 25-oct el giro
  en 2 min salía de 0,34° en vez de 0,25° (un 27 % de más, hasta 0,09° de error, medio píxel en el
  limbo). Ahora cada fotograma lleva la hora UTC exacta y la cabecera la local y la UTC, como pide
  el formato (lo leen bien AutoStakkert, PIPP y SER Player). Los vídeos ya grabados se corrigen al
  leerlos con la hora de inicio de su `.json`. Prueba en `herramientas/probar_apilado_video.py`.

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
