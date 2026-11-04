# Galería pública: cómo funciona y qué poner en un hosting

ScopePower publica las fotos que marques en la galería (botón con el globo) en una dirección
pública, para enseñarlas desde el móvil con el mini PC apagado:

    https://scopepower.es/galeria/<nombre>/

Todo lo sube la app: fotos reducidas, visor, planetario. Para quien usa ScopePower no hay nada que
instalar: en Configuración > Galería pública, activar, elegir un nombre que esté libre y publicar.
El nombre queda reservado en el primer uso con una clave que la app genera y guarda sola en
`secretos.ini` (`publicar_clave`): quien llega primero se queda el nombre, y nadie más puede
escribir en su galería. Cada galería tiene una cuota de espacio en scopepower.es.

Solo salen JPG reducidos y datos de cada foto (objeto, fecha, exposición, coordenadas, nota,
autor): ni FITS, ni vídeos, ni la posición exacta del observatorio.

Uso: solo fotos propias del cielo hechas con ScopePower (cielo profundo, Sol, Luna, planetas,
cometas). Cada foto publicada lleva las coordenadas del objeto (de su JSON o de la cabecera del
FITS), salvo las del Sol y la Luna, que no las necesitan; las de tipo Paisaje no se publican. Una
foto sin coordenadas (una antigua sin FITS, o una que no sale de ScopePower) no se deja marcar: la
app lo dice al pulsar el globo. El dueño del hosting puede retirar una galería que no lo cumpla; en
el pie de cada galería hay un enlace «Informar de un problema» con su correo (si lo ha puesto en
`contacto`).

El resto de este documento es para quien quiera montar el endpoint en su propio hosting.

## Quién escribe dónde

El endpoint reparte los permisos para que nadie pueda colar código en la web de otro:

- **Un nombre registrado desde la app** (o dado de alta en `claves`) solo escribe en
  `galeria/<nombre>/`, y ahí solo `.jpg` y `.json`: fotos reducidas, la ficha de cada foto, sus
  anotaciones y el índice `galeria.json`. Cada fichero se comprueba al llegar: un `.jpg` tiene que
  ser un JPEG de verdad de hasta 1700 px de lado; un `.json` tiene que parsear y llevar solo las
  claves que escribe la app (tipo de objeto de la lista de la app, textos de hasta 200 caracteres,
  hasta 1000 fotos en el índice) y cada foto con las coordenadas del objeto (`ra` y `dec`) o de
  tipo Sol o Luna, nunca Paisaje (un freno a fotos que no salen de ScopePower, no un filtro de
  contenido). Lo que no cuadra se rechaza con 422 y no se guarda. Nada de HTML, JS ni CSS en la
  carpeta de un nombre.
- **Lo compartido** (`galeria/_comun/<versión>/`: motor del planetario, traducciones, JS y CSS del
  visor y las páginas genéricas en `publico/paginas/`) y el `.htaccess` de `galeria/` solo los sube
  o borra el dueño del hosting: las claves de `claves` de `publicar_config.php` y los nombres de
  `duenos` (nombres registrados desde la app, con su clave cifrada en `claves.php`, que el dueño
  señala como suyos sin copiar la clave). Lo sube su propio ScopePower al sincronizar. A cualquier
  otro nombre el endpoint le contesta 403 y la app lo da por bueno: lo anota una vez en el log y no
  lo vuelve a intentar.
- **Las páginas de cada galería** (`index.html`, `planetario.html` y sus versiones en `en/`) las
  coloca el endpoint: al registrar el nombre y cada vez que llega su `galeria.json` copia las de la
  versión más alta que haya en `_comun/<versión>/publico/paginas/`. Son iguales para todos (el
  título, el autor y el contacto los pone el JS leyendo `galeria.json`), ningún nombre puede subirlas,
  listarlas ni borrarlas, y un ScopePower más nuevo que el hosting usa las páginas que haya. Un
  ScopePower anterior a 1.2.3 sube sus propias páginas con esos nombres: el endpoint le contesta que
  sí (`"ignorado": true`), no guarda nada y coloca las genéricas, para que su cola no se atasque.

## Instalación en un hosting propio (una vez)

Vale cualquier hosting con PHP 7.4 o posterior y Apache o LiteSpeed con `.htaccess`, como los
cPanel compartidos.

1. Crea la carpeta `public_html/scopepower/` y sube ahí `publicar.php` y `endpoint.htaccess`
   (renombrado a `.htaccess`).
2. Copia `publicar_config.example.php` como `publicar_config.php` en la misma carpeta y ajusta:
   - `registro_abierto`: `true` para que cualquier ScopePower reserve un nombre libre desde la app
     (como scopepower.es); `false` para dar de alta los nombres a mano en `claves`.
   - `cuota_nombre_mb`, `cuota_total_mb`, `max_nombres`, `registros_por_dia`: frenos para que un
     hosting abierto no se llene. 300 MB por nombre son unas 600 fotos.
   - `claves`: nombres dados de alta a mano, con su clave en claro. La misma clave va en
     `secretos.ini` de ese ScopePower (`publicar_clave`) o en Configuración > Galería pública >
     «Clave del endpoint». Son claves del dueño: pueden subir lo común (`_comun/`) y el
     `.htaccess`. Con registro abierto puede quedar vacío si pones tu nombre en `duenos`.
   - `duenos`: nombres registrados desde la app que son el dueño (lo normal con registro abierto:
     el nombre de tu propio ScopePower). Hace falta al menos un dueño, en `claves` o aquí, para que
     el hosting reciba el visor y el motor del planetario.
   - `contacto`: tu correo, para el enlace «Informar de un problema» del pie de las galerías.
     Vacío: no sale.
3. En el ScopePower que vaya a publicar ahí: Configuración > Galería pública, dirección del
   hosting (`https://tu-dominio`), nombre, «Probar conexión» (reserva el nombre si el registro
   está abierto) y a publicar.

La carpeta `public_html/galeria/` la crea el endpoint la primera vez. Dentro quedan `.htaccess`
(lo sube el ScopePower del dueño; impide listar carpetas y ejecutar nada), `<nombre>/` por cada
galería y `_comun/<versión>/` con el visor, el motor del planetario y las traducciones, compartido
por todos. Los nombres registrados desde la app quedan en `scopepower/claves.php` con la clave
cifrada.

Al actualizar ScopePower, el dueño sincroniza su galería y sube `_comun/<versión nueva>/`; desde
ese momento el endpoint coloca las páginas nuevas en cada galería con su siguiente `galeria.json`.
Las versiones viejas de `_comun/` se pueden borrar a mano cuando ninguna galería las use.

## Si algo no va

- **401 / «Clave rechazada»**: la clave de `secretos.ini` no es la de ese nombre (otra
  instalación lo reservó antes, o se cambió la clave). Elige otro nombre o, en un hosting
  propio, revisa `claves` en `publicar_config.php`.
- **409 / «Ese nombre ya está en uso»**: otro ScopePower lo reservó antes, o es un nombre
  reservado (`admin`, `api`, `scopepower`...). Elige otro en Configuración.
- **507 / «cuota agotada»**: la galería pasa de `cuota_nombre_mb`; retira fotos con el globo.
  También sale si el hosting no tiene sitio para una galería nueva (`cuota_total_mb`).
- **429 / «demasiadas galerías nuevas hoy»**: freno de `registros_por_dia`; prueba mañana.
- **403 / «El hosting bloquea la petición»**: el cortafuegos del hosting corta la petición
  (algunos rechazan agentes desconocidos; ScopePower se presenta como `ScopePower/<versión>`).
  Mira el log del cortafuegos en cPanel (ModSecurity) y añade una excepción para
  `/scopepower/publicar.php`.
- **403 / «Solo el dueño del hosting puede subir…»** en el log: normal para quien no es el dueño;
  lo común y las páginas las pone el hosting. Si la galería sale sin visor (solo `galeria.json`),
  es que el dueño aún no ha subido `_comun/` con su ScopePower: que sincronice el suyo.
- **422 / «no es un JPEG», «tiene una clave que no se publica»…**: el fichero no es lo que la app
  escribe (una versión de ScopePower más nueva que el endpoint, o alguien probando a mano). La app
  lo salta y sigue con el resto; actualiza `publicar.php` en el hosting.
- **422 / «cada foto debe llevar coordenadas o ser del Sol o la Luna»**: una foto marcada sin
  coordenadas (a mano en su JSON, o con una app anterior a este freno). La app actual no la encola
  y lo anota en el log; retírala de la galería pública o dale coordenadas (FITS con `OBJCTRA` y
  `OBJCTDEC`).
- **404 / «No existe el endpoint»**: falta `publicar.php` en `public_html/scopepower/`.
- **413**: algún fichero pasa de `max_bytes` de `publicar_config.php` (8 MB de serie). Los
  ficheros grandes viajan en trozos de 1 MB, así que el límite por envío del hosting
  (`upload_max_filesize`, 2 MB de serie) no estorba.
- **500 nada más instalar y `error_log` con «syntax error, unexpected '['»**: el dominio corre un
  PHP viejo (5.x). Elige PHP 8 en cPanel (Select PHP Version o MultiPHP Manager) o, solo para
  esta carpeta, quita la almohadilla de la línea `AddHandler` de `endpoint.htaccess` (la de
  cPanel con LiteSpeed; con Apache suele ser `application/x-httpd-ea-php82`). Un `version.php`
  con `<?php echo PHP_VERSION;` dice qué versión corre (bórralo después).
- **500 nada más instalar**: falta `publicar_config.php` o PHP no puede crear
  `public_html/galeria/` (permisos de la carpeta).
- **La galería da 500 o 403 al abrirla**: el hosting no admite alguna directiva del `.htaccess`
  de `galeria/`. Quita del fichero `hosting/galeria.htaccess` el bloque que sobre y vuelve a
  sincronizar; la app lo sube de nuevo.
- **Se ve la galería vieja**: el índice se cachea cinco minutos en el navegador.

## Retirar una galería

Si una galería no cumple el uso (fotos que no son del cielo o no son suyas), el dueño del hosting
la retira: borra `public_html/galeria/<nombre>/` y la entrada de ese nombre en
`scopepower/claves.php` (o en `claves` de `publicar_config.php`). El nombre vuelve a quedar libre
(con registro abierto, el mismo ScopePower lo reservaría otra vez al sincronizar). Para que no
pueda volver a registrarlo, añade el nombre a `nombres_reservados` de `publicar_config.php`.
