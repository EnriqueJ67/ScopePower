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

El resto de este documento es para quien quiera montar el endpoint en su propio hosting.

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
     «Clave del endpoint». Con registro abierto puede quedar vacío.
3. En el ScopePower que vaya a publicar ahí: Configuración > Galería pública, dirección del
   hosting (`https://tu-dominio`), nombre, «Probar conexión» (reserva el nombre si el registro
   está abierto) y a publicar.

La carpeta `public_html/galeria/` la crea el endpoint la primera vez. Dentro quedan `.htaccess`
(lo sube la app; impide listar carpetas y ejecutar nada), `<nombre>/` por cada galería y
`_comun/<versión>/` con el motor del planetario y las traducciones, compartido por todos. Los
nombres registrados desde la app quedan en `scopepower/claves.php` con la clave cifrada.

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

Borra `public_html/galeria/<nombre>/` y la entrada de ese nombre en `scopepower/claves.php` (o en
`claves` de `publicar_config.php`). El nombre vuelve a quedar libre.
