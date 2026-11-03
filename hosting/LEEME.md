# Galería pública: qué poner en el hosting

ScopePower puede publicar las fotos que marques en la galería (botón «Publicar») en tu propio
hosting, para enseñarlas desde el móvil con el mini PC apagado:

    https://tu-dominio/galeria/<nombre>/

Todo lo sube la app (fotos reducidas, visor, planetario). En el hosting solo hay que colocar
una vez el endpoint que las recibe. Vale cualquier hosting con PHP (7.4 o posterior) y Apache
con `.htaccess`, como los cPanel compartidos.

## Instalación (una vez)

1. Crea la carpeta `public_html/scopepower/` y sube ahí `publicar.php`.
2. Copia `publicar_config.example.php` como `publicar_config.php` (en la misma carpeta) y
   pon en `claves` tu nombre y una clave larga y aleatoria:

       python -c "import secrets; print(secrets.token_urlsafe(32))"

   El nombre es el de `[Publicar] nombre` en `config.ini` o, si está vacío, el de
   `[Observatorio] autor` en minúsculas, sin tildes y con guiones («Enrique Jiménez» →
   `enrique-jimenez`). Configuración lo enseña.
3. En ScopePower, `config.ini`:

       [Publicar]
       activado = true
       url_base = https://tu-dominio

   y en `secretos.ini`, la misma clave: `publicar_clave = ...`. (Desde Configuración > Galería
   pública se rellena todo esto sin editar ficheros.)
4. Pulsa «Probar conexión» en Configuración, o en una consola:

       python -c "import publicar; print(publicar.probar())"

5. Marca una foto con «Publicar» en la galería y abre la dirección desde el móvil.

La carpeta `public_html/galeria/` la crea el endpoint la primera vez. Dentro quedan
`.htaccess` (lo sube la app; impide listar carpetas y ejecutar nada), `<nombre>/` con tus fotos
y `_comun/<versión>/` con el motor del planetario y las traducciones, compartido por todos los
nombres que publiquen en el mismo hosting.

## Si algo no va

- **401 / «Clave rechazada»**: la clave de `secretos.ini` no es la de ese nombre en
  `publicar_config.php`, o ese nombre no está dado de alta. Un nombre solo lo puede usar quien
  tiene su clave.
- **413**: el hosting limita las subidas por debajo de 4 MB (el catálogo del planetario pesa
  3,5 MB). En cPanel: Select PHP Version > Options > `upload_max_filesize` y `post_max_size`.
- **500 nada más instalar**: falta `publicar_config.php` o PHP no puede crear
  `public_html/galeria/` (permisos de la carpeta).
- **La galería da 500 o 403 al abrirla**: el hosting no admite alguna directiva del `.htaccess`.
  Quita del fichero `hosting/galeria.htaccess` el bloque que sobre (lo normal es el de
  `mod_headers`) y vuelve a sincronizar; la app lo sube de nuevo.
- **Se ve la galería vieja**: el índice se cachea cinco minutos en el navegador.

## Varios nombres en el mismo hosting

Cada propietario es una entrada más en `claves`. Nada se registra solo: el alta la hace el
dueño del hosting, y así nadie puede ocupar el nombre de otro ni llenar el disco.
